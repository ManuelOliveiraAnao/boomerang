"""Receipts storage (Amazon S3)."""
import json
import os

import boto3

s3 = boto3.client("s3")


def _bucket() -> str:
    return os.environ["RECEIPTS_BUCKET"]


def put_receipt(order_id: str, receipt: dict) -> str:
    key = f"receipts/{order_id}.json"
    s3.put_object(Bucket=_bucket(), Key=key, Body=json.dumps(receipt).encode("utf-8"),
                  ContentType="application/json")
    return key


def get_receipt(order_id: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=_bucket(), Key=f"receipts/{order_id}.json")
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())
