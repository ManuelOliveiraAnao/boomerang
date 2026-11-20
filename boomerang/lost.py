from __future__ import annotations

from typing import Any


def _model_to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def build(pam: Any, assessment: Any, data_moves: list[Any] | Any = None) -> list[dict]:
    """Build a preserved/changed/lost report from findings and data moves."""
    pam_dict = _model_to_dict(pam)
    assessment_dict = _model_to_dict(assessment)
    result: list[dict] = []

    for finding in assessment_dict.get("findings", []):
        result.append(
            {
                "component_id": finding["component_id"],
                "feature": finding["feature"],
                "status": "lost" if finding["light"] == "red" else "changed" if finding["light"] == "amber" else "preserved",
                "detail": finding["what_changes"],
            }
        )

    component_ids = {component["id"] for component in pam_dict.get("components", [])}
    covered_ids = {item["component_id"] for item in result}
    for component_id in sorted(component_ids - covered_ids):
        result.append(
            {
                "component_id": component_id,
                "feature": "component",
                "status": "preserved",
                "detail": "No migration-specific finding was recorded.",
            }
        )

    if data_moves is not None:
        moves = _model_to_dict(data_moves)
        if isinstance(moves, dict):
            moves = [moves]
        for move in moves:
            move_dict = _model_to_dict(move)
            for count in move_dict.get("counts", []):
                if count.get("notes"):
                    result.append(
                        {
                            "component_id": count["component_id"],
                            "feature": "data",
                            "status": "changed",
                            "detail": "; ".join(count["notes"]),
                        }
                    )
    return result
