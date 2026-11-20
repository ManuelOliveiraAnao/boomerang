"""Traffic lights: rules decide, the AI explains (docs/02 §4). Owner B, task B4.

Spec: docs/03 §3.4. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel, Assessment


def assess(pam: AppModel, emit: Emitter) -> Assessment:
    """Binding (docs/03 §3.4)."""
    raise NotBuilt("assess.assess", owner="B", doc="docs/03 §3.4")
