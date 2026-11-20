"""Pipeline: plan → (human) → approve → execute, and the round trip. Owner C, tasks C4 and C6.

Spec: docs/15 §5 (gate L2, steps, run directory) and §7 (round trip). The gate calls, execute and
the round trip are stubs until C4/C6; run loading and listing already work (the CLI ``runs`` uses them).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.config import get_settings
from boomerang.events import Emitter
from boomerang.fsutil import atomic_write_text
from boomerang.models import Approval, Event, RunSummary

UISink = Callable[[Event], None]


class NotApproved(Exception):
    """`approval.json` is missing, or `pam.json`/`assessment.json` changed after approval."""


def new_run_id() -> str:
    """``YYYYMMDD-HHMMSS-<4 hex>``"""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


@dataclass
class RunContext:
    run_id: str
    root: Path               # runs/<run_id>/
    source_dir: Path
    summary: RunSummary
    emitter: Emitter

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


def run_dir(run_id: str) -> Path:
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


def plan(source_dir: Path, *, run_id: str | None = None, force: bool = False,
         emit_extra: UISink | None = None) -> RunContext:
    """Scan + assess + lock-in → status `awaiting_approval`. Writes nothing in `leaseweb/` (S6)."""
    raise NotBuilt("pipeline.plan", owner="C", doc="docs/15 §5.2")


def approve(run_id: str, *, approved_by: str = "cli", emit_extra: UISink | None = None) -> Approval:
    raise NotBuilt("pipeline.approve", owner="C", doc="docs/15 §5.2")


def execute(run_id: str, *, emit_extra: UISink | None = None) -> RunSummary:
    raise NotBuilt("pipeline.execute", owner="C", doc="docs/15 §5.2")


def migrate(source_dir: Path, *, yes: bool = False, run_id: str | None = None, force: bool = False,
            emit_extra: UISink | None = None) -> RunSummary:
    """``plan()`` → ``approve()`` (only when ``yes``) → ``execute()``. The interactive prompt is in cli.py."""
    raise NotBuilt("pipeline.migrate", owner="C", doc="docs/15 §5.2")


def roundtrip(run_id: str, *, emit_extra: UISink | None = None) -> RunSummary:
    raise NotBuilt("pipeline.roundtrip", owner="C", doc="docs/15 §7")


def cleanup(run_id: str) -> None:
    """`compose down -v` for this run's stack."""
    raise NotBuilt("pipeline.cleanup", owner="C", doc="docs/15 §5.2")
