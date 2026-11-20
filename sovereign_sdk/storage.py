"""S3 → any S3-compatible store (SeaweedFS, Leaseweb Object Storage): a config change. Owner C, task C3.

Spec: docs/02 §5, docs/15 §4.1. Stub until C3.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt


def s3_client():
    """boto3 S3 client from S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY (+ S3_REGION), path-style."""
    raise NotBuilt("sovereign_sdk.storage.s3_client", owner="C", doc="docs/15 §4.1")
