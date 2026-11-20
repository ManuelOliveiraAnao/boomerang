"""Pipeline, approval gate and report (docs/15 §5.4). Every other role is faked: no Docker, no model.

The de-lock is the real one (C3) on tests/data/mini_app with a fake LLM.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from boomerang import pipeline, report
from boomerang._stub import NotBuilt
from boomerang.cli import app as cli_app
from boomerang.config import get_settings
from boomerang.events import read_events
from boomerang.fsutil import hash_tree
from boomerang.models import (
    AppModel,
    Check,
    DataCount,
    DataMove,
    FunctionRewrite,
    GeneratedFile,
    LostItem,
    RewriteRecord,
    RunSummary,
    TestFailure,
    TestReport,
    Verification,
)
from boomerang.pipeline import NotApproved, PipelineError, RunExists
from tests.test_delock import GET_ORDER, LIST_BY_CUSTOMER, MINI_APP, mini_assessment, mini_pam

SECRET = "pw-Zq8xR2mN5vL1kT7c"


# ---------- builders ----------

def make_report(platform: str = "leaseweb", attempt: int = 1, passed: int = 7, failed: int = 0) -> TestReport:
    failures = [TestFailure(test=f"tests/test_mini.py::test_{i}", message="assert 6 == 7", traceback="tb")
                for i in range(failed)]
    return TestReport(attempt=attempt, platform=platform, passed=passed, failed=failed, errors=0,  # type: ignore[arg-type]
                      total=passed + failed, duration_s=1.0, failures=failures)


def verification(tests_ok: bool = True, boot_ok: bool = True, attempt: int = 1) -> Verification:
    checks = [Check(name="compose config", ok=True), Check(name="boot", ok=boot_ok, detail="" if boot_ok else "api exited")]
    if not boot_ok:
        return Verification(checks=checks)
    checks += [Check(name="tests", ok=tests_ok), Check(name="health", ok=True)]
    return Verification(checks=checks, tests=make_report(attempt=attempt, passed=7 if tests_ok else 6,
                                                         failed=0 if tests_ok else 1))


def data_move(ok: bool = True) -> DataMove:
    return DataMove(direction="aws->leaseweb", counts=[
        DataCount(component_id="orders", kind="kv_table", source_count=25, target_count=25 if ok else 24,
                  checksum_match=ok),
        DataCount(component_id="receipts", kind="object_store", source_count=25, target_count=25, checksum_match=True),
        DataCount(component_id="order_events", kind="queue", source_count=3, target_count=3)])


def fix_record(attempt: int, accepted: bool = True) -> RewriteRecord:
    return RewriteRecord(file="app/db.py", function="get_order", kind="fix", attempt=attempt, accepted=accepted,
                         changed_lines=1, analysis="Decimal → int was lost")


GENERATED = [
    GeneratedFile(path="docker-compose.yml", content="services:\r\n  api: {}\r\n"),
    GeneratedFile(path="deploy.sh", content="#!/bin/sh\r\necho deploy\r\n", executable=True),
    GeneratedFile(path="backup/pg_backup.sh", content="#!/bin/sh\rpg_dump\r\n", executable=True),
    GeneratedFile(path="secrets.env", content=f"DB_PASSWORD={SECRET}\n", secret=True),
    GeneratedFile(path="app/Dockerfile", content="FROM python:3.12-slim\n"),
]


# ---------- the fake world (A, B and C5) ----------

@dataclass
class World:
    calls: list[str] = field(default_factory=list)
    lab_up: bool = True
    baseline: TestReport = field(default_factory=lambda: make_report("aws", 0))
    verifications: list[Verification] = field(default_factory=lambda: [verification()])
    fixes: list[list[RewriteRecord]] = field(default_factory=list)
    move: DataMove = field(default_factory=data_move)
    generated: list[GeneratedFile] = field(default_factory=lambda: list(GENERATED))
    on_verify: Callable[[Path, int], None] | None = None
    docker: list[list[str]] = field(default_factory=list)

    def verify(self, leaseweb_dir: Path, attempt: int, emit: Any) -> Verification:
        self.calls.append(f"verify:{attempt}")
        assert (leaseweb_dir / "app" / "sovereign_sdk" / "storage.py").is_file()
        if self.on_verify:
            self.on_verify(leaseweb_dir, attempt)
        return self.verifications[min(attempt - 1, len(self.verifications) - 1)]

    def fix(self, app_dir, original_dir, pam, test_report_, attempt, rewrites, diffs_dir, emit) -> list[RewriteRecord]:
        self.calls.append(f"fix:{attempt}")
        assert not test_report_.ok and original_dir.name == "original"
        return self.fixes[attempt - 1] if attempt <= len(self.fixes) else []


RESIDUAL = AppModel(app_name="mini", platform="leaseweb", entrypoints=[], components=[], files=[], env=[])


@pytest.fixture
def llm(fake_llm):
    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="ConsistentRead dropped", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="Key(...).eq", new_source=LIST_BY_CUSTOMER),
    })
    return fake_llm


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, llm) -> World:
    w = World()

    def record(name: str, result: Any = None) -> Callable[..., Any]:
        def fn(*args: Any, **kwargs: Any) -> Any:
            w.calls.append(name)
            return result
        return fn

    def scan(app_dir: Path, emit: Any) -> AppModel:
        w.calls.append("scan")
        return mini_pam(app_dir)

    def run_tests(app_dir: Path, env: dict, platform: str, attempt: int, emit: Any) -> TestReport:
        w.calls.append(f"run_tests:{platform}:{app_dir.name}")
        return w.baseline

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        assert isinstance(cmd, list) and "shell" not in kwargs
        w.docker.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    patches = {
        "boomerang.scanner.scan": scan,
        "boomerang.scanner.static.scan_static": lambda app_dir, exclude=("tests", "sovereign_sdk"): RESIDUAL,
        "boomerang.assess.assess": lambda pam, emit: mini_assessment(),
        "boomerang.lockin.score": lambda pam, scope=None: (80 if pam.components else 12) if scope == [] else 999,
        "boomerang.lab.status": lambda: w.lab_up,
        "boomerang.lab.reset": record("lab.reset"),
        "boomerang.provisioner_aws.provision": record("provision"),
        "boomerang.provisioner_aws.seed": record("seed", {"orders": 25, "receipts": 25, "messages": 3}),
        "boomerang.verifier.run_tests": run_tests,
        "boomerang.verifier.aws_env": lambda: {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566"},
        "boomerang.verifier.leaseweb_env": lambda leaseweb_dir: {"DATABASE_URL": "postgresql://127.0.0.1:5432/x"},
        "boomerang.verifier.verify": w.verify,
        "boomerang.verifier.reset_data": record("reset_data"),
        "boomerang.verifier.stop_service": lambda d, name: w.calls.append(f"stop:{name}"),
        "boomerang.verifier.start_service": lambda d, name: w.calls.append(f"start:{name}"),
        "boomerang.datamover.move": lambda pam, direction, src, dst, emit: (w.calls.append(f"move:{direction}"),
                                                                             w.move)[1],
        "boomerang.generators.leaseweb.make_secrets": lambda pam: {"DB_PASSWORD": SECRET},
        "boomerang.generators.leaseweb.render": lambda pam, secrets: w.generated,
        "boomerang.fixer.fix": w.fix,
    }
    for target, value in patches.items():
        monkeypatch.setattr(target, value)
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    return w


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """ShopLite's layout: the app in sample-app/aws, its tests next to it in sample-app/tests."""
    aws = tmp_path / "sample-app" / "aws"
    shutil.copytree(MINI_APP / "app", aws / "app")
    shutil.copytree(MINI_APP / "tests", aws.parent / "tests")
    (aws / "infra").mkdir()
    (aws / "infra" / "main.tf").write_text('resource "aws_lambda_function" "thumbnailer" {}\n', encoding="utf-8")
    (aws / ".env").write_text("DB_PASSWORD=real-local-secret-123\n", encoding="utf-8")
    (aws / ".env.example").write_text("ORDERS_TABLE=orders\n", encoding="utf-8")
    return aws


def root_of(run_id: str) -> Path:
    return get_settings().runs_dir / run_id


def messages(run_id: str, level: str | None = None) -> list[str]:
    return [e.message for e in read_events(root_of(run_id) / "events.jsonl") if level in (None, e.level)]


def planned(source: Path, run_id: str = "r1") -> pipeline.RunContext:
    return pipeline.plan(source, run_id=run_id)


# ---------- plan and the gate ----------

def test_plan_stops_at_the_gate_and_writes_nothing_in_leaseweb(world, source):
    ctx = planned(source)
    root = ctx.root
    assert ctx.summary.status == "awaiting_approval"
    assert pipeline.load_run("r1").status == "awaiting_approval"
    assert (root / "pam.json").is_file() and (root / "assessment.json").is_file()
    assert not (root / "leaseweb").exists() and not (root / "approval.json").exists()
    assert (root / "original" / "tests" / "test_mini.py").is_file()           # found in ../tests
    assert (root / "original" / "infra" / "main.tf").is_file()                # original keeps the IaC
    assert (root / "original" / ".env.example").is_file()
    assert not (root / "original" / ".env").exists()                          # never copied
    assert ctx.summary.lock_in_before == 80 and ctx.summary.llm.startswith("ollama")
    assert any(m.startswith("plan ready: 1 🟢 · 2 🟠 · 1 🔴") for m in messages("r1"))
    assert any("will NOT be migrated: receipt_thumbnailer" in m for m in messages("r1", "warning"))
    assert "verify:1" not in world.calls and "lab.reset" not in world.calls


def test_execute_refuses_without_an_approval(world, source):
    planned(source)
    with pytest.raises(NotApproved, match="not approved"):
        pipeline.execute("r1")
    assert pipeline.load_run("r1").status == "awaiting_approval"            # still waiting, not "error"
    assert not (root_of("r1") / "leaseweb").exists()
    assert world.calls == ["scan"]


@pytest.mark.parametrize("name", ["assessment.json", "pam.json"])
def test_execute_refuses_when_the_plan_changed_after_approval(world, source, name):
    planned(source)
    approval = pipeline.approve("r1", approved_by="ui")
    assert approval.approved_by == "ui" and approval.plan_sha256 == pipeline.plan_sha256(root_of("r1"))
    path = root_of("r1") / name
    path.write_text(path.read_text(encoding="utf-8").replace('"amber"', '"green"').replace('"mini"', '"x"'),
                    encoding="utf-8")
    with pytest.raises(NotApproved, match="changed after approval"):
        pipeline.execute("r1")
    assert not (root_of("r1") / "leaseweb").exists()


def test_migrate_without_yes_waits_for_approval(world, source):
    summary = pipeline.migrate(source, run_id="r1")
    assert summary.status == "awaiting_approval" and not (root_of("r1") / "approval.json").exists()


def test_existing_run_needs_force(world, source):
    planned(source)
    with pytest.raises(RunExists):
        planned(source)
    (root_of("r1") / "stale.txt").write_text("x", encoding="utf-8")
    ctx = pipeline.plan(source, run_id="r1", force=True)
    assert ctx.summary.status == "awaiting_approval" and not (root_of("r1") / "stale.txt").exists()


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "-x", "x" * 65])
def test_bad_run_ids_are_refused(bad):
    with pytest.raises(PipelineError, match="bad run id"):
        pipeline.run_dir(bad)


def test_runs_folder_inside_the_app_is_refused(world, tmp_path, monkeypatch):
    monkeypatch.setenv("BOOMERANG_RUNS_DIR", str(tmp_path / "app" / "runs"))
    get_settings.cache_clear()
    (tmp_path / "app").mkdir()
    with pytest.raises(PipelineError, match="inside the app"):
        pipeline.plan(tmp_path / "app")


# ---------- the happy path ----------

def test_full_run_passes(world, source):
    planned(source)
    approved_bytes = (root_of("r1") / "assessment.json").read_bytes()
    pipeline.approve("r1", approved_by="auto")
    summary = pipeline.execute("r1")
    root, app = root_of("r1"), root_of("r1") / "leaseweb" / "app"

    assert summary.status == "passed" and summary.finished_at is not None
    assert pipeline.load_run("r1") == summary
    assert world.calls == [
        "scan",
        "lab.reset", "provision", "run_tests:aws:original", "lab.reset", "provision", "seed",   # L5
        "verify:1",
        "reset_data", "stop:worker", "move:aws->leaseweb", "start:worker",                    # L4
    ]
    # baseline, lock-in, verify, data
    assert summary.baseline and summary.baseline.ok and (root / "baseline.json").is_file()
    assert (summary.lock_in_before, summary.lock_in_after) == (80, 12)
    assert len(summary.verifications) == 1 and (root / "verify" / "attempt-1.json").is_file()
    assert summary.data_moves == [world.move] and (root / "data" / "aws-to-leaseweb.json").is_file()
    assert pipeline.active_stack() == "r1"
    # prepare: no AWS IaC in the deliverable; the original keeps it
    assert not (app / "infra").exists() and (root / "original" / "infra" / "main.tf").is_file()
    assert (app / "tests" / "test_mini.py").is_file()
    hashes = (root / "tests.sha256").read_text(encoding="utf-8").splitlines()
    assert len(hashes) == 1 and hashes[0].endswith("  test_mini.py") and len(hashes[0].split()[0]) == 64
    # de-lock (real, C3): the approved assessment is untouched; the final one feeds the summary
    assert (root / "assessment.json").read_bytes() == approved_bytes
    assert (root / "assessment.final.json").is_file() and (root / "delock.json").is_file()
    assert {(r.function, r.kind) for r in summary.rewrites} >= {("get_order", "ai"), ("put_order", "config")}
    assert "kv.resource()" in (app / "app" / "db.py").read_text(encoding="utf-8")
    # generate: LF only, contained, sovereign_sdk copied (no tests, no caches)
    for rel in ("deploy.sh", "backup/pg_backup.sh", "docker-compose.yml"):
        assert b"\r" not in (root / "leaseweb" / rel).read_bytes()
    assert (root / "leaseweb" / "secrets.env").read_text(encoding="utf-8") == f"DB_PASSWORD={SECRET}\n"
    assert sorted(p.name for p in (app / "sovereign_sdk").iterdir()) == ["__init__.py", "kv.py", "queue.py",
                                                                         "storage.py"]
    # test integrity, report
    assert summary.tests_unchanged is True
    text = (root / "report.md").read_text(encoding="utf-8")
    assert "✅ **passed**" in text and "AWS 7/7 → Leaseweb 7/7" in text and "80 → 12" in text
    assert SECRET not in text and "real-local-secret" not in text
    assert any(m == "run passed: verified, original tests unchanged, data counts match" for m in messages("r1"))


def test_every_event_has_a_valid_stage_and_every_step_starts_and_ends(world, source):
    pipeline.migrate(source, yes=True, run_id="r1")
    events = read_events(root_of("r1") / "events.jsonl")                    # validates every Stage
    steps = [(e.data or {}).get("step") for e in events if (e.data or {}).get("phase") == "start"]
    ends = [(e.data or {}).get("step") for e in events if (e.data or {}).get("phase") == "end"]
    assert steps == ends == ["copy source", "scan", "assess", "lock-in before", "gate", "baseline", "prepare",
                             "delock", "generate", "lock-in after", "verify", "test integrity", "data", "report"]
    assert {e.stage for e in events} >= {"scan", "assess", "approve", "verify", "generate", "delock", "data",
                                         "report"}


def test_emit_extra_gets_every_event(world, source):
    seen = []
    pipeline.migrate(source, yes=True, run_id="r1", emit_extra=seen.append)
    assert [e.message for e in seen] == messages("r1")


def test_baseline_failure_is_a_warning_not_a_stop(world, source):
    world.baseline = make_report("aws", 0, passed=6, failed=1)
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.status == "passed" and summary.baseline and not summary.baseline.ok
    assert any("fails its own tests on AWS" in m for m in messages("r1", "warning"))
    assert "fails its own tests on AWS" in (root_of("r1") / "report.md").read_text(encoding="utf-8")


def test_lab_down_stops_with_a_clear_message(world, source):
    world.lab_up = False
    with pytest.raises(PipelineError, match="boomerang lab up"):
        pipeline.migrate(source, yes=True, run_id="r1")
    assert pipeline.load_run("r1").status == "error"


# ---------- the verify / fix loop (L11) ----------

def test_failing_tests_get_fixed_on_attempt_2(world, source):
    world.verifications = [verification(tests_ok=False), verification(attempt=2)]
    world.fixes = [[fix_record(1)]]
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.status == "passed"
    assert [c for c in world.calls if c.startswith(("verify", "fix"))] == ["verify:1", "fix:1", "verify:2"]
    assert summary.rewrites[-1].kind == "fix" and len(summary.verifications) == 2
    assert '"kind": "fix"' in (root_of("r1") / "delock.json").read_text(encoding="utf-8")   # D's Code tab


def test_boot_failure_is_not_sent_to_the_fixer(world, source):
    world.verifications = [verification(boot_ok=False)]
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.status == "failed"
    assert "fix:1" not in world.calls and "move:aws->leaseweb" not in world.calls       # data skipped too
    assert any("template/boot problem" in m for m in messages("r1", "error"))


def test_loop_stops_when_the_fixer_finds_nothing(world, source):
    world.verifications = [verification(tests_ok=False)]
    world.fixes = [[fix_record(1, accepted=False)]]
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.status == "failed" and len(summary.verifications) == 1
    assert "move:aws->leaseweb" in world.calls                              # the stack is up: data still moves


@pytest.mark.parametrize("max_fix, expected", [
    (0, ["verify:1"]),
    (1, ["verify:1", "fix:1", "verify:2"]),
    (2, ["verify:1", "fix:1", "verify:2", "fix:2", "verify:3"]),
])
def test_max_fix_attempts(world, source, monkeypatch, max_fix, expected):
    monkeypatch.setenv("BOOMERANG_MAX_FIX_ATTEMPTS", str(max_fix))
    get_settings.cache_clear()
    world.verifications = [verification(tests_ok=False)]
    world.fixes = [[fix_record(1)], [fix_record(2)], [fix_record(3)]]
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert [c for c in world.calls if c.startswith(("verify", "fix"))] == expected
    assert summary.status == "failed" and len(summary.verifications) == max_fix + 1


# ---------- test integrity (L12) ----------

def test_changed_tests_fail_the_run(world, source):
    def tamper(leaseweb: Path, attempt: int) -> None:
        test = leaseweb / "app" / "tests" / "test_mini.py"
        test.write_text(test.read_text(encoding="utf-8") + "\n# weakened\n", encoding="utf-8")

    world.on_verify = tamper
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.tests_unchanged is False and summary.status == "failed"
    assert any("original tests CHANGED" in m for m in messages("r1", "error"))


def test_pytest_caches_under_tests_do_not_flip_integrity(world, source):
    def caches(leaseweb: Path, attempt: int) -> None:
        tests = leaseweb / "app" / "tests"
        (tests / "__pycache__").mkdir()
        (tests / "__pycache__" / "test_mini.cpython-312-pytest-8.pyc").write_bytes(b"\x00pyc")
        (tests / ".pytest_cache").mkdir()
        (tests / ".pytest_cache" / "README.md").write_text("cache", encoding="utf-8")

    world.on_verify = caches
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.tests_unchanged is True and summary.status == "passed"


def test_tests_inside_the_app_folder_are_found(world, tmp_path):
    src = tmp_path / "mini"
    shutil.copytree(MINI_APP, src)
    summary = pipeline.migrate(src, yes=True, run_id="r1")
    assert summary.status == "passed" and summary.tests_unchanged is True


# ---------- errors and re-runs ----------

@pytest.mark.parametrize("error, shown", [(RuntimeError("boom"), "RuntimeError: boom"),
                                          (KeyboardInterrupt(), "interrupted")])
def test_a_failing_stage_marks_the_run_error_and_saves(world, source, monkeypatch, error, shown):
    def explode(leaseweb_dir, attempt, emit):
        raise error

    monkeypatch.setattr("boomerang.verifier.verify", explode)
    with pytest.raises(type(error)):
        pipeline.migrate(source, yes=True, run_id="r1")
    saved = pipeline.load_run("r1")
    assert saved.status == "error" and saved.finished_at is not None
    assert messages("r1", "error")[-1] == f"verify failed: {shown}"
    assert (root_of("r1") / "report.md").is_file()                          # best-effort report after an error


def test_execute_twice_on_the_same_run(world, source):
    first = pipeline.migrate(source, yes=True, run_id="r1")
    diffs = sorted(p.name for p in (root_of("r1") / "diffs").iterdir())
    second = pipeline.execute("r1")
    assert first.status == second.status == "passed"
    assert len(second.verifications) == 1 and len(second.rewrites) == len(first.rewrites)
    assert sorted(p.name for p in (root_of("r1") / "diffs").iterdir()) == diffs
    compose = str(root_of("r1") / "leaseweb" / "docker-compose.yml")
    assert world.docker == [["/bin/docker", "compose", "-p", "bmr-verify", "-f", compose, "down", "-v",
                             "--remove-orphans"]]
    assert any(m.startswith("cleaned the previous attempt") for m in messages("r1"))


def test_cleanup_only_touches_the_active_stack(world, source):
    pipeline.migrate(source, yes=True, run_id="r1")
    pipeline.cleanup("other")
    assert world.docker == [] and pipeline.active_stack() == "r1"
    pipeline.cleanup("r1")
    assert len(world.docker) == 1 and pipeline.active_stack() is None


@pytest.mark.parametrize("bad", ["../evil.sh", "/etc/evil", "C:/evil", "app/tests/test_evil.py",
                                 "app\\Tests\\x.py", "app/Sovereign_SDK/kv.py", ""])
def test_generated_files_stay_inside_leaseweb(world, source, bad):
    world.generated = [*GENERATED, GeneratedFile(path=bad, content="x")]
    with pytest.raises(PipelineError, match="generator returned a path"):
        pipeline.migrate(source, yes=True, run_id="r1")
    assert pipeline.load_run("r1").status == "error"
    assert not (root_of("r1").parent / "evil.sh").exists()


# ---------- fixture fallback (L9) ----------

# What the fixture-mode tests treat as "not built yet": every role has finished its modules by now.
UNBUILT = {
    "boomerang.scanner.scan": "B", "boomerang.scanner.static.scan_static": "B", "boomerang.assess.assess": "B",
    "boomerang.lockin.score": "B", "boomerang.generators.leaseweb.make_secrets": "B",
    "boomerang.generators.leaseweb.render": "B", "boomerang.lost.build": "B",
    "boomerang.lab.status": "A", "boomerang.verifier.verify": "A", "boomerang.verifier.reset_data": "A",
}


def unbuild(monkeypatch: pytest.MonkeyPatch, targets: dict[str, str] = UNBUILT) -> None:
    for target, owner in targets.items():
        def raiser(*args: Any, _what: str = target, _owner: str = owner, **kwargs: Any) -> Any:
            raise NotBuilt(_what, owner=_owner)
        monkeypatch.setattr(target, raiser)


def test_not_built_stops_the_run_without_fixture_mode(source, monkeypatch):
    unbuild(monkeypatch)
    with pytest.raises(NotBuilt, match="owner B"):
        pipeline.plan(source, run_id="r1")
    assert pipeline.load_run("r1").status == "error"


@pytest.fixture
def fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "fixtures"
    folder.mkdir()
    (folder / "pam.aws.json").write_text(mini_pam(MINI_APP).model_dump_json(), encoding="utf-8")
    (folder / "assessment.json").write_text(mini_assessment().model_dump_json(), encoding="utf-8")
    (folder / "verification.json").write_text(verification().model_dump_json(), encoding="utf-8")
    (folder / "data.aws-to-leaseweb.json").write_text(data_move().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(pipeline, "fixtures_dir", lambda: folder)
    unbuild(monkeypatch)
    monkeypatch.setenv("BOOMERANG_ALLOW_FIXTURES", "1")
    get_settings.cache_clear()
    return folder


def test_fixture_mode_runs_end_to_end_and_says_so(fixtures, source, llm):
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.fixtures_used == ["scan", "assess", "lock-in before", "baseline", "generate", "lock-in after",
                                     "verify", "data"]
    assert summary.status == "passed" and pipeline.active_stack() is None   # nothing was really booted
    assert any("using fixtures/pam.aws.json" in m for m in messages("r1", "warning"))
    assert "Fixtures were used for: scan, assess" in (root_of("r1") / "report.md").read_text(encoding="utf-8")


def test_fixture_mode_keeps_plan_fixtures_across_a_rerun(fixtures, source, llm):
    pipeline.migrate(source, yes=True, run_id="r1")
    again = pipeline.execute("r1")
    assert again.fixtures_used[:3] == ["scan", "assess", "lock-in before"] and again.fixtures_used.count("verify") == 1


def test_fixtures_are_refused_for_the_golden_run(fixtures, source):
    with pytest.raises(PipelineError, match="golden"):
        pipeline.plan(source, run_id="golden")


def test_a_real_not_implemented_error_is_never_hidden_behind_a_fixture(fixtures, source, monkeypatch):
    def scan(app_dir, emit):
        raise NotImplementedError("CloudFormation is not supported")

    monkeypatch.setattr("boomerang.scanner.scan", scan)
    with pytest.raises(NotImplementedError, match="CloudFormation"):
        pipeline.plan(source, run_id="r1")


def test_missing_fixture_says_which_files_it_looked_for(fixtures, source):
    (fixtures / "pam.aws.json").unlink()
    with pytest.raises(PipelineError, match="pam.aws.json, pam.json"):
        pipeline.plan(source, run_id="r1")


# ---------- report ----------

def _ctx_with(summary: RunSummary, root: Path) -> pipeline.RunContext:
    events: list = []
    return pipeline.RunContext(summary.run_id, root, Path(summary.source_dir), summary,
                               pipeline.Emitter(summary.run_id, [events.append]))


def test_report_redacts_a_secret_and_says_so(world, source):
    pipeline.migrate(source, yes=True, run_id="r1")
    summary = pipeline.load_run("r1")
    summary.verifications[0].checks[0].detail = f"postgresql://shoplite:{SECRET}@127.0.0.1:5432/shoplite"
    ctx = _ctx_with(summary, root_of("r1"))
    seen = []
    ctx.emitter.add_sink(seen.append)
    text = report.write(ctx).read_text(encoding="utf-8")
    assert SECRET not in text and "<REDACTED:" in text
    assert any(e.level == "error" and "secret-like" in e.message for e in seen)


def test_report_sections_for_a_round_trip(tmp_path):
    root = tmp_path / "rt"
    root.mkdir()
    (root / "cost.json").write_text('{"aws_monthly_eur": 412.5, "leaseweb_monthly_eur": 233.0, "lines": []}',
                                    encoding="utf-8")
    summary = RunSummary(
        run_id="rt", source_dir="sample-app/aws", status="passed", started_at=0, out_dir=str(root),
        assessment=mini_assessment(), lock_in_before=80, lock_in_after=12,
        rewrites=[RewriteRecord(file="app/db.py", function="get_order", kind="ai", accepted=True, changed_lines=1,
                                diff_path="diffs/app_db.py__get_order.diff"),
                  RewriteRecord(file="app/db.py", function="put_order", kind="config", accepted=True),
                  RewriteRecord(file="app/db.py", function="<module>", kind="config", accepted=True, changed_lines=3),
                  RewriteRecord(file="app/q.py", function="weird", kind="ai", accepted=False,
                                rejected_reason="rule 3: os.system", unsupported=["FIFO | dedup"])],
        roundtrip="passed", roundtrip_checks=[Check(name="orders 25 → 26", ok=True)],
        roundtrip_tests=make_report("aws", 99),
        lost=[LostItem(component_id="thumbnailer", feature="s3 trigger", status="lost", detail="Lambda not migrated"),
              LostItem(component_id="order_events", feature="dlq", status="changed", detail="delivery-limit")])
    text = report.render(_ctx_with(summary, root))
    assert "🤖 rewritten by AI" in text and "✔️ 1:1 mapping, checked by code" in text
    assert "⚙️ config change" in text and "❌ kept the original" in text
    assert "[diff](<diffs/app_db.py__get_order.diff>)" in text
    assert "FIFO \\| dedup" in text                                          # pipes escaped in table cells
    assert "1 rewritten by AI · 0 unchanged by AI · 1 1:1 checked by code" in text
    assert "fresh (emulated) AWS environment" in text and "after the copy back: 7/7" in text
    assert text.index("❌ lost") < text.index("🟠 changed")
    assert "| aws_monthly_eur | 412.5 |" in text and "| lines |" not in text
    assert "does not claim zero downtime" in text


def test_report_for_an_empty_run(tmp_path):
    summary = RunSummary(run_id="e", source_dir="x", status="error", started_at=0, out_dir=str(tmp_path))
    text = report.render(_ctx_with(summary, tmp_path))
    assert "No assessment" in text and "Not verified." in text and "Not run yet" in text


# ---------- CLI (docs/15 §2.5: exit codes 0 / 1 / 3 / 4) ----------

runner = CliRunner()


def test_cli_migrate_yes_passes(world, source):
    result = runner.invoke(cli_app, ["migrate", str(source), "--yes", "--run-id", "r1"])
    assert result.exit_code == 0, result.output
    assert "run r1: passed" in result.output
    assert pipeline.load_run("r1").status == "passed"


def test_cli_plan_approve_execute(world, source):
    assert runner.invoke(cli_app, ["plan", str(source), "--run-id", "r1"]).exit_code == 0
    early = runner.invoke(cli_app, ["execute", "r1"])
    assert early.exit_code == 4 and "not approved" in early.output
    assert runner.invoke(cli_app, ["approve", "r1", "--by", "ui"]).exit_code == 0
    world.verifications = [verification(tests_ok=False)]
    result = runner.invoke(cli_app, ["execute", "r1"])
    assert result.exit_code == 1 and "failed" in result.output


def test_cli_migrate_declined_waits(world, source):
    result = runner.invoke(cli_app, ["migrate", str(source), "--run-id", "r1"], input="n\n")
    assert result.exit_code == 4 and "waiting for approval" in result.output
    assert pipeline.load_run("r1").status == "awaiting_approval"


def test_cli_pipeline_errors_exit_3(world, source):
    world.lab_up = False
    result = runner.invoke(cli_app, ["migrate", str(source), "--yes", "--run-id", "r1"])
    assert result.exit_code == 3 and "boomerang lab up" in result.output
    again = runner.invoke(cli_app, ["plan", str(source), "--run-id", "r1"])
    assert again.exit_code == 3 and "--force" in again.output


def test_unchanged_original_tests_hash_matches_the_source(world, source):
    pipeline.migrate(source, yes=True, run_id="r1")
    assert hash_tree(root_of("r1") / "leaseweb" / "app" / "tests") == hash_tree(source.parent / "tests")


def test_plan_accepts_the_folder_the_ui_made_for_its_log(world, source):
    """ui/runner.py (D7) opens runs/<id>/ui-plan.log, then starts `boomerang plan --run-id <id>`."""
    root = root_of("ui1")
    root.mkdir(parents=True)
    (root / "ui-plan.log").write_text("starting\n", encoding="utf-8")
    (root / "leftover.txt").write_text("x", encoding="utf-8")
    ctx = pipeline.plan(source, run_id="ui1")
    assert ctx.summary.status == "awaiting_approval"
    assert (root / "ui-plan.log").read_text(encoding="utf-8") == "starting\n" and not (root / "leftover.txt").exists()
    with pytest.raises(RunExists):
        pipeline.plan(source, run_id="ui1")                     # a real run (run.json) still needs --force
