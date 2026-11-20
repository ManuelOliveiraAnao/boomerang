"""What got lost: preserved / changed / lost. Owner B, task B7.

Spec: docs/03 §3.12. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from boomerang.models import AppModel, Assessment, DataMove, LostItem


def build(pam: AppModel, assessment: Assessment, data_moves: list[DataMove]) -> list[LostItem]:
    """Binding (docs/03 §3.12). ``assessment`` includes the de-lock findings (assessment.final.json)."""
    result: list[LostItem] = []
    for finding in assessment.findings:
        status = "lost" if finding.light == "red" else "changed" if finding.light == "amber" else "preserved"
        result.append(LostItem(
            component_id=finding.component_id,
            feature=finding.feature,
            status=status,
            detail=finding.what_changes,
        ))

    covered = {item.component_id for item in result}
    for component in pam.components:
        if component.id not in covered:
            result.append(LostItem(
                component_id=component.id,
                feature="component",
                status="preserved",
                detail="No migration-specific finding was recorded.",
            ))

    for move in data_moves:
        for count in move.counts:
            if count.notes:
                result.append(LostItem(
                    component_id=count.component_id,
                    feature="data",
                    status="changed",
                    detail="; ".join(count.notes),
                ))
    return result
