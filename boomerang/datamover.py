"""Data both ways with counts and checksums. Owner A, task A6.

Spec: docs/03 §3.11. Stub created by C1: A replaces the body.
"""

from __future__ import annotations

from typing import Literal

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel, DataMove

Direction = Literal["aws->leaseweb", "leaseweb->aws"]


def move(pam: AppModel, direction: Direction, src_env: dict[str, str], dst_env: dict[str, str],
         emit: Emitter) -> DataMove:
    """Binding (docs/03 §3.11)."""
    raise NotBuilt("datamover.move", owner="A", doc="docs/03 §3.11")
