from __future__ import annotations

from typing import Any


RULES = [
    ("object_store", "put", "green", "S3-compatible API; endpoint and keys change only"),
    ("object_store", "get", "green", "S3-compatible API; endpoint and keys change only"),
    ("object_store", "event_notification", "red", "S3-to-Lambda notification has no equivalent wired up"),
    ("kv_table", "conditional_write", "amber", "Only attribute_not_exists and attribute_exists are supported"),
    ("kv_table", "gsi", "amber", "Postgres adapter uses a generated index for the GSI"),
    ("kv_table", "transactions", "red", "Transactions are not supported"),
    ("kv_table", "streams", "red", "DynamoDB streams are not supported"),
    ("kv_table", "ttl", "red", "DynamoDB TTL is not supported"),
    ("queue", "send", "amber", "RabbitMQ requires the queue adapter"),
    ("queue", "receive", "amber", "Visibility timeout is emulated by redelivery"),
    ("queue", "delete", "amber", "RabbitMQ acknowledgement differs from SQS deletion"),
    ("queue", "visibility_timeout", "amber", "RabbitMQ redelivery emulates visibility timeout"),
    ("queue", "dlq", "amber", "RabbitMQ dead-letter exchange emulates the DLQ"),
    ("queue", "fifo", "red", "SQS FIFO is not supported"),
    ("function", "*", "red", "Lambda functions are not migrated"),
    ("identity", "*", "red", "IAM credentials are replaced with scoped credentials"),
    ("monitoring", "*", "red", "CloudWatch alarms are not migrated"),
]


def _model_to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def assess(pam: Any, emit: Any = None) -> dict:
    """Build deterministic traffic-light findings for a Portable App Model."""
    pam_dict = _model_to_dict(pam)
    findings: list[dict] = []

    for component in pam_dict.get("components", []):
        features = list(component.get("features", []))
        if not features:
            features = ["*"]
        for feature in features:
            matches = [
                rule
                for rule in RULES
                if rule[0] == component["kind"] and rule[1] in {feature, "*"}
            ]
            if not matches:
                continue
            light, what_changes = matches[0][2], matches[0][3]
            findings.append(
                {
                    "analysis": what_changes,
                    "component_id": component["id"],
                    "feature": feature,
                    "light": light,
                    "what_changes": what_changes,
                    "evidence": component.get("usages", []),
                }
            )

    severity = {"green": 0, "amber": 1, "red": 2}
    overall = max(
        (finding["light"] for finding in findings),
        key=lambda light: severity[light],
        default="green",
    )
    result = {
        "findings": findings,
        "overall": overall,
        "summary": f"{len(findings)} migration findings",
    }
    if emit is not None:
        emit("assess", "Traffic-light assessment completed", "success", findings=len(findings))
    return result
