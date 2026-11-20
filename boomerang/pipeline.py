"""Pipeline: plan → (human) → approve → execute, and the round trip. Owner C, tasks C4 and C6.

Spec: docs/15 §5 (gate L2, steps, run directory) and §7 (round trip).

The approval gate is three calls (L2), so a UI that re-runs its script on every click never has to keep
a pipeline "waiting": ``plan()`` stops at ``awaiting_approval``; ``approve()`` records the sha256 of what
the human saw (``pam.json`` + ``assessment.json``); ``execute()`` refuses unless both still match.

Every ``execute()`` step runs inside ``_stage(stage, step)``: start/end events with timings, and any
``BaseException`` (Ctrl-C and a killed UI run included) → ``status="error"`` + ``run.json`` saved +
re-raised. The engine never imports the scanned app (L17) and never prints: events only.

Fixture fallback (L9, ``BOOMERANG_ALLOW_FIXTURES=1``): a stage whose owner hasn't built it yet (it raises
``NotBuilt``, and only stubs do) loads ``fixtures/<name>.json`` instead, emits a ⚠️ warning and is listed
in ``summary.fixtures_used``. Refused for the ``golden`` run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import TypeAdapter
from rich.console import Console

from boomerang import (
    assess,
    datamover,
    delock,
    fixer,
    lab,
    lockin,
    lost,
    provisioner_aws,
    redact,
    report,
    scanner,
    verifier,
)
from boomerang._stub import NotBuilt
from boomerang.config import get_settings
from boomerang.events import Emitter, console_sink, jsonl_sink
from boomerang.fsutil import atomic_write_text, copy_ignore, hash_tree, write_text_lf
from boomerang.generators import leaseweb as leaseweb_gen
from boomerang.models import (
    AppModel,
    Approval,
    Assessment,
    Check,
    Component,
    DataMove,
    Event,
    GeneratedFile,
    LostItem,
    RewriteRecord,
    RunSummary,
    Stage,
    TestReport,
    Verification,
)
from boomerang.scanner import static as scanner_static

UISink = Callable[[Event], None]

GOLDEN = "golden"
SEED_ORDERS = 25
COMPOSE_PROJECT = "bmr-verify"          # docs/03 §3.8: must match the -p that verifier.verify (A) boots with
ACTIVE_STACK = ".active_stack"          # runs/.active_stack: run id whose Leaseweb stack is (being) booted
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UI_LOG = re.compile(r"^ui-[\w-]+\.log$")         # the UI's subprocess logs in runs/<id>/ (ui/runner.py, D7)

# Fixture fallback (L9): first existing file wins. Proposed names; B1 owns fixtures/.
FIXTURES: dict[str, tuple[str, ...]] = {
    "pam": ("pam.aws.json", "pam.json"),
    "assessment": ("assessment.json",),
    "baseline": ("baseline.json",),
    "verification": ("verification.json",),
    "data": ("data.aws-to-leaseweb.json", "datamove.json"),
    "data_back": ("data.leaseweb-to-aws.json",),
    "lost": ("lost.json",),
}
# Plan-time steps: their fixture use survives a re-run of execute().
PLAN_STEPS = ("scan", "assess", "lock-in before")
# AWS IaC stays in original/ but never ships in the Leaseweb deliverable (nor counts as residual lock-in).
IAC_ROOT_NAMES = {"infra", "terraform", ".terraform", "serverless.yml", "serverless.yaml", "template.yaml",
                  "template.yml", "samconfig.toml", "cdk.json", "cdk.out", ".aws-sam", ".serverless"}
IAC_PATTERNS = ("*.tf", "*.tfvars", "*.tfstate", "*.tfstate.*", ".terraform.lock.hcl")
# Files that belong to one execute() attempt; a re-run deletes them first.
ATTEMPT_DIRS = ("leaseweb", "diffs", "verify", "data")
ATTEMPT_FILES = ("delock.json", "assessment.final.json", "baseline.json", "tests.sha256", "lost.json", "report.md")
PROTECTED_GENERATED = (("app", "tests"), ("app", "sovereign_sdk"))

# Round trip (docs/15 §7)
LEASEWEB_API = "http://127.0.0.1:8080"   # the api container's published port (docs/14), 127.0.0.1 not localhost
HTTP_TIMEOUT_S = 30
SUBPROCESS_TIMEOUT_S = 180
ROUNDTRIP_ATTEMPT = 99                   # TestReport.attempt of the original tests after the copy back
ROUNDTRIP_PRICE_CENTS = 1234
# L8: the only engine env vars a subprocess running app code inherits (no API keys, no AWS profile)
ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "LC_ALL")

_MISSING: Any = object()


class PipelineError(Exception):
    """A user-facing stop with a message that says what to do (the CLI exits 3)."""


class NotApproved(PipelineError):
    """`approval.json` is missing, or `pam.json`/`assessment.json` changed after approval."""


class RunExists(PipelineError):
    """`plan()` on an existing run id without ``force``."""


def new_run_id() -> str:
    """``YYYYMMDD-HHMMSS-<4 hex>``"""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"


@dataclass
class RunContext:
    run_id: str
    root: Path               # runs/<run_id>/
    source_dir: Path
    summary: RunSummary
    emitter: Emitter
    stage: Stage = "scan"    # current stage, for events emitted by helpers (fixture warnings)

    @property
    def original(self) -> Path: return self.root / "original"
    @property
    def leaseweb(self) -> Path: return self.root / "leaseweb"
    @property
    def app(self) -> Path: return self.leaseweb / "app"
    @property
    def diffs(self) -> Path: return self.root / "diffs"
    @property
    def verify(self) -> Path: return self.root / "verify"
    @property
    def data(self) -> Path: return self.root / "data"

    def save(self) -> None:
        """`run.json`, atomic (retried on Windows while the UI has it open)."""
        atomic_write_text(self.root / "run.json", self.summary.model_dump_json(indent=2))

    def emit(self, message: str, level: str = "info", **data: Any) -> Event:
        return self.emitter.emit(self.stage, message, level, **data)  # type: ignore[arg-type]


# ---------- runs on disk ----------

def run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise PipelineError(f"bad run id {run_id!r}: letters, digits, '.', '_' and '-' only (max 64)")
    return get_settings().runs_dir / run_id


def load_run(run_id: str) -> RunSummary:
    path = run_dir(run_id) / "run.json"
    if not path.is_file():
        raise FileNotFoundError(f"no run {run_id!r} (looked for {path})")
    return RunSummary.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs() -> list[RunSummary]:
    """Every readable `runs/*/run.json`, newest first. Unreadable ones are skipped."""
    runs_dir = get_settings().runs_dir
    out: list[RunSummary] = []
    for path in runs_dir.glob("*/run.json") if runs_dir.is_dir() else []:
        try:
            out.append(RunSummary.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: s.started_at, reverse=True)


def plan_sha256(root: Path) -> str:
    """sha256 over `pam.json` + `assessment.json` exactly as they are on disk: what the human approves."""
    h = hashlib.sha256()
    for name in ("pam.json", "assessment.json"):
        path = root / name
        if not path.is_file():
            raise NotApproved(f"{name} is missing in {root}: run `boomerang plan` first")
        h.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def active_stack() -> str | None:
    path = get_settings().runs_dir / ACTIVE_STACK
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _set_active_stack(run_id: str | None) -> None:
    path = get_settings().runs_dir / ACTIVE_STACK
    if run_id is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_text(path, run_id + "\n")


# ---------- the gate: plan → approve → execute ----------

def plan(source_dir: Path, *, run_id: str | None = None, force: bool = False,
         emit_extra: UISink | None = None) -> RunContext:
    """Copy → scan → assess → lock-in → status `awaiting_approval`. Writes nothing in `leaseweb/` (S6)."""
    settings = get_settings()
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"no app folder {source}")
    runs_dir = settings.runs_dir.resolve()
    if runs_dir == source or source in runs_dir.parents:
        raise PipelineError(f"the runs folder {runs_dir} is inside the app {source}: point at the app folder itself")
    run_id = run_id or new_run_id()
    root = run_dir(run_id)
    notes: list[str] = []
    if (root / "run.json").exists():
        if not force:
            raise RunExists(f"run {run_id!r} already exists: use --force to replace it")
        try:
            cleanup(run_id)
        except PipelineError as e:
            notes.append(f"could not stop the old stack of {run_id}: {e}")
        _rmtree(root)
    elif root.exists():
        # No run.json: the UI made the folder for its `ui-<step>.log` (open, it is our stdout) before
        # starting `boomerang plan --run-id <id>` (L2). Keep those logs; clear anything else.
        for child in root.iterdir():
            if child.is_dir():
                _rmtree(child)
            elif not UI_LOG.match(child.name):
                child.unlink(missing_ok=True)
    root.mkdir(parents=True, exist_ok=True)

    summary = RunSummary(run_id=run_id, source_dir=str(source), status="running", started_at=time.time(),
                         out_dir=str(root), llm=settings.llm_badge)
    ctx = RunContext(run_id, root, source, summary, _emitter(run_id, root, emit_extra))
    ctx.save()
    ctx.emitter.emit("scan", f"plan: {source.name} → run {run_id}", step="plan", source=str(source))
    for note in notes:
        ctx.emitter.emit("scan", note, "warning", step="plan")

    with _stage(ctx, "scan", "copy source"):
        _copy_source(ctx, source)
    with _stage(ctx, "scan", "scan"):
        pam = _call(ctx, "scan", scanner.scan, ctx.original, ctx.emitter, fixture="pam", model=AppModel)
        atomic_write_text(root / "pam.json", pam.model_dump_json(indent=2))
    with _stage(ctx, "assess", "assess"):
        assessment = _call(ctx, "assess", assess.assess, pam, ctx.emitter, fixture="assessment", model=Assessment)
        atomic_write_text(root / "assessment.json", assessment.model_dump_json(indent=2))
        summary.assessment = assessment
    with _stage(ctx, "assess", "lock-in before"):
        summary.lock_in_before = _call(ctx, "lock-in before", lockin.score, pam, default=None)

    summary.status = "awaiting_approval"
    ctx.save()
    reds = [f for f in assessment.findings if f.light == "red"]
    ctx.emitter.emit("approve", f"plan ready: {report.light_counts(assessment)} · lock-in "
                     f"{report.score(summary.lock_in_before)} · waiting for approval", "success", step="plan",
                     **report.light_numbers(assessment), lock_in_before=summary.lock_in_before)
    for f in reds:
        ctx.emitter.emit("approve", f"will NOT be migrated: {f.component_id} · {f.feature}: {f.what_changes}",
                         "warning", step="plan", component_id=f.component_id, feature=f.feature)
    return ctx


def approve(run_id: str, *, approved_by: str = "cli", emit_extra: UISink | None = None) -> Approval:
    """Record who approved and the sha256 of the plan they saw (`approval.json`)."""
    ctx = _load(run_id, emit_extra)
    if ctx.summary.status == "running":
        raise PipelineError(f"run {run_id} is running; approve it after it stops")
    approval = Approval(approved_by=approved_by, ts=time.time(), plan_sha256=plan_sha256(ctx.root))
    atomic_write_text(ctx.root / "approval.json", approval.model_dump_json(indent=2))
    ctx.emitter.emit("approve", f"plan approved by {approved_by} · sha256 {approval.plan_sha256[:12]}", "success",
                     step="approve", approved_by=approved_by, plan_sha256=approval.plan_sha256)
    return approval


def execute(run_id: str, *, emit_extra: UISink | None = None) -> RunSummary:
    """Run an approved plan (docs/15 §5.2). This is what the UI starts as a subprocess (L2)."""
    ctx = _load(run_id, emit_extra)
    approval = _check_gate(ctx)
    if not ctx.original.is_dir():
        raise PipelineError(f"{ctx.original} is missing: plan the run again")
    pam = AppModel.model_validate_json((ctx.root / "pam.json").read_text(encoding="utf-8"))
    approved = Assessment.model_validate_json((ctx.root / "assessment.json").read_text(encoding="utf-8"))

    ctx.summary.status = "running"
    ctx.summary.finished_at = None
    ctx.summary.llm = get_settings().llm_badge
    ctx.save()
    try:
        with _stage(ctx, "approve", "gate"):
            ctx.emit(f"approved by {approval.approved_by}; pam.json + assessment.json unchanged since approval",
                     "success", step="gate", plan_sha256=approval.plan_sha256)
            _reset_previous(ctx, approved)
        with _stage(ctx, "verify", "baseline"):
            _baseline_step(ctx, pam)
        with _stage(ctx, "generate", "prepare"):
            _prepare(ctx)
        with _stage(ctx, "delock", "delock"):
            ctx.summary.rewrites = delock.delock_detailed(ctx.app, pam, approved, ctx.emitter, ctx.diffs,
                                                          out_dir=ctx.root)
            final = ctx.root / "assessment.final.json"
            if final.is_file():
                ctx.summary.assessment = Assessment.model_validate_json(final.read_text(encoding="utf-8"))
            ctx.save()
        with _stage(ctx, "generate", "generate"):
            _generate(ctx, pam)
        with _stage(ctx, "assess", "lock-in after"):
            _lock_in_after(ctx)
        with _stage(ctx, "verify", "verify"):
            _verify_loop(ctx, pam)
        with _stage(ctx, "verify", "test integrity"):
            _test_integrity(ctx)
        with _stage(ctx, "data", "data"):
            _data_step(ctx, pam)
        with _stage(ctx, "report", "report"):
            _finish(ctx)
            report.write(ctx)
            ctx.save()
    except BaseException:
        _try_report(ctx)
        raise
    return ctx.summary


def migrate(source_dir: Path, *, yes: bool = False, run_id: str | None = None, force: bool = False,
            emit_extra: UISink | None = None) -> RunSummary:
    """``plan()`` → ``approve()`` (only when ``yes``) → ``execute()``. The interactive prompt is in cli.py:
    without ``yes`` this returns the summary in ``awaiting_approval``."""
    ctx = plan(source_dir, run_id=run_id, force=force, emit_extra=emit_extra)
    if not (yes or get_settings().auto_approve):
        return ctx.summary
    approve(ctx.run_id, approved_by="auto", emit_extra=emit_extra)
    return execute(ctx.run_id, emit_extra=emit_extra)


def roundtrip(run_id: str, *, emit_extra: UISink | None = None) -> RunSummary:
    """AWS → Leaseweb → AWS (docs/15 §7): one new order written on Leaseweb comes back to a fresh AWS, the
    original code's tests pass there, and ``lost.json`` says what didn't survive the trip."""
    ctx = _load(run_id, emit_extra)
    ctx.stage = "rollback"
    pam = AppModel.model_validate_json((ctx.root / "pam.json").read_text(encoding="utf-8"))
    before, has_api = _roundtrip_preconditions(ctx)

    s = ctx.summary
    s.roundtrip, s.roundtrip_checks, s.roundtrip_tests, s.lost = "error", [], None, []
    s.data_moves = [before]                      # data/aws-to-leaseweb.json is the source of truth
    ctx.save()
    ctx.emit(f"round trip {run_id}: AWS → Leaseweb → fresh AWS", step="round trip", api=has_api)
    try:
        with _stage(ctx, "rollback", "freeze worker", field="roundtrip"):
            _call(ctx, "stop worker", verifier.stop_service, ctx.leaseweb, "worker", default=None)
        try:
            with _stage(ctx, "rollback", "write on Leaseweb", field="roundtrip"):
                order = _write_order(ctx, pam, has_api)
            with _stage(ctx, "rollback", "fresh AWS", field="roundtrip"):
                lab.reset()
                provisioner_aws.provision(pam, ctx.emitter)
                ctx.emit("fresh (emulated) AWS: resources provisioned, no data", step="fresh AWS")
            with _stage(ctx, "data", "copy back", field="roundtrip"):
                back = _copy_back(ctx, pam)
            with _stage(ctx, "rollback", "checks", field="roundtrip"):
                s.roundtrip_checks = _roundtrip_checks(ctx, pam, before, back, order)
                ctx.save()
            with _stage(ctx, "rollback", "original tests on AWS", field="roundtrip"):
                s.roundtrip_tests = _roundtrip_tests(ctx)
            with _stage(ctx, "rollback", "what got lost", field="roundtrip"):
                _what_got_lost(ctx, pam)
        finally:
            _restart_worker(ctx)
        with _stage(ctx, "report", "report", field="roundtrip"):
            ok = (back.ok and all(c.ok for c in s.roundtrip_checks)
                  and bool(s.roundtrip_tests and s.roundtrip_tests.ok))
            s.roundtrip = "passed" if ok else "failed"
            ctx.save()
            failed = [c.name for c in s.roundtrip_checks if not c.ok]
            if not (s.roundtrip_tests and s.roundtrip_tests.ok):
                failed.append(f"original tests on AWS {report.tests_ratio(s.roundtrip_tests)}")
            if ok:
                ctx.emit(f"round trip passed: counts match (+{order.total_writes} order written on Leaseweb), "
                         f"original tests {report.tests_ratio(s.roundtrip_tests)} on AWS", "success", step="report")
            else:
                ctx.emit(f"round trip failed: {'; '.join(failed) or 'copy back mismatched'}", "error",
                         step="report", failed=failed)
            report.write(ctx)
    except BaseException:
        _try_report(ctx)
        raise
    return s


def cleanup(run_id: str) -> None:
    """`compose down -v` for this run's stack, if it is the active one (one stack at a time: fixed ports)."""
    if active_stack() != run_id:
        return
    compose = run_dir(run_id) / "leaseweb" / "docker-compose.yml"
    if compose.is_file():
        docker = shutil.which("docker")
        if docker is None:
            raise PipelineError("docker is not on PATH: cannot stop the Leaseweb stack")
        cmd = [docker, "compose", "-p", COMPOSE_PROJECT, "-f", str(compose), "down", "-v", "--remove-orphans"]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise PipelineError(f"docker compose down failed: {e}") from e
        if done.returncode != 0:
            raise PipelineError(f"docker compose down failed: {(done.stderr or done.stdout).strip()[-500:]}")
    _set_active_stack(None)


# ---------- execute() steps ----------

def _check_gate(ctx: RunContext) -> Approval:
    path = ctx.root / "approval.json"
    try:
        if not path.is_file():
            raise NotApproved(f"run {ctx.run_id} is not approved: `boomerang approve {ctx.run_id}` (or the UI button)")
        approval = Approval.model_validate_json(path.read_text(encoding="utf-8"))
        if approval.plan_sha256 != plan_sha256(ctx.root):
            raise NotApproved(f"pam.json or assessment.json changed after approval: review the plan and approve "
                              f"run {ctx.run_id} again")
    except NotApproved as e:
        ctx.emitter.emit("approve", str(e), "error", step="gate")
        raise
    return approval


def _reset_previous(ctx: RunContext, approved: Assessment) -> None:
    """A second execute() on the same run starts clean: stop its stack, delete the last attempt's outputs."""
    previous = [d for d in ATTEMPT_DIRS if (ctx.root / d).exists()]
    if active_stack() == ctx.run_id:
        try:
            cleanup(ctx.run_id)
        except PipelineError as e:
            ctx.emit(f"{e}; the next verify replaces the stack anyway", "warning", step="gate")
    for name in ATTEMPT_DIRS:
        _rmtree(ctx.root / name)
    for name in ATTEMPT_FILES:
        (ctx.root / name).unlink(missing_ok=True)
    s = ctx.summary
    s.assessment = approved
    s.baseline, s.rewrites, s.verifications, s.data_moves, s.lost = None, [], [], [], []
    s.tests_unchanged, s.lock_in_after = None, None
    s.roundtrip, s.roundtrip_checks, s.roundtrip_tests = "not_run", [], None
    s.fixtures_used = [f for f in s.fixtures_used if f in PLAN_STEPS]
    if previous:
        ctx.emit(f"cleaned the previous attempt ({', '.join(previous)})", step="gate")
    ctx.save()


def _baseline_step(ctx: RunContext, pam: AppModel) -> None:
    """L5: original tests on a fresh AWS (moto), then a second fresh AWS with the seed for the data move."""
    baseline: TestReport | None = _call(ctx, "baseline", _baseline, ctx, pam, fixture="baseline",
                                        model=TestReport, default=None)
    ctx.summary.baseline = baseline
    if baseline is None:
        ctx.emit("baseline skipped: no original test results on AWS to compare with", "warning", step="baseline")
    else:
        atomic_write_text(ctx.root / "baseline.json", baseline.model_dump_json(indent=2))
        if baseline.ok:
            ctx.emit(f"baseline: {report.tests_ratio(baseline)} original tests pass on AWS", "success",
                     step="baseline", passed=baseline.passed, total=baseline.total)
        else:
            ctx.emit(f"baseline: the original app fails its own tests on AWS ({report.tests_ratio(baseline)}); "
                     "the migration can't prove more than that", "warning", step="baseline",
                     passed=baseline.passed, total=baseline.total)
    ctx.save()


def _baseline(ctx: RunContext, pam: AppModel) -> TestReport:
    if not lab.status():
        raise PipelineError("the lab (fake AWS) is down: run `boomerang lab up`")
    lab.reset()
    provisioner_aws.provision(pam, ctx.emitter)
    result = verifier.run_tests(ctx.original, verifier.aws_env(), "aws", 0, ctx.emitter)
    lab.reset()                                  # the tests drained the queue: seed on a fresh state (L5)
    provisioner_aws.provision(pam, ctx.emitter)
    counts = provisioner_aws.seed(pam, SEED_ORDERS, ctx.emitter)
    ctx.emit(f"seeded AWS: {', '.join(f'{k} {v}' for k, v in (counts or {}).items()) or 'done'}", "success",
             step="baseline", seeded=counts)
    return result


def _prepare(ctx: RunContext) -> None:
    """original/ → leaseweb/app/ (no AWS IaC), and the sha256 of every original test file (L12)."""
    original = ctx.original
    left_out: list[str] = []
    base = copy_ignore(IAC_PATTERNS)

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = base(directory, names)
        if Path(directory) == original:
            skipped |= {n for n in names if n in IAC_ROOT_NAMES}
        left_out.extend(Path(directory, n).relative_to(original).as_posix() for n in sorted(skipped)
                        if n in IAC_ROOT_NAMES or any(PurePosixPath(n).match(p) for p in IAC_PATTERNS))
        return skipped

    shutil.copytree(original, ctx.app, ignore=ignore)
    ctx.emit("copied original/ → leaseweb/app/" + (f" (AWS IaC left out: {', '.join(left_out)})" if left_out else ""),
             step="prepare", left_out=left_out)

    tests = original / "tests"
    if not tests.is_dir():
        ctx.emit("no tests/ in the app: the migration can't be verified", "warning", step="prepare")
        return
    hashes = hash_tree(tests)
    atomic_write_text(ctx.root / "tests.sha256", "".join(f"{h}  {p}\n" for p, h in sorted(hashes.items())))
    ctx.emit(f"hashed {len(hashes)} original test file(s) → tests.sha256", step="prepare", files=len(hashes))


def _generate(ctx: RunContext, pam: AppModel) -> None:
    """Leaseweb files from B's generator (LF, contained in leaseweb/), then sovereign_sdk/ into app/ (L7)."""
    files = _call(ctx, "generate", _render, pam, default=None)
    if files is None:
        ctx.emit("no Leaseweb files generated (generator not built)", "warning", step="generate")
    else:
        for f in files:
            _write_generated(ctx, f)
        secret_files = [f.path for f in files if f.secret]
        ctx.emit(f"generated {len(files)} Leaseweb file(s)" + (f"; {', '.join(secret_files)} chmod 600, never "
                 "sent to the AI" if secret_files else ""), "success", step="generate",
                 files=sorted(f.path for f in files))
    _copy_sdk(ctx)
    redact.secret_values.cache_clear()           # the new passwords are redacted from every prompt from now on


def _render(pam: AppModel) -> list[GeneratedFile]:
    return leaseweb_gen.render(pam, leaseweb_gen.make_secrets(pam))


def _write_generated(ctx: RunContext, f: GeneratedFile) -> None:
    rel = f.path.replace("\\", "/")
    parts = PurePosixPath(rel).parts
    if (not parts or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ".." in parts
            or any(tuple(p.lower() for p in parts[:2]) == bad for bad in PROTECTED_GENERATED)):
        raise PipelineError(f"generator returned a path outside leaseweb/ or under app/tests|sovereign_sdk: {f.path!r}")
    target = ctx.leaseweb.joinpath(*parts)
    if not target.resolve().is_relative_to(ctx.leaseweb.resolve()):
        raise PipelineError(f"generator returned a path outside leaseweb/: {f.path!r}")
    write_text_lf(target, f.content)             # LF + UTF-8: a CRLF .sh fails in a Linux container
    if f.executable:
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if f.secret:
        target.chmod(0o600)


def _copy_sdk(ctx: RunContext) -> None:
    import sovereign_sdk

    src = Path(sovereign_sdk.__file__).parent
    dst = ctx.app / "sovereign_sdk"
    _rmtree(dst)
    dst.mkdir(parents=True)
    names = sorted(p.name for p in src.glob("*.py"))
    for name in names:
        shutil.copyfile(src / name, dst / name)
    ctx.emit(f"copied sovereign_sdk/ into app/ ({', '.join(names)})", step="generate")


def _lock_in_after(ctx: RunContext) -> None:
    """L16: static scan of the migrated app (no AI; tests/ and sovereign_sdk/ excluded)."""
    residual = _call(ctx, "lock-in after", scanner_static.scan_static, ctx.app, default=None)
    if residual is not None:
        ctx.summary.lock_in_after = _call(ctx, "lock-in after", lockin.score, residual, default=None)
    reds = sum(f.light == "red" for f in (ctx.summary.assessment.findings if ctx.summary.assessment else []))
    note = f"; {reds} 🔴 item(s) leave the score because they were not migrated, not because they were solved" \
        if reds else ""
    ctx.emit(f"lock-in {report.score(ctx.summary.lock_in_before)} → {report.score(ctx.summary.lock_in_after)}"
             f"{note}", step="lock-in after", before=ctx.summary.lock_in_before, after=ctx.summary.lock_in_after,
             red_not_migrated=reds)
    ctx.save()


def _verify_loop(ctx: RunContext, pam: AppModel) -> None:
    """L11: at most ``max_fix_attempts`` fixer rounds → at most 1 + that many verifications."""
    max_fix = get_settings().max_fix_attempts
    _set_active_stack(ctx.run_id)                # verify replaces whatever stack is up (fixed host ports)
    for attempt in range(1, max_fix + 2):
        v: Verification = _call(ctx, "verify", verifier.verify, ctx.leaseweb, attempt, ctx.emitter,
                                fixture="verification", model=Verification)
        ctx.summary.verifications.append(v)
        atomic_write_text(ctx.verify / f"attempt-{attempt}.json", v.model_dump_json(indent=2))
        ctx.save()
        tests = f" · tests {report.tests_ratio(v.tests)}" if v.tests else ""
        if v.ok:
            ctx.emit(f"attempt {attempt}: verified{tests}", "success", step="verify", attempt=attempt)
            break
        broken = [c.name for c in v.checks if not c.ok and c.name != "tests"]
        if broken:
            ctx.emit(f"attempt {attempt}: {', '.join(broken)} failed: template/boot problem (owners B/A); "
                     "the AI can't fix compose files", "error", step="verify", attempt=attempt, failed=broken)
            break
        ctx.emit(f"attempt {attempt}: original tests failing{tests}", "warning", step="verify", attempt=attempt)
        if v.tests is None:
            break
        if attempt > max_fix:
            ctx.emit(f"no fix attempts left (BOOMERANG_MAX_FIX_ATTEMPTS={max_fix})", "warning", step="verify")
            break
        new: list[RewriteRecord] = _call(ctx, "fix", fixer.fix, ctx.app, ctx.original, pam, v.tests, attempt,
                                         ctx.summary.rewrites, ctx.diffs, ctx.emitter, default=[])
        ctx.summary.rewrites.extend(new)
        _write_rewrites(ctx)
        ctx.save()
        if not any(r.accepted for r in new):
            ctx.emit("the fixer found nothing it could fix: stopping", "warning", step="verify", attempt=attempt)
            break
    if "verify" in ctx.summary.fixtures_used:
        _set_active_stack(None)                  # nothing was booted


def _test_integrity(ctx: RunContext) -> None:
    """L12: app/tests/ must be byte-identical to original/tests/ (caches excluded). A mismatch fails the run."""
    path = ctx.root / "tests.sha256"
    if not path.is_file():
        ctx.summary.tests_unchanged = None
        ctx.emit("no original tests to compare", "warning", step="test integrity")
        ctx.save()
        return
    before = _read_hashes(path)
    tests = ctx.app / "tests"
    after = hash_tree(tests) if tests.is_dir() else {}
    changed = sorted(p for p in before.keys() & after.keys() if before[p] != after[p])
    added, removed = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    ctx.summary.tests_unchanged = not (changed or added or removed)
    if ctx.summary.tests_unchanged:
        ctx.emit(f"original tests unchanged ✅ ({len(before)} file(s), sha256)", "success", step="test integrity")
    else:
        ctx.emit(f"original tests CHANGED: changed {changed}, added {added}, removed {removed}", "error",
                 step="test integrity", changed=changed, added=added, removed=removed)
    ctx.save()


def _data_step(ctx: RunContext, pam: AppModel) -> None:
    """L4: clean stack, worker stopped during the copy, then started again."""
    last = ctx.summary.verifications[-1] if ctx.summary.verifications else None
    broken = [c.name for c in last.checks if not c.ok and c.name != "tests"] if last else ["verify"]
    if broken:
        ctx.emit(f"data move skipped: the Leaseweb stack is not up ({', '.join(broken)} failed)", "warning",
                 step="data")
        return
    move: DataMove = _call(ctx, "data", _move_data, ctx, pam, fixture="data", model=DataMove)
    ctx.summary.data_moves = [move]
    atomic_write_text(ctx.data / "aws-to-leaseweb.json", move.model_dump_json(indent=2))
    for c in move.counts:
        ok = c.source_count == c.target_count and c.checksum_match is not False
        checksum = {True: "checksum ✅", False: "checksum ❌", None: "no checksum"}[c.checksum_match]
        ctx.emit(f"{c.component_id}: {c.source_count} → {c.target_count} · {checksum}", "success" if ok else "error",
                 step="data", component_id=c.component_id, source=c.source_count, target=c.target_count)
    ctx.save()


def _move_data(ctx: RunContext, pam: AppModel) -> DataMove:
    verifier.reset_data(ctx.leaseweb)            # the verify tests wrote orders: start from empty tables
    verifier.stop_service(ctx.leaseweb, "worker")
    try:
        move = datamover.move(pam, "aws->leaseweb", verifier.aws_env(), verifier.leaseweb_env(ctx.leaseweb),
                              ctx.emitter)
    except BaseException:
        with suppress(Exception):
            verifier.start_service(ctx.leaseweb, "worker")
        raise
    verifier.start_service(ctx.leaseweb, "worker")
    return move


def _finish(ctx: RunContext) -> None:
    s = ctx.summary
    last = s.verifications[-1] if s.verifications else None
    reasons = []
    if not (last and last.ok):
        reasons.append("verification failed" if last else "not verified")
    if not (s.data_moves and s.data_moves[-1].ok):
        reasons.append("data move failed" if s.data_moves else "no data moved")
    if s.tests_unchanged is not True:
        reasons.append("original tests changed" if s.tests_unchanged is False else "tests not checked")
    s.status = "failed" if reasons else "passed"
    s.finished_at = time.time()
    if s.fixtures_used:
        ctx.emit(f"⚠️ fixtures used for: {', '.join(s.fixtures_used)}: not a real migration", "warning",
                 step="report", fixtures_used=s.fixtures_used)
    if reasons:
        ctx.emit(f"run failed: {'; '.join(reasons)}", "error", step="report", reasons=reasons)
    else:
        ctx.emit("run passed: verified, original tests unchanged, data counts match", "success", step="report")


# ---------- roundtrip() steps (docs/15 §7) ----------

@dataclass
class _Order:
    order_id: str
    body: dict[str, Any]
    total_writes: int                         # orders the round trips of this stack wrote on Leaseweb


def _roundtrip_preconditions(ctx: RunContext) -> tuple[DataMove, bool]:
    """Passed run, its stack is the active one, the first data move is on disk, health 200 (when there's an
    api container). A 200 alone could come from another run's stack (fixed ports)."""
    s, run_id = ctx.summary, ctx.run_id
    again = f"re-run `boomerang execute {run_id}`"
    problem: str | None = None
    move_path = ctx.data / "aws-to-leaseweb.json"
    has_api = _has_api(ctx.leaseweb)
    if s.status != "passed":
        problem = f"run {run_id} is {s.status}: the round trip needs a passed migration; {again}"
    elif "verify" in s.fixtures_used:
        problem = f"run {run_id} verified on a fixture: the round trip needs a real Leaseweb stack; {again}"
    elif active_stack() != run_id:
        problem = (f"the Leaseweb stack that is up belongs to {active_stack() or 'no run'}, not {run_id}; "
                   f"{again} (booting again wipes the data this step needs, so it isn't done here)")
    elif not move_path.is_file():
        problem = f"data/aws-to-leaseweb.json is missing: no data was moved to Leaseweb; {again}"
    elif has_api:
        try:
            status = _http("GET", "/health").status_code
        except Exception as e:  # noqa: BLE001 - any transport error means "not up"
            status, problem = 0, f"Leaseweb api not reachable at {LEASEWEB_API} ({type(e).__name__}); {again}"
        if status and status != 200:
            problem = f"Leaseweb api health is {status}, not 200; {again}"
    if problem:
        ctx.emit(problem, "error", step="preconditions")
        raise PipelineError(problem)
    before = DataMove.model_validate_json(move_path.read_text(encoding="utf-8"))
    ctx.emit(f"preconditions ok: run passed, stack {run_id} up"
             + (", health 200" if has_api else ", no api container (CP2 cut line)"), "success",
             step="preconditions")
    return before, has_api


def _has_api(leaseweb: Path) -> bool:
    """Whether the generated compose has an ``api`` service (the CP2 cut line removes it)."""
    import yaml

    try:
        data = yaml.safe_load((leaseweb / "docker-compose.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return True                              # unknown: try HTTP; a failure says what to do
    services = data.get("services") if isinstance(data, dict) else None
    return not isinstance(services, dict) or "api" in services


def _http(method: str, path: str, **kwargs: Any) -> Any:
    import httpx

    with httpx.Client(base_url=LEASEWEB_API, timeout=HTTP_TIMEOUT_S) as client:
        return client.request(method, path, **kwargs)


def _writes_path(ctx: RunContext) -> Path:
    return ctx.data / "roundtrip-writes.json"


def _write_order(ctx: RunContext, pam: AppModel, has_api: bool) -> _Order:
    """One new order on Leaseweb through the de-locked app: HTTP to the api container, or (CP2 cut line) the
    app's TestClient in a subprocess with the Leaseweb env (L17: never imported into the engine)."""
    payload = {"customer_id": f"rt-{ctx.run_id}",
               "items": [{"sku": "BOOMERANG-RT", "qty": 1, "price_cents": ROUNDTRIP_PRICE_CENTS}]}
    if has_api:
        try:
            resp = _http("POST", "/orders", json=payload)
        except Exception as e:
            raise PipelineError(f"POST {LEASEWEB_API}/orders failed: {type(e).__name__}: {e}") from e
        status, body, how = resp.status_code, _json_body(resp), f"POST {LEASEWEB_API}/orders"
    else:
        ctx.emit("no api container (CP2 cut line): writing through the app's TestClient in a subprocess",
                 "warning", step="write on Leaseweb")
        env = verifier.leaseweb_env(ctx.leaseweb)
        status, body = _post_order_subprocess(ctx.app, env, pam.entrypoints[0] if pam.entrypoints else "", payload)
        how = "TestClient POST /orders (subprocess)"
    order_id = body.get("order_id") if isinstance(body, dict) else None
    if status != 201 or not isinstance(order_id, str | int):
        raise PipelineError(f"{how} → {status}, expected 201 with an order_id: {str(body)[:300]}")
    writes = _read_json_list(_writes_path(ctx))
    writes.append(str(order_id))
    atomic_write_text(_writes_path(ctx), json.dumps(writes, indent=2) + "\n")
    ctx.emit(f"new order {order_id} written on Leaseweb ({how} → 201)", "success", step="write on Leaseweb",
             order_id=str(order_id), customer_id=payload["customer_id"])
    return _Order(str(order_id), body, len(writes))


_ENTRYPOINT = re.compile(r"^[A-Za-z_][\w.]*(?::[A-Za-z_]\w*)?$")
_TESTCLIENT_SCRIPT = """\
import importlib, json, sys
from fastapi.testclient import TestClient
module, _, attr = sys.argv[1].partition(":")
app = getattr(importlib.import_module(module), attr or "app")
with TestClient(app) as client:
    r = client.post("/orders", json=json.loads(sys.argv[2]))
try:
    body = r.json()
except ValueError:
    body = r.text
print(json.dumps({"status": r.status_code, "body": body}))
"""


def _post_order_subprocess(app_dir: Path, env: dict[str, str], entrypoint: str,
                           payload: dict[str, Any]) -> tuple[int, Any]:
    entrypoint = entrypoint or "app.main:app"
    if not _ENTRYPOINT.match(entrypoint):
        raise PipelineError(f"entrypoint {entrypoint!r} is not 'module:attribute'")
    cmd = [sys.executable, "-c", _TESTCLIENT_SCRIPT, entrypoint, json.dumps(payload)]
    try:
        done = subprocess.run(cmd, cwd=app_dir, env=subprocess_env(env), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PipelineError(f"TestClient subprocess failed: {e}") from e
    lines = [ln for ln in (done.stdout or "").splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1])
        return int(result["status"]), result.get("body")
    except (IndexError, ValueError, KeyError, TypeError):
        tail = (done.stderr or done.stdout or "").strip()[-500:]
        raise PipelineError(f"TestClient subprocess exited {done.returncode}: {tail}") from None


def subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    """L8: an allowlist of the engine's env + the platform vars, never ``os.environ.copy()`` (no API keys)."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _copy_back(ctx: RunContext, pam: AppModel) -> DataMove:
    back: DataMove = _call(ctx, "copy back", datamover.move, pam, "leaseweb->aws",
                           verifier.leaseweb_env(ctx.leaseweb), verifier.aws_env(), ctx.emitter,
                           fixture="data_back", model=DataMove)
    atomic_write_text(ctx.data / "leaseweb-to-aws.json", back.model_dump_json(indent=2))
    ctx.summary.data_moves = [*ctx.summary.data_moves[:1], back]
    ctx.save()
    for c in back.counts:
        checksum = {True: "checksum ✅", False: "checksum ❌", None: "no checksum"}[c.checksum_match]
        ok = c.source_count == c.target_count and c.checksum_match is not False
        ctx.emit(f"{c.component_id}: Leaseweb {c.source_count} → AWS {c.target_count} · {checksum}",
                 "success" if ok else "error", step="copy back", component_id=c.component_id,
                 source=c.source_count, target=c.target_count)
    return back


def _roundtrip_checks(ctx: RunContext, pam: AppModel, before: DataMove, back: DataMove, order: _Order
                      ) -> list[Check]:
    """Counts before any test runs (the tests write and drain): stores +N round-trip orders, the queue as
    pending on Leaseweb at the copy, and the new order is in DynamoDB."""
    started = {c.component_id: c for c in before.counts}
    checks: list[Check] = [Check(name="new order written on Leaseweb", ok=True,
                                 detail=f"order {order.order_id} (customer rt-{ctx.run_id})")]
    plus = order.total_writes
    for c in back.counts:
        first = started.get(c.component_id)
        copied = c.source_count == c.target_count and c.checksum_match is not False
        checksum = {True: ", checksum ✅", False: ", checksum ❌", None: ""}[c.checksum_match]
        if c.kind == "queue":
            checks.append(Check(name=f"{c.component_id}: pending messages copied back", ok=copied,
                                detail=f"{c.source_count} pending on Leaseweb → {c.target_count} on AWS"))
        elif first is None:
            checks.append(Check(name=f"{c.component_id}: AWS before + {plus}", ok=False,
                                detail="no count from the AWS → Leaseweb move to compare with"))
        else:
            expected = first.source_count + plus
            checks.append(Check(name=f"{c.component_id}: AWS before + {plus}",
                                ok=copied and c.target_count == expected,
                                detail=f"AWS {first.source_count} → Leaseweb {c.source_count} → AWS "
                                       f"{c.target_count} (expected {expected}){checksum}"))
    checks.append(_order_exists_check(pam, order))
    for check in checks:
        ctx.emit(f"{'✅' if check.ok else '❌'} {check.name}: {check.detail}", "success" if check.ok else "error",
                 step="checks", check=check.name, ok=check.ok)
    return checks


def _order_exists_check(pam: AppModel, order: _Order) -> Check:
    name = "new order exists on AWS"
    tables = [c for c in pam.components if c.kind == "kv_table" and c.provider == "aws.dynamodb"]
    table = next((t for t in tables if t.partition_key and t.partition_key.name in order.body), None) or \
        next(iter(tables), None)
    if table is None:
        return Check(name=name, ok=False, detail="no DynamoDB table in the PAM to look in")
    pk = table.partition_key.name if table.partition_key else "order_id"
    value = order.body.get(pk, order.order_id) if isinstance(order.body, dict) else order.order_id
    try:
        found, where = _dynamodb_has(table, pk, value, verifier.aws_env())
    except Exception as e:  # noqa: BLE001 - boto3/botocore errors: the check fails, it says why
        return Check(name=name, ok=False, detail=f"lookup failed: {type(e).__name__}: {str(e)[:200]}")
    return Check(name=name, ok=found, detail=f"{pk}={value} {'found' if found else 'NOT found'} in {where}")


def _dynamodb_has(table: Component, pk: str, value: Any, env: dict[str, str]) -> tuple[bool, str]:
    """Plain boto3 against the emulated AWS (L17: never the app's code in the engine)."""
    name = env.get(table.name_env_var or "", "") or table.resource_name
    typ = table.partition_key.type if table.partition_key else "S"
    client = _dynamodb_client(env)
    key = {pk: {typ: str(value)}}
    if table.sort_key is None:
        found = "Item" in client.get_item(TableName=name, Key=key, ConsistentRead=True)
    else:
        resp = client.query(TableName=name, KeyConditionExpression="#pk = :v", ExpressionAttributeNames={"#pk": pk},
                            ExpressionAttributeValues={":v": key[pk]}, Limit=1)
        found = bool(resp.get("Items"))
    return found, f"DynamoDB table {name}"


def _dynamodb_client(env: dict[str, str]) -> Any:
    import boto3

    return boto3.client(
        "dynamodb",
        endpoint_url=env.get("AWS_ENDPOINT_URL_DYNAMODB") or env.get("AWS_ENDPOINT_URL") or "http://127.0.0.1:4566",
        region_name=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1",
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _roundtrip_tests(ctx: RunContext) -> TestReport:
    tests: TestReport = verifier.run_tests(ctx.original, verifier.aws_env(), "aws", ROUNDTRIP_ATTEMPT, ctx.emitter)
    atomic_write_text(ctx.data / "roundtrip-tests.json", tests.model_dump_json(indent=2))
    ctx.summary.roundtrip_tests = tests
    ctx.save()
    ctx.emit(f"original AWS code on the fresh AWS: {report.tests_ratio(tests)} tests pass",
             "success" if tests.ok else "error", step="original tests on AWS", passed=tests.passed, total=tests.total)
    return tests


def _what_got_lost(ctx: RunContext, pam: AppModel) -> None:
    final = ctx.root / "assessment.final.json"
    assessment = (Assessment.model_validate_json(final.read_text(encoding="utf-8")) if final.is_file()
                  else ctx.summary.assessment)
    if assessment is None:
        assessment = Assessment.model_validate_json((ctx.root / "assessment.json").read_text(encoding="utf-8"))
    items: list[LostItem] = _call(ctx, "what got lost", lost.build, pam, assessment, ctx.summary.data_moves,
                                  fixture="lost", model=list[LostItem], default=[])
    ctx.summary.lost = items
    atomic_write_text(ctx.root / "lost.json",
                      json.dumps([i.model_dump(mode="json") for i in items], indent=2, ensure_ascii=False) + "\n")
    ctx.save()
    by = {st: sum(i.status == st for i in items) for st in ("lost", "changed", "preserved")}
    ctx.emit(f"what got lost: {by['lost']} lost · {by['changed']} changed · {by['preserved']} preserved",
             "warning" if by["lost"] else "success", step="what got lost", **by)


def _restart_worker(ctx: RunContext) -> None:
    try:
        _call(ctx, "start worker", verifier.start_service, ctx.leaseweb, "worker", default=None)
    except Exception as e:  # noqa: BLE001 - never hide the round trip's own error behind this one
        ctx.emit(f"could not start the worker again: {type(e).__name__}: {e}", "warning", step="round trip")


def _json_body(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _read_json_list(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _try_report(ctx: RunContext) -> None:
    """After an error: a best-effort report.md, so the UI and the judges still see how far it got."""
    with suppress(Exception):
        report.write(ctx)


# ---------- helpers ----------

def _emitter(run_id: str, root: Path, emit_extra: UISink | None) -> Emitter:
    sinks = [jsonl_sink(root / "events.jsonl"), console_sink(Console(highlight=False))]
    if emit_extra is not None:
        sinks.append(emit_extra)
    return Emitter(run_id, sinks)


def _load(run_id: str, emit_extra: UISink | None) -> RunContext:
    summary = load_run(run_id)
    root = run_dir(run_id)
    return RunContext(run_id, root, Path(summary.source_dir), summary, _emitter(run_id, root, emit_extra))


@contextmanager
def _stage(ctx: RunContext, stage: Stage, step: str, *, field: str = "status") -> Iterator[None]:
    """Start/end events with timing; any BaseException → ``summary.<field> = "error"`` + run.json saved +
    re-raised. ``field`` is ``status`` for execute() and ``roundtrip`` for the round trip, whose failure must
    not turn a passed migration into an error."""
    ctx.stage = stage
    ctx.emitter.emit(stage, f"{step} …", step=step, phase="start")
    start = time.perf_counter()
    try:
        yield
    except BaseException as e:
        took = round(time.perf_counter() - start, 2)
        setattr(ctx.summary, field, "error")
        if field == "status":
            ctx.summary.finished_at = time.time()
        reason = "interrupted" if isinstance(e, KeyboardInterrupt | SystemExit) else f"{type(e).__name__}: {e}"
        try:
            ctx.emitter.emit(stage, f"{step} failed: {reason}", "error", step=step, phase="error",
                             duration_s=took, error=type(e).__name__)
        finally:
            ctx.save()
        raise
    took = round(time.perf_counter() - start, 2)
    ctx.emitter.emit(stage, f"{step} done in {took:.1f} s", step=step, phase="end", duration_s=took)


def _call(ctx: RunContext, step: str, fn: Callable[..., Any], *args: Any, fixture: str | None = None,
          model: Any = None, default: Any = _MISSING) -> Any:
    """``fn(*args)``; with ``BOOMERANG_ALLOW_FIXTURES=1`` a ``NotBuilt`` stub falls back to a fixture (L9)."""
    try:
        return fn(*args)
    except NotBuilt as e:
        if not get_settings().allow_fixtures:
            raise
        if ctx.run_id == GOLDEN:
            raise PipelineError(f"fixtures are refused for the golden run ({e})") from e
        path = _fixture_path(fixture) if fixture else None
        if path is not None:
            value = TypeAdapter(model).validate_json(path.read_text(encoding="utf-8"))
            what = f"using fixtures/{path.name}"
        elif default is not _MISSING:
            value, what = default, "skipped"
        else:
            names = ", ".join(FIXTURES.get(fixture or "", ())) or "none"
            raise PipelineError(f"{e}; fixture mode is on but no fixture for {step} (looked for: {names})") from e
        if step not in ctx.summary.fixtures_used:
            ctx.summary.fixtures_used.append(step)
        ctx.emit(f"⚠️ {step}: {what} ({e.what} not built yet, owner {e.owner})", "warning", step=step,
                 fixture=path.name if path else None, owner=e.owner)
        return value


def fixtures_dir() -> Path:
    return get_settings().repo_root / "fixtures"


def _fixture_path(name: str) -> Path | None:
    folder = fixtures_dir()
    return next((folder / n for n in FIXTURES.get(name, (f"{name}.json",)) if (folder / n).is_file()), None)


def _copy_source(ctx: RunContext, source: Path) -> None:
    """The source app (and its tests: `<src>/tests`, else `<src>/../tests`) → original/ (rollback source)."""
    shutil.copytree(source, ctx.original, ignore=copy_ignore())
    tests = source / "tests"
    if not tests.is_dir() and (source.parent / "tests").is_dir():
        tests = source.parent / "tests"
        shutil.copytree(tests, ctx.original / "tests", ignore=copy_ignore())
    found = f"tests from {tests.relative_to(source.parent).as_posix()}/" if tests.is_dir() else "no tests found"
    files = sum(1 for p in ctx.original.rglob("*") if p.is_file())
    ctx.emit(f"copied {source.name}/ → original/ ({files} files, {found})", step="copy source", files=files)


def _write_rewrites(ctx: RunContext) -> None:
    """delock.json = every RewriteRecord of the run, fixer rounds included (D's Code tab reads it)."""
    atomic_write_text(ctx.root / "delock.json",
                      json.dumps([r.model_dump(mode="json") for r in ctx.summary.rewrites], indent=2,
                                 ensure_ascii=False) + "\n")


def _read_hashes(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, _, rel = line.partition("  ")
            out[rel] = digest
    return out


def _rmtree(path: Path) -> None:
    """``shutil.rmtree`` that also removes read-only files (Windows) and ignores a missing folder."""
    def make_writable(func: Callable[..., Any], target: str, _exc: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable)
