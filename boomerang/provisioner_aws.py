"""Create an app's AWS resources in the fake AWS (moto) from its PAM, and seed demo data. Owner A, task A3.

Contract with C (docs/15 L5/L17): provision(pam, emit) and seed(pam, count, emit). Seeding uses plain
boto3: the engine never imports the scanned app.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from . import lab
from .models import AppModel, Component

NAMES_FILE = lab.STATE_DIR / "aws_names.json"  # last provisioned env vars -> name (read by verifier.aws_env)


def _emit(emit, message: str, level: str = "info", **data) -> None:
    if emit is not None:
        emit.emit("verify", message, level, step="provision", **data)


def _clients(env: dict[str, str]):
    kw = dict(endpoint_url=env["AWS_ENDPOINT_URL"], region_name=env["AWS_REGION"],
              aws_access_key_id=env["AWS_ACCESS_KEY_ID"], aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"])
    return boto3.client("s3", **kw), boto3.client("dynamodb", **kw), boto3.client("sqs", **kw), boto3.resource("dynamodb", **kw)


def resource_names(pam: AppModel, run_tag: str | None = None) -> dict[str, str]:
    """component_id -> physical name (optionally suffixed to isolate runs)."""
    names = {}
    for c in pam.components:
        if c.kind in ("object_store", "kv_table", "queue"):
            if not run_tag:
                names[c.id] = c.resource_name
            elif c.resource_name.endswith(".fifo"):
                names[c.id] = f"{c.resource_name[:-5]}-{run_tag}.fifo"
            else:
                names[c.id] = f"{c.resource_name}-{run_tag}"
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
    if c.fifo:
        if not name.endswith(".fifo"):
            raise ValueError(f"FIFO queue name must end with .fifo: {name}")
        attributes["FifoQueue"] = "true"
    if c.visibility_timeout_s:
        attributes["VisibilityTimeout"] = str(c.visibility_timeout_s)
    if c.max_receive_count:
        dlq = f"{name[:-5]}-dlq.fifo" if c.fifo else f"{name}-dlq"
        dlq_url = sqs.create_queue(QueueName=dlq, Attributes={"FifoQueue": "true"} if c.fifo else {})["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        attributes["RedrivePolicy"] = json.dumps({"deadLetterTargetArn": dlq_arn,
                                                  "maxReceiveCount": str(c.max_receive_count)})
    sqs.create_queue(QueueName=name, Attributes=attributes)


def provision(pam: AppModel, emit=None, *, run_tag: str | None = None) -> dict[str, str]:
    """Create bucket/table/queue components in moto. Idempotent. Returns the app's env
    (C's contract only needs the side effect; the return value is a convenience)."""
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
    NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAMES_FILE.write_text(json.dumps({k: v for k, v in env.items() if k not in lab.aws_env()}, indent=2),
                          encoding="utf-8")
    _emit(emit, f"fake AWS ready: {', '.join(names.values())}", "success", names=names)
    return env


def last_names() -> dict[str, str]:
    """Name env vars of the last provision() (e.g. ORDERS_TABLE=orders)."""
    try:
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def seed(pam: AppModel, count: int = 25, emit=None, *, pending: int = 3) -> dict[str, int]:
    """`count` ShopLite orders with receipts; `pending` of them still have a message in the queue."""
    env = app_env(pam, resource_names(pam))
    counts = seed_shoplite(env, n_orders=count, pending=pending)
    _emit(emit, f"seeded {counts}", "success", counts=counts)
    return counts


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


def _queue_name(c: Component, env: dict[str, str]) -> str:
    return env.get(c.name_env_var, c.resource_name) if c.name_env_var else c.resource_name


def _drain_queue(sqs, url: str) -> list[str]:
    bodies: list[str] = []
    empty_polls = 0
    while empty_polls < 2:
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0,
                                   VisibilityTimeout=60).get("Messages", [])
        if not msgs:
            empty_polls += 1
            continue
        empty_polls = 0
        for m in msgs:
            bodies.append(m["Body"])
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
    return bodies


def snapshot_queues(pam: AppModel, queue_ids: list[str], env: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Drain and return pending messages from the named SQS queues so a hybrid verify run
    neither fails on stale seeded messages nor loses them before a later move-over."""
    resolved = env or app_env(pam, resource_names(pam))
    _, _, sqs, _ = _clients(resolved)
    wanted = set(queue_ids)
    snapshot: dict[str, list[str]] = {}
    for c in pam.components:
        if c.kind != "queue" or c.id not in wanted:
            continue
        url = sqs.get_queue_url(QueueName=_queue_name(c, resolved))["QueueUrl"]
        snapshot[c.id] = _drain_queue(sqs, url)
    return snapshot


def restore_queues(pam: AppModel, snapshot: dict[str, list[str]], env: dict[str, str] | None = None) -> dict[str, int]:
    """Empty any test messages left in the snapshotted SQS queues and re-send the original bodies."""
    resolved = env or app_env(pam, resource_names(pam))
    _, _, sqs, _ = _clients(resolved)
    restored: dict[str, int] = {}
    for c in pam.components:
        if c.kind != "queue" or c.id not in snapshot:
            continue
        url = sqs.get_queue_url(QueueName=_queue_name(c, resolved))["QueueUrl"]
        _drain_queue(sqs, url)
        bodies = snapshot[c.id]
        for idx, body in enumerate(bodies):
            kwargs: dict[str, str] = {"QueueUrl": url, "MessageBody": body}
            if c.fifo:
                kwargs["MessageGroupId"] = "boomerang-restore"
                kwargs["MessageDeduplicationId"] = f"restore-{c.id}-{idx}-{uuid.uuid4().hex[:8]}"
            sqs.send_message(**kwargs)
        restored[c.id] = len(bodies)
    return restored
