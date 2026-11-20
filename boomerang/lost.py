"""What got lost: preserved / changed / lost. Owner B, task B7.

Spec: docs/03 §3.12. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from collections.abc import Iterable

from boomerang.models import AppModel, Assessment, DataMove, LostItem, Scope
from boomerang.scope import normalize_scope


def build(
    pam: AppModel,
    assessment: Assessment,
    data_moves: list[DataMove],
    scope: Scope | Iterable[str] | None = None,
) -> list[LostItem]:
    """Binding (docs/03 §3.12). ``assessment`` includes the de-lock findings (assessment.final.json)."""
    normalized = normalize_scope(pam, scope)
    selfhosted = set(normalized.selfhosted)
    result: list[LostItem] = []
    for finding in assessment.findings:
        movable_component_ids = {
            component.id for component in pam.components
            if component.kind in {"object_store", "kv_table", "queue"}
        }
        if (
            finding.component_id != "hybrid"
            and finding.component_id in movable_component_ids
            and finding.component_id not in selfhosted
        ):
            result.append(LostItem(
                component_id=finding.component_id,
                feature=finding.feature,
                status="preserved",
                detail="Stays on AWS",
            ))
            continue
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
