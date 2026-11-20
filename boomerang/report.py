"""`report.md` for a run (docs/15 §5.3). Owner C, task C4. Stub until C4."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from boomerang._stub import NotBuilt

if TYPE_CHECKING:
    from boomerang.pipeline import RunContext


def write(ctx: RunContext) -> Path:
    raise NotBuilt("report.write", owner="C", doc="docs/15 §5.3")
