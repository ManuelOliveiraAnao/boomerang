"""S3 → any S3-compatible store (SeaweedFS, Leaseweb Object Storage): a config change. Owner C, task C3.

Spec: docs/02 §5, docs/15 §4.1.

This file is copied into the migrated app (``leaseweb/app/sovereign_sdk/``), so it imports only boto3 and
botocore, never ``boomerang``.

The client is a plain boto3 S3 client, so every S3 call in the app keeps working unchanged:

- ``endpoint_url`` comes from ``S3_ENDPOINT_URL`` and beats any leftover ``AWS_ENDPOINT_URL`` in the env;
- credentials come from ``S3_ACCESS_KEY_ID`` / ``S3_SECRET_ACCESS_KEY`` (never the ``AWS_*`` ones);
- path-style addressing and SigV4, which every S3-compatible store understands;
- ``request_checksum_calculation`` / ``response_checksum_validation`` = ``when_required``: recent boto3
  versions send new default integrity checksums that some S3-compatible stores reject.
"""

from __future__ import annotations

import os

REQUIRED = ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
DEFAULT_REGION = "us-east-1"


def s3_client():
    """boto3 S3 client from S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY (+ S3_REGION), path-style."""
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise KeyError(f"{', '.join(missing)} not set: sovereign_sdk.storage needs "
                       f"{', '.join(REQUIRED)} (and optionally S3_REGION, default {DEFAULT_REGION})")

    import boto3
    from botocore.config import Config

    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"max_attempts": 3},
    )
    # A private session: never the shared default one, and never AWS_* credentials from the environment.
    session = boto3.session.Session(
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"].strip(),
        region_name=os.environ.get("S3_REGION", "").strip() or DEFAULT_REGION,
    )
    return session.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"].strip(), config=config)
