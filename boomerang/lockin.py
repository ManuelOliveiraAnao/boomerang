"""Lock-in score (docs/03 §3.4). Owner B, task B4. Stub created by C1: B replaces the body."""

from __future__ import annotations

from boomerang.models import AppModel


def score(pam: AppModel) -> int:
    """Weighted by usage count; DynamoDB 90, SQS 70, S3 35, Lambda 95, IAM 80."""
    weights = {
        "aws.dynamodb": 90,
        "aws.sqs": 70,
        "aws.s3": 35,
        "aws.lambda": 95,
        "aws.iam": 80,
        "aws.cloudwatch": 10,
    }
    return sum(
        weights.get(component.provider, 10) * max(1, len(component.usages))
        for component in pam.components
    )
