"""Create an app's AWS resources in the fake AWS (moto) from its PAM, and seed demo data."""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from . import lab
from .models import AppModel, Component


def _clients(env: dict[str, str]):
    kw = dict(endpoint_url=env["AWS_ENDPOINT_URL"], region_name=env["AWS_REGION"],
              aws_access_key_id=env["AWS_ACCESS_KEY_ID"], aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"])
    return boto3.client("s3", **kw), boto3.client("dynamodb", **kw), boto3.client("sqs", **kw), boto3.resource("dynamodb", **kw)


def resource_names(pam: AppModel, run_tag: str | None = None) -> dict[str, str]:
    """component_id -> physical name (optionally suffixed to isolate runs)."""
    names = {}
    for c in pam.components:
        if c.kind in ("object_store", "kv_table", "queue"):
            names[c.id] = f"{c.resource_name}-{run_tag}" if run_tag else c.resource_name
    return names


def app_env(pam: AppModel, names: dict[str, str]) -> dict[str, str]:
    env = lab.aws_env()
    for c in pam.components:
        if c.id in names and c.name_env_var:
            env[c.name_env_var] = names[c.id]
    return env


def _create_table(ddb, c: Component, name: str) -> None:
    attrs = {c.partition_key.name: c.partition_key.type}
    key_schema = [{"AttributeName": c.partition_key.name, "KeyType": "HASH"}]
    if c.sort_key:
        attrs[c.sort_key.name] = c.sort_key.type
        key_schema.append({"AttributeName": c.sort_key.name, "KeyType": "RANGE"})
    gsis = []
    for ix in c.indexes:
        attrs[ix.partition_key.name] = ix.partition_key.type
        ks = [{"AttributeName": ix.partition_key.name, "KeyType": "HASH"}]
        if ix.sort_key:
            attrs[ix.sort_key.name] = ix.sort_key.type
            ks.append({"AttributeName": ix.sort_key.name, "KeyType": "RANGE"})
        gsis.append({"IndexName": ix.name, "KeySchema": ks, "Projection": {"ProjectionType": "ALL"}})
    kwargs = dict(TableName=name, BillingMode="PAY_PER_REQUEST", KeySchema=key_schema,
                  AttributeDefinitions=[{"AttributeName": k, "AttributeType": t} for k, t in attrs.items()])
    if gsis:
        kwargs["GlobalSecondaryIndexes"] = gsis
    try:
        ddb.create_table(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
    ddb.get_waiter("table_exists").wait(TableName=name)


def _create_queue(sqs, c: Component, name: str) -> None:
    attributes = {}
    if c.visibility_timeout_s:
        attributes["VisibilityTimeout"] = str(c.visibility_timeout_s)
    if c.max_receive_count:
        dlq_url = sqs.create_queue(QueueName=f"{name}-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        attributes["RedrivePolicy"] = json.dumps({"deadLetterTargetArn": dlq_arn,
                                                  "maxReceiveCount": str(c.max_receive_count)})
    sqs.create_queue(QueueName=name, Attributes=attributes)


def provision(pam: AppModel, *, run_tag: str | None = None) -> dict[str, str]:
    """Create bucket/table/queue components in moto. Idempotent. Returns the app's env."""
    names = resource_names(pam, run_tag)
    env = app_env(pam, names)
    s3, ddb, sqs, _ = _clients(env)
    for c in pam.components:
        if c.id not in names:
            continue  # lambda / iam / alarms are never created: they are flagged, not migrated
        name = names[c.id]
        if c.kind == "object_store":
            try:
                s3.create_bucket(Bucket=name)
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
        elif c.kind == "kv_table":
            _create_table(ddb, c, name)
        elif c.kind == "queue":
            _create_queue(sqs, c, name)
    return env


def seed_shoplite(env: dict[str, str], n_orders: int = 25, pending: int = 3, rng_seed: int = 7) -> dict[str, int]:
    """Deterministic ShopLite demo data written straight to AWS (same item shape the app writes)."""
    s3, _, sqs, ddb_res = _clients(env)
    rng = random.Random(rng_seed)
    table = ddb_res.Table(env["ORDERS_TABLE"])
    queue_url = sqs.get_queue_url(QueueName=env["ORDER_EVENTS_QUEUE"])["QueueUrl"]
    customers = [f"cust-{i:02d}" for i in range(1, 6)]
    start = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    for i in range(n_orders):
        order_id = uuid.UUID(int=rng.getrandbits(128)).hex
        items = [{"sku": f"SKU-{rng.randint(1, 40):03d}", "qty": rng.randint(1, 4),
                  "price_cents": rng.choice([499, 1250, 2999, 8900])} for _ in range(rng.randint(1, 3))]
        is_pending = i >= n_orders - pending
        created = start + timedelta(hours=i * 7)
        order = {"order_id": order_id, "customer_id": rng.choice(customers), "items": items,
                 "total_cents": sum(it["qty"] * it["price_cents"] for it in items),
                 "status": "pending" if is_pending else "processed",
                 "created_at": created.isoformat()}
        if not is_pending:
            order["processed_at"] = (created + timedelta(minutes=2)).isoformat()
        table.put_item(Item=order)
        receipt = {k: order[k] for k in ("order_id", "customer_id", "items", "total_cents")}
        s3.put_object(Bucket=env["RECEIPTS_BUCKET"], Key=f"receipts/{order_id}.json",
                      Body=json.dumps(receipt).encode("utf-8"), ContentType="application/json")
        if is_pending:
            sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"order_id": order_id}))
    return counts_shoplite(env)


def counts_shoplite(env: dict[str, str]) -> dict[str, int]:
    s3, _, sqs, ddb_res = _clients(env)
    table = ddb_res.Table(env["ORDERS_TABLE"])
    n_items, kwargs = 0, {"Select": "COUNT"}
    while True:
        page = table.scan(**kwargs)
        n_items += page["Count"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    n_objects = sum(p.get("KeyCount", 0) for p in
                    s3.get_paginator("list_objects_v2").paginate(Bucket=env["RECEIPTS_BUCKET"]))
    url = sqs.get_queue_url(QueueName=env["ORDER_EVENTS_QUEUE"])["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages",
                                                                    "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    n_msgs = int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"])
    return {"orders": n_items, "receipts": n_objects, "order_events": n_msgs}
