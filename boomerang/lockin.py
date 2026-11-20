from __future__ import annotations

from typing import Any


WEIGHTS = {
    "aws.dynamodb": 90,
    "aws.sqs": 70,
    "aws.s3": 35,
    "aws.lambda": 95,
    "aws.iam": 80,
}


def score(pam: Any) -> int:
    """Calculate a weighted AWS lock-in score from PAM component usage."""
    pam_dict = pam.model_dump() if hasattr(pam, "model_dump") else pam
    total = 0
    for component in pam_dict.get("components", []):
        provider = component.get("provider", "")
        usage_count = max(1, len(component.get("usages", [])))
        total += WEIGHTS.get(provider, 10) * usage_count
    return total
