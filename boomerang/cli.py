"""`boomerang` CLI (docs/03 §3.14, docs/15 §2.5).

Every command imports its owner's module **inside** the function, so one broken module never breaks
``--help`` or the other commands. A stub that isn't built yet exits with code 3 and says who owns it.

Exit codes: 0 = passed, 1 = failed, 3 = error / not built yet, 4 = migrate stopped at the approval
prompt. (2 is Click's own usage-error code.)
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import click
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:
    from boomerang.events import Emitter
    from boomerang.models import Assessment, DataMove, RunSummary, Verification

EXIT_PASSED, EXIT_FAILED, EXIT_ERROR, EXIT_AWAITING = 0, 1, 3, 4
LIGHT_ICONS = {"green": "🟢", "amber": "🟠", "red": "🔴"}

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="🪃 Boomerang: leave AWS for Leaseweb, know what breaks, come back if you want.")
lab_app = typer.Typer(no_args_is_help=True, help="Fake AWS (moto) on 127.0.0.1:4566.")
app.add_typer(lab_app, name="lab")
cache_app = typer.Typer(no_args_is_help=True, help="The LLM cache that replays the demo offline (C7).")
app.add_typer(cache_app, name="cache")

console = Console()


@app.callback()
def _main() -> None:
    # Emoji on a cp1252 console or pipe would crash the output; the README also says PYTHONUTF8=1.
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@contextmanager
def _handled() -> Iterator[None]:
    from boomerang._stub import NotBuilt

    try:
        yield
    except NotBuilt as e:
        console.print(f"[yellow]⏳ {escape(str(e))}[/yellow]")
        raise typer.Exit(EXIT_ERROR) from None
    except FileNotFoundError as e:
        console.print(f"[red]❌ {escape(str(e))}[/red]")
        raise typer.Exit(EXIT_ERROR) from None
    except Exception as e:
        from boomerang.llm import LLMError

        if isinstance(e, LLMError):                 # LLMUnavailable / CacheMiss say what to do
            console.print(f"[red]❌ {type(e).__name__}: {escape(str(e))}[/red]")
            raise typer.Exit(EXIT_ERROR) from None
        names = {cls.__name__ for cls in type(e).__mro__}   # by name: a broken pipeline module must not break this
        if "NotApproved" in names:
            console.print(f"[red]❌ not approved: {escape(str(e))}[/red]")
            raise typer.Exit(EXIT_AWAITING) from None
        if "PipelineError" in names:                         # user-facing stops say what to do
            console.print(f"[red]❌ {escape(str(e))}[/red]")
            raise typer.Exit(EXIT_ERROR) from None
        raise


# ---------- shared output ----------

def light_counts(assessment: Assessment) -> str:
    """``1 🟢 · 4 🟠 · 3 🔴``"""
    counts = Counter(f.light for f in assessment.findings)
    return " · ".join(f"{counts.get(light, 0)} {icon}" for light, icon in LIGHT_ICONS.items())


def lights_table(assessment: Assessment) -> Table:
    table = Table(title=f"Traffic lights: {light_counts(assessment)}", show_lines=False)
    for col in ("component", "feature", "light", "what changes", "evidence"):
        table.add_column(col, overflow="fold")
    for f in assessment.findings:
        evidence = ", ".join(f"{ref.file}:{ref.line}" for ref in f.evidence[:3])
        table.add_row(escape(f.component_id), escape(f.feature), LIGHT_ICONS[f.light],
                      escape(f.what_changes), escape(evidence))
    return table


def _status_exit(status: str) -> int:
    return {"passed": EXIT_PASSED, "failed": EXIT_FAILED, "awaiting_approval": EXIT_AWAITING}.get(status, EXIT_ERROR)


def _print_summary(summary: RunSummary) -> None:
    icon = {"passed": "✅", "failed": "❌", "error": "❌", "awaiting_approval": "⏸️"}.get(summary.status, "·")
    console.print(f"{icon} run [bold]{summary.run_id}[/bold]: {summary.status}"
                  f" · report: {escape(str(Path(summary.out_dir) / 'report.md'))}")


def _run_emitter(run_id: str) -> Emitter:
    from boomerang.config import get_settings
    from boomerang.events import Emitter, console_sink, jsonl_sink

    root = get_settings().runs_dir / run_id
    return Emitter(run_id, [jsonl_sink(root / "events.jsonl"), console_sink(console)])


def _adhoc_emitter() -> Emitter:
    from boomerang.events import Emitter, console_sink

    return Emitter("adhoc", [console_sink(console)])


def _run_root(run_id: str) -> Path:
    from boomerang.config import get_settings

    root = get_settings().runs_dir / run_id
    if not (root / "run.json").is_file():
        raise FileNotFoundError(f"no run {run_id!r} in {root.parent}")
    return root


AppDir = Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Folder of the AWS app to scan.")]
Only = Annotated[str | None, typer.Option(
    "--only", help="Partial migration: comma-separated component ids to run self-hosted (default: all movable).")]
Components = Annotated[str, typer.Argument(help="Comma-separated component ids, e.g. orders,receipts.")]


def _ids(text: str | None) -> list[str] | None:
    """``"orders, receipts"`` → ``["orders", "receipts"]``; None/empty → None (every movable component)."""
    ids = [part.strip() for part in (text or "").split(",") if part.strip()]
    return ids or None
RunId = Annotated[str, typer.Argument(help="Run id (folder name under runs/).")]


# ---------- lab (A) ----------

@lab_app.command("up")
def lab_up() -> None:
    """Start moto."""
    with _handled():
        from boomerang import lab
        lab.up()
        console.print("✅ lab up: moto on 127.0.0.1:4566")


@lab_app.command("down")
def lab_down() -> None:
    """Stop moto."""
    with _handled():
        from boomerang import lab
        lab.down()
        console.print("lab down")


@lab_app.command("status")
def lab_status() -> None:
    """Is moto answering?"""
    with _handled():
        from boomerang import lab
        up = lab.status()
    console.print("✅ lab up" if up else "❌ lab down")
    raise typer.Exit(EXIT_PASSED if up else EXIT_FAILED)


# ---------- scan (B) ----------

@app.command()
def scan(app_dir: AppDir) -> None:
    """Scan an AWS app and show the traffic lights (no files written)."""
    with _handled():
        from boomerang import assess, scanner
        emitter = _adhoc_emitter()
        pam = scanner.scan(app_dir, emitter)
        assessment = assess.assess(pam, emitter)
    console.print(lights_table(assessment))


# ---------- the gate and the pipeline (C) ----------

@app.command()
def migrate(
    app_dir: AppDir,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve the plan without asking.")] = False,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: timestamp).")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing run with this id.")] = False,
    only: Only = None,
) -> None:
    """Scan → lights → approve → de-lock → Leaseweb files → verify → data."""
    with _handled():
        from boomerang import pipeline
        from boomerang.config import get_settings

        yes = yes or get_settings().auto_approve
        ctx = pipeline.plan(app_dir, run_id=run_id, force=force)
        if ctx.summary.assessment:
            console.print(lights_table(ctx.summary.assessment))
        reds = sum(f.light == "red" for f in (ctx.summary.assessment.findings if ctx.summary.assessment else []))
        if not yes:
            try:
                ok = typer.confirm(f"Approve plan? {reds} red items will NOT be migrated")
            except click.exceptions.Abort:
                ok = False
            if not ok:
                console.print(f"⏸️ run {ctx.run_id} is waiting for approval: boomerang approve {ctx.run_id}")
                raise typer.Exit(EXIT_AWAITING)
        pipeline.approve(ctx.run_id, approved_by="auto" if yes else "cli", scope=_ids(only))
        summary = pipeline.execute(ctx.run_id)
    _print_summary(summary)
    raise typer.Exit(_status_exit(summary.status))


@app.command()
def plan(
    app_dir: AppDir,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: timestamp).")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing run with this id.")] = False,
) -> None:
    """Scan + lights only; the run waits for `boomerang approve`."""
    with _handled():
        from boomerang import pipeline
        ctx = pipeline.plan(app_dir, run_id=run_id, force=force)
    if ctx.summary.assessment:
        console.print(lights_table(ctx.summary.assessment))
    console.print(f"⏸️ run {ctx.run_id} is waiting for approval: boomerang approve {ctx.run_id}")


@app.command()
def approve(
    run_id: RunId,
    by: Annotated[str, typer.Option(help="Who approved: cli, ui or auto.")] = "cli",
    only: Only = None,
) -> None:
    """Approve a planned run (records the hash of what was approved, and the scope)."""
    with _handled():
        from boomerang import pipeline
        approval = pipeline.approve(run_id, approved_by=by, scope=_ids(only))
    scope = ", ".join(approval.scope) if approval.scope else "every movable component"
    console.print(f"✅ approved by {approval.approved_by} · plan {approval.plan_sha256[:12]} · self-hosted: {scope}")


@app.command()
def execute(run_id: RunId) -> None:
    """Run an approved plan. This is what the UI starts as a subprocess (docs/15 L2)."""
    with _handled():
        from boomerang import pipeline
        summary = pipeline.execute(run_id)
    _print_summary(summary)
    raise typer.Exit(_status_exit(summary.status))


@app.command()
def roundtrip(run_id: RunId) -> None:
    """Leaseweb → back to AWS: new order, data copied back, original tests, what got lost."""
    with _handled():
        from boomerang import pipeline
        summary = pipeline.roundtrip(run_id)
    icon = {"passed": "✅", "failed": "❌"}.get(summary.roundtrip, "❌")
    console.print(f"{icon} round trip {run_id}: {summary.roundtrip}")
    raise typer.Exit({"passed": EXIT_PASSED, "failed": EXIT_FAILED}.get(summary.roundtrip, EXIT_ERROR))


@app.command("move-over")
def move_over(
    run_id: RunId,
    components: Components,
    by: Annotated[str, typer.Option(help="Who decided: cli or ui.")] = "cli",
) -> None:
    """Partial migration: run more components self-hosted (after a passed migration)."""
    with _handled():
        from boomerang import pipeline
        summary = pipeline.move_over(run_id, _ids(components) or [], by=by)
    _print_scope(summary)
    raise typer.Exit(_status_exit(summary.status))


@app.command("move-back")
def move_back(
    run_id: RunId,
    components: Components,
    by: Annotated[str, typer.Option(help="Who decided: cli or ui.")] = "cli",
) -> None:
    """Partial migration: put components back on AWS, keep the rest self-hosted."""
    with _handled():
        from boomerang import pipeline
        summary = pipeline.move_back(run_id, _ids(components) or [], by=by)
    _print_scope(summary)
    raise typer.Exit(_status_exit(summary.status))


def _print_scope(summary: RunSummary) -> None:
    scope = ", ".join(summary.scope.selfhosted) if summary.scope else "every movable component"
    _print_summary(summary)
    console.print(f"self-hosted now: {escape(scope)} · steps: {len(summary.steps)}")


# ---------- verify and data (A) ----------

@app.command()
def verify(
    run_id: RunId,
    attempt: Annotated[int, typer.Option(help="Attempt number for the record.")] = 1,
) -> None:
    """Re-verify a run's Leaseweb files: config → boot → schema → tests → health."""
    with _handled():
        from boomerang import verifier
        root = _run_root(run_id)
        result: Verification = verifier.verify(root / "leaseweb", attempt, _run_emitter(run_id))
    for check in result.checks:
        console.print(f"{'✅' if check.ok else '❌'} {escape(check.name)} {escape(check.detail)}")
    if result.tests:
        console.print(f"tests {result.tests.passed}/{result.tests.total}")
    raise typer.Exit(EXIT_PASSED if result.ok else EXIT_FAILED)


class Direction(str, Enum):
    aws_to_leaseweb = "aws->leaseweb"
    leaseweb_to_aws = "leaseweb->aws"


@app.command()
def data(
    run_id: RunId,
    direction: Annotated[Direction, typer.Option(help="Which way to copy.")] = Direction.aws_to_leaseweb,
) -> None:
    """Copy data between the two sides with counts and checksums."""
    with _handled():
        from boomerang import datamover, verifier
        from boomerang.fsutil import atomic_write_text
        from boomerang.models import AppModel

        root = _run_root(run_id)
        pam = AppModel.model_validate_json((root / "pam.json").read_text(encoding="utf-8"))
        aws, lw = verifier.aws_env(), verifier.leaseweb_env(root / "leaseweb")
        src, dst = (aws, lw) if direction is Direction.aws_to_leaseweb else (lw, aws)
        move: DataMove = datamover.move(pam, direction.value, src, dst, _run_emitter(run_id))
        atomic_write_text(root / "data" / f"{direction.value.replace('->', '-to-')}.json",
                          move.model_dump_json(indent=2))
    table = Table(title=f"Data {direction.value}")
    for col in ("component", "source", "target", "checksum"):
        table.add_column(col)
    for c in move.counts:
        checksum = {True: "✅", False: "❌", None: "–"}[c.checksum_match]
        table.add_row(c.component_id, str(c.source_count), str(c.target_count), checksum)
    console.print(table)
    raise typer.Exit(EXIT_PASSED if move.ok else EXIT_FAILED)


# ---------- cost (D) ----------

@app.command()
def cost(
    usage: Annotated[Path | None, typer.Option(help="Usage YAML (default: pricing/usage.default.yaml).")] = None,
    only: Only = None,
) -> None:
    """Monthly AWS vs Leaseweb costs from published prices, your usage and ops hours."""
    with _handled():
        import yaml

        from boomerang import cost as cost_mod
        from boomerang.config import get_settings

        pricing = get_settings().repo_root / "pricing"

        def load(path: Path) -> dict:
            if not path.is_file():
                raise FileNotFoundError(f"missing {path}")
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        scope = _ids(only)
        result = (cost_mod.estimate(load(pricing / "prices.yaml"), load(usage or pricing / "usage.default.yaml"),
                                    load(pricing / "ops.yaml"))
                  if scope is None else
                  cost_mod.estimate(load(pricing / "prices.yaml"), load(usage or pricing / "usage.default.yaml"),
                                    load(pricing / "ops.yaml"), scope))
    console.print(result)


# ---------- LLM check and runs (C) ----------

@app.command("llm-check")
def llm_check() -> None:
    """One toy structured call with timing (the Q1 benchmark as a command)."""
    from pydantic import BaseModel

    class Toy(BaseModel):
        analysis: str
        answer: int

    with _handled():
        from boomerang import llm
        from boomerang.config import get_settings

        console.print(f"model: {get_settings().llm_badge}")
        start = time.perf_counter()
        result = llm.structured(system="You answer with JSON that matches the schema.",
                                user="What is 2 + 3? Explain in one short sentence, then give the number.",
                                schema=Toy, label="llm-check", emit=_adhoc_emitter(), stage="scan")
        took = time.perf_counter() - start
    ok = result.answer == 5
    console.print(f"{'✅' if ok else '❌'} answer={result.answer} in {took:.1f} s · {escape(result.analysis)}")
    raise typer.Exit(EXIT_PASSED if ok else EXIT_FAILED)


@app.command()
def runs() -> None:
    """List runs (newest first)."""
    with _handled():
        from boomerang import pipeline
        summaries = pipeline.list_runs()
    if not summaries:
        console.print("no runs yet")
        return
    table = Table(title="Runs")
    for col in ("run id", "status", "started"):
        table.add_column(col, no_wrap=True)          # ids get copied into the next command: never fold them
    for col in ("source", "round trip"):
        table.add_column(col, overflow="fold")
    for s in summaries:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.started_at))
        table.add_row(s.run_id, s.status, started, escape(s.source_dir), s.roundtrip)
    console.print(table)


# ---------- demo LLM cache (C, C7) ----------

CacheDir = Annotated[Path | None, typer.Option("--cache-dir", file_okay=False,
                                               help="Default: BOOMERANG_CACHE_DIR (demo/llm-cache for the demo).")]


@cache_app.command("check")
def cache_check(
    run: Annotated[list[str] | None, typer.Option("--run", help="Run(s) that must be covered: repeat the flag.")] = None,
    cache_dir: CacheDir = None,
    prune: Annotated[bool, typer.Option("--prune", help="Delete entries none of the runs used.")] = False,
) -> None:
    """Before committing the cache: entries valid, no secrets, every call covered, nothing stale, no drift."""
    with _handled():
        from boomerang import democache
        from boomerang.config import get_settings

        folder = cache_dir or get_settings().cache_dir
        roots = [_run_root(r) for r in run or []]
        result = democache.check(folder, roots)
        if prune and result.unused:
            for path in democache.prune(result):
                console.print(f"🗑️ pruned {escape(path.name)}")
    _print_cache(result, roots)
    raise typer.Exit(EXIT_PASSED if result.ok else EXIT_FAILED)


@cache_app.command("freeze")
def cache_freeze(
    run: Annotated[str, typer.Option("--run", help="The run that recorded the cache.")] = "golden",
    cache_dir: CacheDir = None,
) -> None:
    """Right after recording: check, then write manifest.json (prompts, schemas, provider, model, entries)."""
    with _handled():
        from boomerang import democache
        from boomerang.config import get_settings

        folder = cache_dir or get_settings().cache_dir
        result = democache.check(folder, [_run_root(run)])
        _print_cache(result, [_run_root(run)])
        if not result.ok:
            console.print("[red]❌ not frozen: fix the problems above (re-record into an empty folder)[/red]")
            raise typer.Exit(EXIT_FAILED)
        path = democache.write_manifest(folder, run)
    console.print(f"🧊 frozen: {escape(str(path))}. Any edit to prompts/*.md or a schema now needs a re-recording.")


def _print_cache(result, roots: list[Path]) -> None:
    by_label: Counter[str] = Counter(e.label.split(":", 1)[0] for e in result.entries)
    kinds = ", ".join(f"{n} {label}" for label, n in sorted(by_label.items())) or "none"
    console.print(f"cache {escape(str(result.cache_dir))}: {len(result.entries)} entr(y/ies) ({escape(kinds)})"
                  + (f" · runs: {', '.join(r.name for r in roots)}" if roots else ""))
    for line in result.problems:
        console.print(f"[red]❌ {escape(line)}[/red]")
    for line in result.drift:
        console.print(f"[red]❌ drift: {escape(line)}[/red]")
    for label in result.missing:
        console.print(f"[red]❌ missing: {escape(label)} (recorded in live mode? re-record)[/red]")
    for e in result.unused:
        console.print(f"[yellow]⚠️ unused: {escape(e.path.name)} ({escape(e.label)}); --prune deletes it[/yellow]")
    for line in result.notes:
        console.print(f"[yellow]· {escape(line)}[/yellow]")
    if result.ok:
        console.print("✅ cache ok: replay will find every answer these runs need")
