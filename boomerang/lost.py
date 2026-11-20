"""What got lost: preserved / changed / lost. Owner B, task B7.

Spec: docs/03 §3.12. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt
from boomerang.models import AppModel, Assessment, DataMove, LostItem


def build(pam: AppModel, assessment: Assessment, data_moves: list[DataMove]) -> list[LostItem]:
    """Binding (docs/03 §3.12). ``assessment`` includes the de-lock findings (assessment.final.json)."""
    raise NotBuilt("lost.build", owner="B", doc="docs/03 §3.12")
