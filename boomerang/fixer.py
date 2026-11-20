"""Fixer: failing original tests → one AI fix per function (docs/03 §3.9). Owner C, task C5.

Spec: docs/15 §6. Stub until C5.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel, RewriteRecord, TestReport


def fix(app_dir: Path, original_dir: Path, pam: AppModel, report: TestReport, attempt: int,
        rewrites: list[RewriteRecord], diffs_dir: Path, emit: Emitter) -> list[RewriteRecord]:
    """``[]`` = nothing fixed. Never writes under tests/ or sovereign_sdk/."""
    raise NotBuilt("fixer.fix", owner="C", doc="docs/15 §6.1")


def frames(traceback: str) -> list[tuple[str, int, str]]:
    """(file, line, function), outermost → innermost; pytest and classic formats, Windows paths."""
    raise NotBuilt("fixer.frames", owner="C", doc="docs/15 §6.1")
