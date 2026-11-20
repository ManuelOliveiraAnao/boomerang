"""Receipts in S3 (ShopLite shape): a config change on Leaseweb."""
import json
import os

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
BUCKET = os.environ.get("RECEIPTS_BUCKET", "receipts")


def put_receipt(order):
    body = json.dumps(order).encode("utf-8")
    s3.put_object(Bucket=BUCKET, Key=f"receipts/{order['order_id']}.json", Body=body)


def get_receipt(order_id):
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"receipts/{order_id}.json")
    except ClientError:
        return None
    return json.loads(obj["Body"].read())
