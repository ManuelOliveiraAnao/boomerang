"""Lock-in score (docs/03 §3.4). Owner B, task B4. Stub created by C1: B replaces the body."""

from __future__ import annotations

from boomerang._stub import NotBuilt
from boomerang.models import AppModel


def score(pam: AppModel) -> int:
    """Weighted by usage count; DynamoDB 90, SQS 70, S3 35, Lambda 95, IAM 80; Leaseweb providers 5–10."""
    raise NotBuilt("lockin.score", owner="B", doc="docs/03 §3.4")
