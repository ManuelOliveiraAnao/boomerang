"""The lab: fake AWS (moto) on 127.0.0.1:4566. Owner A, task A1.

Spec: docs/07 (A1), docs/15 L5. Stub created by C1: A replaces the bodies; signatures are proposed.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt


def up() -> None:
    """``docker compose -f infra/lab.compose.yml up -d --wait`` (list args)."""
    raise NotBuilt("lab.up", owner="A", doc="docs/07 A1")


def down() -> None:
    raise NotBuilt("lab.down", owner="A", doc="docs/07 A1")


def status() -> bool:
    """True when moto answers on 127.0.0.1:4566."""
    raise NotBuilt("lab.status", owner="A", doc="docs/07 A1")


def reset() -> None:
    """Wipe all moto state (``POST /moto-api/reset``), used by the baseline and the round trip (L5)."""
    raise NotBuilt("lab.reset", owner="A", doc="docs/15 L5")
