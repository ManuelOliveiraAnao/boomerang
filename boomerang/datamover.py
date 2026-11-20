"""Copy an app's data between AWS and Leaseweb, then prove it with counts and checksums.

Both sides expose boto3-shaped handles: AWS uses boto3, Leaseweb uses sovereign_sdk adapters
(which mimic boto3). Run with the app stopped (write freeze), see verifier.freeze().
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Literal

import boto3

from .events import Emitter, null_emitter
from .models import AppModel, Component, DataCount, DataMove

Direction = Literal["aws->leaseweb", "leaseweb->aws"]


@dataclass
class Side:
    """Handles for one platform. Each factory returns a boto3-shaped object."""
    name: str
    env: dict[str, str]
    table: Callable[[str], Any]   # name -> object with scan(**kw) and put_item(Item=...)
    s3: Callable[[], Any]         # -> client with list_objects_v2, get_object, put_object
    queue: Callable[[], Any]      # -> client with get_queue_url, receive/send/delete_message
    wants_decimal: bool = True    # DynamoDB rejects floats

    def resource_name(self, c: Component) -> str:
        return self.env.get(c.name_env_var, c.resource_name) if c.name_env_var else c.resource_name


def aws_side(env: dict[str, str]) -> Side:
    kw = dict(endpoint_url=env.get("AWS_ENDPOINT_URL"), region_name=env.get("AWS_REGION", "us-east-1"),
              aws_access_key_id=env.get("AWS_ACCESS_KEY_ID"), aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY"))
    ddb = boto3.resource("dynamodb", **kw)
    return Side("aws", env, table=ddb.Table, s3=lambda: boto3.client("s3", **kw),
                queue=lambda: boto3.client("sqs", **kw), wants_decimal=True)


class MissingAdapter(RuntimeError):
    """A sovereign_sdk adapter the Leaseweb side needs is not in the repo yet."""


def leaseweb_side(env: dict[str, str]) -> Side:
    """Uses the same adapters the migrated app uses (kv: B, storage: C, queue: A)."""
    for k, v in env.items():  # adapters read their connection settings from the environment
        os.environ[k] = v
    from sovereign_sdk import queue
    try:
        from sovereign_sdk import kv
    except ImportError as e:
        raise MissingAdapter("sovereign_sdk.kv (Postgres adapter, task B5) is not available yet") from e
    try:
        from sovereign_sdk.storage import s3_client
    except ImportError:  # fallback until storage.py lands
        def s3_client():
            from botocore.config import Config
            return boto3.client("s3", endpoint_url=env["S3_ENDPOINT_URL"], region_name="us-east-1",
                                aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                                aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                                config=Config(s3={"addressing_style": "path"}))
    store = kv.resource()
    return Side("leaseweb", env, table=store.Table, s3=s3_client, queue=queue.Queue, wants_decimal=True)


# ---------------------------------------------------------------- canonical form
def normalize(value: Any) -> Any:
    """Type-neutral form used for checksums: Decimal/float -> int when integral, else float."""
    if isinstance(value, (Decimal, float)) and not isinstance(value, bool):
        return int(value) if value == int(value) else float(value)
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    return value


def to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    return value


def checksum(items: list[dict], key: str) -> str:
    lines = sorted(json.dumps(normalize(i), sort_keys=True, separators=(",", ":")) for i in items)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _scan_all(table) -> list[dict]:
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _list_keys(s3, bucket: str) -> list[str]:
    keys, kwargs = [], {"Bucket": bucket}
    while True:
        page = s3.list_objects_v2(**kwargs)
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


# ---------------------------------------------------------------- per kind
def move_table(c: Component, src: Side, dst: Side) -> DataCount:
    src_t, dst_t = src.table(src.resource_name(c)), dst.table(dst.resource_name(c))
    items = _scan_all(src_t)
    for item in items:
        dst_t.put_item(Item=to_dynamo(item) if dst.wants_decimal else normalize(item))
    after = _scan_all(dst_t)
    key = c.partition_key.name if c.partition_key else "id"
    src_ids = {str(i[key]) for i in items}
    copied = [i for i in after if str(i.get(key)) in src_ids]
    match = checksum(items, key) == checksum(copied, key)
    return DataCount(component_id=c.id, kind="kv_table", source_count=len(items), target_count=len(after),
                     checksum_match=match,
                     notes=["numbers compared type-neutrally (DynamoDB Decimal vs JSON number)"])


def move_objects(c: Component, src: Side, dst: Side) -> DataCount:
    s, d = src.s3(), dst.s3()
    sb, db = src.resource_name(c), dst.resource_name(c)
    keys = _list_keys(s, sb)
    digests = {}
    for k in keys:
        obj = s.get_object(Bucket=sb, Key=k)
        body = obj["Body"].read()
        digests[k] = hashlib.md5(body).hexdigest()
        extra = {"ContentType": obj["ContentType"]} if obj.get("ContentType") else {}
        if obj.get("Metadata"):
            extra["Metadata"] = obj["Metadata"]
        d.put_object(Bucket=db, Key=k, Body=body, **extra)
    dst_keys = _list_keys(d, db)
    match = all(hashlib.md5(d.get_object(Bucket=db, Key=k)["Body"].read()).hexdigest() == h
                for k, h in digests.items())
    return DataCount(component_id=c.id, kind="object_store", source_count=len(keys), target_count=len(dst_keys),
                     checksum_match=match,
                     notes=["content type and user metadata copied; ACLs, tags, versions and "
                            "event notifications are not"])


def move_queue(c: Component, src: Side, dst: Side) -> DataCount:
    s, d = src.queue(), dst.queue()
    s_url = s.get_queue_url(QueueName=src.resource_name(c))["QueueUrl"]
    d_url = d.get_queue_url(QueueName=dst.resource_name(c))["QueueUrl"]
    received = sent = empty_polls = 0
    bodies_src, bodies_dst = [], []
    while empty_polls < 2:
        msgs = s.receive_message(QueueUrl=s_url, MaxNumberOfMessages=10, WaitTimeSeconds=1,
                                 VisibilityTimeout=60).get("Messages", [])
        if not msgs:
            empty_polls += 1
            continue
        empty_polls = 0
        for m in msgs:
            received += 1
            bodies_src.append(m["Body"])
            d.send_message(QueueUrl=d_url, MessageBody=m["Body"])
            sent += 1
            bodies_dst.append(m["Body"])
            s.delete_message(QueueUrl=s_url, ReceiptHandle=m["ReceiptHandle"])
    match = sorted(bodies_src) == sorted(bodies_dst)
    return DataCount(component_id=c.id, kind="queue", source_count=received, target_count=sent,
                     checksum_match=match,
                     notes=["pending messages moved as new messages: IDs and receive counts are not "
                            "preserved; in-flight messages at cut-over are redelivered later"])


MOVERS = {"kv_table": move_table, "object_store": move_objects, "queue": move_queue}


def move(pam: AppModel, direction: Direction, aws_env: dict[str, str], lw_env: dict[str, str],
         emit: Emitter | None = None, sides: tuple[Side, Side] | None = None) -> DataMove:
    """Copy every data component in `direction`. `sides` overrides (src, dst) for tests."""
    emit = emit or null_emitter()
    if sides is None:
        aws, lw = aws_side(aws_env), leaseweb_side(lw_env)
        sides = (aws, lw) if direction == "aws->leaseweb" else (lw, aws)
    src, dst = sides
    counts: list[DataCount] = []
    for c in pam.components:
        mover = MOVERS.get(c.kind)
        if mover is None:
            continue
        emit("data", f"copying {c.id} ({c.kind}) {direction}")
        try:
            dc = mover(c, src, dst)
        except Exception as e:  # report, don't crash the run
            dc = DataCount(component_id=c.id, kind=c.kind, source_count=0, target_count=0,
                           checksum_match=False, notes=[f"copy failed: {type(e).__name__}: {e}"])
        ok = dc.source_count == dc.target_count and dc.checksum_match is not False
        emit("data", f"{c.id}: {dc.source_count} -> {dc.target_count}, checksum "
                     f"{'OK' if dc.checksum_match else 'MISMATCH'}",
             level="success" if ok else "warning", count=dc.model_dump())
        counts.append(dc)
    return DataMove(direction=direction, counts=counts)
