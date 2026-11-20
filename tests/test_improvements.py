"""Extensive tests for the 4 reliability & efficiency improvements:

1. sovereign_sdk.kv: dynamic schema env/caching, composite PK & update_item ConditionExpression,
   Decimal decoding for JSONB dicts, and generator BOOMERANG_KV_SCHEMA + secret flags.
2. scanner.scan_ai & assess.assess: graceful deterministic fallback when Ollama is unavailable,
   plus static Terraform metadata preservation when AI drops fields.
3. provisioner_aws & pipeline: preserving kept AWS SQS queue messages across hybrid verification.
4. delock & pipeline: per-file relevant `kept` prompts and run-local `delock-cache.json` reuse
   across move-over / move-back scope steps.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from boomerang import assess, delock, llm as llm_mod, pipeline, provisioner_aws, rules, verifier
from boomerang.config import get_settings
from boomerang.events import Emitter, read_events
from boomerang.generators import leaseweb as leaseweb_gen
from boomerang.models import (
    AppModel,
    Assessment,
    CodeRef,
    Component,
    Finding,
    FunctionRewrite,
    Index,
    KeyAttr,
)
from boomerang.scanner import ai as scanner_ai
from boomerang.scanner.static import scan_static
from sovereign_sdk.kv import (
    ConditionalCheckFailedException,
    KV,
    Table,
    UnsupportedFeature,
    _decode_item,
)
from tests.test_partial_migration import Moves, moves  # noqa: F401 - fixtures
from tests.test_pipeline import World, llm, root_of, source, verification, world  # noqa: F401 - fixtures

SHOPLITE = Path(__file__).resolve().parents[1] / "sample-app" / "aws"


# ============================================================================
# Fake in-memory Postgres for deterministic, zero-skip sovereign_sdk.kv tests
# ============================================================================

class _FakeCursor:
    def __init__(self, conn: "_FakePgConnection") -> None:
        self.conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        norm = " ".join(sql.split())
        self.conn.queries.append((norm, params))
        if "FROM pg_index" in norm:
            table_name = params[0]
            self._rows = [(col,) for col in self.conn.pk_cols[table_name]]
            return
        if "FROM information_schema.columns" in norm:
            table_name = params[0]
            self._rows = [(col,) for col in self.conn.all_cols[table_name]]
            return
        if norm.startswith("SELECT data FROM "):
            table_name = norm.split('"')[1]
            rows = self.conn.tables.setdefault(table_name, {})
            if " WHERE " not in norm:
                self._rows = [(dict(v),) for v in rows.values()]
                return
            pk_cols = self.conn.pk_cols[table_name]
            key_tuple = tuple(str(p) for p in params)
            if len(key_tuple) == len(pk_cols) and key_tuple in rows:
                self._rows = [(dict(rows[key_tuple]),)]
            else:
                self._rows = []
            return
        if norm.startswith("INSERT INTO "):
            table_name = norm.split('"')[1]
            pk_cols = self.conn.pk_cols[table_name]
            key_tuple = tuple(str(params[i]) for i in range(len(pk_cols)))
            # Simulate psycopg v3 JSONB returning plain Python dict (int/float, not Decimal)
            doc = json.loads(params[-1])
            self.conn.tables.setdefault(table_name, {})[key_tuple] = doc
            self._rows = []
            return
        if norm.startswith("DELETE FROM "):
            table_name = norm.split('"')[1]
            key_tuple = tuple(str(p) for p in params)
            self.conn.tables.setdefault(table_name, {}).pop(key_tuple, None)
            self._rows = []
            return
        raise AssertionError(f"Unexpected SQL in FakeCursor: {norm}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakePgConnection:
    def __init__(self, pk_cols: dict[str, list[str]], all_cols: dict[str, list[str]]) -> None:
        self.pk_cols = pk_cols
        self.all_cols = all_cols
        self.tables: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
        self.queries: list[tuple[str, Any]] = []
        self.closed = False
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def _fake_kv_table(
    table_name: str,
    pk_cols: list[str],
    extra_cols: list[str] | None = None,
) -> tuple[Table, _FakePgConnection]:
    all_cols = list(pk_cols) + list(extra_cols or [])
    conn = _FakePgConnection({table_name: pk_cols}, {table_name: all_cols})
    kv = KV("postgresql://fake/test")
    kv.connection = conn
    return kv.Table(table_name), conn


# ============================================================================
# 1. sovereign_sdk.kv & Leaseweb generator improvements
# ============================================================================

def test_kv_decode_item_converts_jsonb_dicts_and_nested_numbers_to_decimal():
    raw_dict = {
        "user_id": "u-1",
        "count": 42,
        "price": 19.95,
        "active": True,
        "nested": {"qty": 3, "discount": 0.25, "flag": False},
        "items": [{"cents": 1250, "weight": 1.5}],
    }
    decoded_from_dict = _decode_item(raw_dict)
    decoded_from_str = _decode_item(json.dumps(raw_dict))

    for decoded in (decoded_from_dict, decoded_from_str):
        assert isinstance(decoded["count"], Decimal) and decoded["count"] == Decimal("42")
        assert isinstance(decoded["price"], Decimal) and decoded["price"] == Decimal("19.95")
        assert decoded["active"] is True and isinstance(decoded["active"], bool)
        assert isinstance(decoded["nested"]["qty"], Decimal)
        assert isinstance(decoded["nested"]["discount"], Decimal) and decoded["nested"]["discount"] == Decimal("0.25")
        assert decoded["nested"]["flag"] is False and isinstance(decoded["nested"]["flag"], bool)
        assert isinstance(decoded["items"][0]["cents"], Decimal)
        assert isinstance(decoded["items"][0]["weight"], Decimal)


def test_kv_env_schema_avoids_catalog_queries_and_supports_arbitrary_primary_keys(monkeypatch):
    schema = {
        "users": {
            "partition_key": "user_id",
            "sort_key": "tenant_id",
            "columns": ["user_id", "tenant_id", "email"],
        }
    }
    monkeypatch.setenv("BOOMERANG_KV_SCHEMA", json.dumps(schema))
    table, conn = _fake_kv_table("users", ["user_id", "tenant_id"], ["email"])

    table.put_item(
        {"user_id": "u1", "tenant_id": "t1", "email": "a@example.com", "score": 100},
        ConditionExpression="attribute_not_exists(user_id)",
    )
    got = table.get_item({"user_id": "u1", "tenant_id": "t1"})["Item"]
    assert got["email"] == "a@example.com"
    assert isinstance(got["score"], Decimal) and got["score"] == Decimal("100")

    table.update_item(
        {"user_id": "u1", "tenant_id": "t1"},
        "SET email = :e",
        ExpressionAttributeValues={":e": "b@example.com"},
        ConditionExpression="attribute_exists(user_id)",
    )
    assert table.get_item({"user_id": "u1", "tenant_id": "t1"})["Item"]["email"] == "b@example.com"
    table.delete_item({"user_id": "u1", "tenant_id": "t1"})
    assert table.get_item({"user_id": "u1", "tenant_id": "t1"}) == {}

    catalog_queries = [q for q, _ in conn.queries if "pg_index" in q or "information_schema" in q]
    assert catalog_queries == []


def test_kv_schema_cache_queries_postgres_catalog_only_once_without_env_schema(monkeypatch):
    monkeypatch.delenv("BOOMERANG_KV_SCHEMA", raising=False)
    table, conn = _fake_kv_table("events", ["account_id", "event_id"], ["status"])

    for i in range(4):
        table.put_item({"account_id": "acct-1", "event_id": f"ev-{i}", "status": "new"})
        assert table.get_item({"account_id": "acct-1", "event_id": f"ev-{i}"})["Item"]["status"] == "new"
        table.update_item(
            {"account_id": "acct-1", "event_id": f"ev-{i}"},
            "SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "done"},
            ConditionExpression="attribute_exists(account_id)",
        )
        table.delete_item({"account_id": "acct-1", "event_id": f"ev-{i}"})

    pg_index_hits = [q for q, _ in conn.queries if "FROM pg_index" in q]
    info_schema_hits = [q for q, _ in conn.queries if "FROM information_schema.columns" in q]
    assert len(pg_index_hits) == 1
    assert len(info_schema_hits) == 1

    table.invalidate_schema_cache()
    table.get_item({"account_id": "acct-1", "event_id": "ev-0"})
    assert len([q for q, _ in conn.queries if "FROM pg_index" in q]) == 2


def test_kv_put_item_condition_expression_respects_composite_primary_key(monkeypatch):
    monkeypatch.delenv("BOOMERANG_KV_SCHEMA", raising=False)
    table, _ = _fake_kv_table("events", ["account_id", "event_id"])

    # Same partition_key ("a1"), different sort_keys ("e1" vs "e2") must both succeed with attribute_not_exists
    table.put_item(
        {"account_id": "a1", "event_id": "e1", "payload": "first"},
        ConditionExpression="attribute_not_exists(account_id)",
    )
    table.put_item(
        {"account_id": "a1", "event_id": "e2", "payload": "second"},
        ConditionExpression="attribute_not_exists(account_id)",
    )
    assert table.get_item({"account_id": "a1", "event_id": "e1"})["Item"]["payload"] == "first"
    assert table.get_item({"account_id": "a1", "event_id": "e2"})["Item"]["payload"] == "second"

    # Duplicate (a1, e1) must fail attribute_not_exists
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item(
            {"account_id": "a1", "event_id": "e1", "payload": "dup"},
            ConditionExpression="attribute_not_exists(account_id)",
        )

    # attribute_exists on existing sort key succeeds; on missing composite key fails
    table.put_item(
        {"account_id": "a1", "event_id": "e1", "payload": "updated", "tag": "v2"},
        ConditionExpression="attribute_exists(event_id)",
    )
    assert table.get_item({"account_id": "a1", "event_id": "e1"})["Item"]["payload"] == "updated"
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item(
            {"account_id": "a1", "event_id": "e999", "payload": "missing"},
            ConditionExpression="attribute_exists(account_id)",
        )

    # Non-key attribute check inspects JSONB fields
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item(
            {"account_id": "a1", "event_id": "e1", "payload": "blocked"},
            ConditionExpression="attribute_not_exists(tag)",
        )
    table.put_item(
        {"account_id": "a1", "event_id": "e1", "payload": "allowed"},
        ConditionExpression="attribute_exists(tag)",
    )

    # Unsupported syntax raises UnsupportedFeature
    with pytest.raises(UnsupportedFeature):
        table.put_item(
            {"account_id": "a1", "event_id": "e3"},
            ConditionExpression="begins_with(account_id, 'a')",
        )


def test_kv_update_item_evaluates_condition_expression_and_aliases(monkeypatch):
    monkeypatch.delenv("BOOMERANG_KV_SCHEMA", raising=False)
    table, _ = _fake_kv_table("accounts", ["account_id"], ["status"])

    # attribute_exists on missing item must fail
    with pytest.raises(ConditionalCheckFailedException):
        table.update_item(
            {"account_id": "a-missing"},
            "SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "active"},
            ConditionExpression="attribute_exists(account_id)",
        )

    # attribute_not_exists on missing item succeeds (conditional upsert)
    table.update_item(
        {"account_id": "a-1"},
        "SET #s = :s",
        ExpressionAttributeNames={"#s": "status", "#pk": "account_id"},
        ExpressionAttributeValues={":s": "created"},
        ConditionExpression="attribute_not_exists(#pk)",
    )
    assert table.get_item({"account_id": "a-1"})["Item"]["status"] == "created"

    # attribute_not_exists on existing item must fail
    with pytest.raises(ConditionalCheckFailedException):
        table.update_item(
            {"account_id": "a-1"},
            "SET #s = :s",
            ExpressionAttributeNames={"#s": "status", "#pk": "account_id"},
            ExpressionAttributeValues={":s": "overwrite"},
            ConditionExpression="attribute_not_exists(#pk)",
        )

    # attribute_exists with custom alias succeeds
    table.update_item(
        {"account_id": "a-1"},
        "SET #s = :s",
        ExpressionAttributeNames={"#s": "status", "#id": "account_id"},
        ExpressionAttributeValues={":s": "verified"},
        ConditionExpression="attribute_exists(#id)",
    )
    assert table.get_item({"account_id": "a-1"})["Item"]["status"] == "verified"

    # Unsupported ConditionExpression must raise UnsupportedFeature (not be silently ignored)
    with pytest.raises(UnsupportedFeature):
        table.update_item(
            {"account_id": "a-1"},
            "SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "bad"},
            ConditionExpression="size(status) > :n",
        )

    # Unknown condition alias raises UnsupportedFeature
    with pytest.raises(UnsupportedFeature):
        table.update_item(
            {"account_id": "a-1"},
            "SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "bad"},
            ConditionExpression="attribute_exists(#nope)",
        )

    # No ConditionExpression on missing item upserts cleanly (DynamoDB semantics)
    table.update_item(
        {"account_id": "a-2"},
        "SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "upserted"},
    )
    assert table.get_item({"account_id": "a-2"})["Item"]["status"] == "upserted"


def test_kv_public_signatures_preserved_for_rules_engine():
    rules.adapter_api.cache_clear()
    api = rules.adapter_api()
    assert list(api["kv.Table"]["get_item"].parameters) == ["self", "Key"]
    assert list(api["kv.Table"]["put_item"].parameters) == ["self", "Item", "ConditionExpression"]
    assert list(api["kv.Table"]["update_item"].parameters) == [
        "self", "Key", "UpdateExpression", "ExpressionAttributeNames",
        "ExpressionAttributeValues", "ConditionExpression",
    ]
    assert list(api["kv.Table"]["query"].parameters) == ["self", "KeyConditionExpression", "IndexName"]


def test_generator_emits_kv_schema_and_marks_secret_json_files(tmp_path):
    pam = AppModel(
        app_name="CustomApp",
        platform="aws",
        entrypoints=["app.main:app"],
        components=[
            Component(
                id="events",
                kind="kv_table",
                provider="aws.dynamodb",
                resource_name="app_events",
                name_env_var="EVENTS_TABLE",
                partition_key=KeyAttr(name="tenant_id", type="S"),
                sort_key=KeyAttr(name="event_id", type="S"),
                indexes=[Index(name="by_type", partition_key=KeyAttr(name="event_type", type="S"))],
            ),
            Component(
                id="receipts",
                kind="object_store",
                provider="aws.s3",
                resource_name="receipts",
                name_env_var="RECEIPTS_BUCKET",
            ),
            Component(
                id="jobs",
                kind="queue",
                provider="aws.sqs",
                resource_name="jobs",
                name_env_var="JOBS_QUEUE",
            ),
        ],
        files=[],
        env=[],
    )
    files = leaseweb_gen.render(pam, leaseweb_gen.make_secrets(pam))
    by_path = {f.path: f for f in files}

    # Secret files marked secret=True (chmod 600)
    assert by_path["secrets.env"].secret is True
    assert by_path["laptop.env"].secret is True
    assert by_path["rabbitmq/definitions.json"].secret is True
    assert by_path["seaweedfs/s3.json"].secret is True
    assert by_path["docker-compose.yml"].secret is False

    # BOOMERANG_KV_SCHEMA in laptop.env parses via verifier.read_env_file
    laptop_path = tmp_path / "laptop.env"
    laptop_path.write_text(by_path["laptop.env"].content, encoding="utf-8")
    parsed_env = verifier.read_env_file(laptop_path)
    kv_schema = json.loads(parsed_env["BOOMERANG_KV_SCHEMA"])
    assert kv_schema == {
        "app_events": {
            "partition_key": "tenant_id",
            "sort_key": "event_id",
            "columns": ["tenant_id", "event_id", "event_type"],
        }
    }

    # BOOMERANG_KV_SCHEMA in docker-compose.yml parses cleanly for api & worker
    compose = yaml.safe_load(by_path["docker-compose.yml"].content)
    for svc in ("api", "worker"):
        assert json.loads(compose["services"][svc]["environment"]["BOOMERANG_KV_SCHEMA"]) == kv_schema

    # Omitted when no kv_table is in scope
    partial_files = {f.path: f.content for f in leaseweb_gen.render(pam, leaseweb_gen.make_secrets(pam), ["receipts"])}
    assert "BOOMERANG_KV_SCHEMA" not in partial_files["laptop.env"]
    assert "BOOMERANG_KV_SCHEMA" not in partial_files["docker-compose.yml"]


# ============================================================================
# 2. Graceful AI fallback in scanner.scan_ai & assess.assess
# ============================================================================

@pytest.mark.parametrize("exc", [
    llm_mod.LLMUnavailable("Ollama not reachable at http://127.0.0.1:11435"),
    llm_mod.LLMError("scan: the answer failed validation twice"),
])
def test_scan_ai_falls_back_to_static_when_llm_unavailable_or_errors(monkeypatch, exc):
    def fail(**kwargs):
        raise exc

    monkeypatch.setattr("boomerang.llm.structured", fail)
    events = []
    pam = scanner_ai.scan_ai(SHOPLITE, Emitter("t", [events.append]))

    ids = {c.id for c in pam.components}
    assert {"orders", "receipts", "order_events"} <= ids
    orders = next(c for c in pam.components if c.id == "orders")
    assert orders.partition_key is not None and orders.partition_key.name == "order_id"
    assert pam.entrypoints == ["app.main:app"]
    assert any(e.level == "warning" and (e.data or {}).get("fallback") == "static" for e in events)


def test_scan_ai_still_raises_cache_miss_in_replay_mode(monkeypatch):
    def miss(**kwargs):
        raise llm_mod.CacheMiss("scan: no cached answer")

    monkeypatch.setattr("boomerang.llm.structured", miss)
    with pytest.raises(llm_mod.CacheMiss):
        scanner_ai.scan_ai(SHOPLITE, Emitter("t"))


def test_scan_ai_preserves_static_terraform_metadata_dropped_by_ai(monkeypatch):
    # Simulate a small LLM omitting optional Terraform metadata fields
    stripped = AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=[],
        components=[
            Component(id="orders", kind="kv_table", provider="aws.dynamodb", resource_name=""),
            Component(id="order_events", kind="queue", provider="aws.sqs", resource_name=""),
            Component(id="receipts", kind="object_store", provider="aws.s3", resource_name=""),
        ],
        files=[],
        env=[],
    )
    monkeypatch.setattr("boomerang.llm.structured", lambda **kwargs: stripped)
    events = []
    pam = scanner_ai.scan_ai(SHOPLITE, Emitter("t", [events.append]))

    assert pam.entrypoints == ["app.main:app"]
    by_id = {c.id: c for c in pam.components}
    assert by_id["orders"].resource_name == "orders"
    assert by_id["orders"].name_env_var == "ORDERS_TABLE"
    assert by_id["orders"].partition_key is not None and by_id["orders"].partition_key.name == "order_id"
    assert [i.name for i in by_id["orders"].indexes] == ["customer_id-index"]
    assert by_id["order_events"].resource_name == "order-events"
    assert by_id["order_events"].max_receive_count == 3
    assert by_id["order_events"].visibility_timeout_s == 30
    assert by_id["receipts"].name_env_var == "RECEIPTS_BUCKET"


@pytest.mark.parametrize("exc", [
    llm_mod.LLMUnavailable("Ollama not reachable"),
    llm_mod.LLMError("assess: validation failed"),
])
def test_assess_falls_back_to_deterministic_rules_when_llm_unavailable_or_errors(monkeypatch, exc):
    def fail(**kwargs):
        raise exc

    monkeypatch.setattr("boomerang.llm.structured", fail)
    pam = scan_static(SHOPLITE)
    events = []
    result = assess.assess(pam, Emitter("t", [events.append]), scope=["orders", "receipts"])

    assert result.overall == "red"
    assert any(f.component_id == "hybrid" and f.light == "red" for f in result.findings)
    assert any(f.component_id == "orders" and f.light == "amber" for f in result.findings)
    assert any(e.level == "warning" and (e.data or {}).get("fallback") == "rules" for e in events)


def test_assess_still_raises_cache_miss_in_replay_mode(monkeypatch):
    def miss(**kwargs):
        raise llm_mod.CacheMiss("assess: no cached answer")

    monkeypatch.setattr("boomerang.llm.structured", miss)
    pam = scan_static(SHOPLITE)
    with pytest.raises(llm_mod.CacheMiss):
        assess.assess(pam, Emitter("t"))


def test_plan_succeeds_end_to_end_when_ollama_is_down(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOMERANG_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BOOMERANG_ALLOW_FIXTURES", "0")
    get_settings.cache_clear()

    def down(**kwargs):
        raise llm_mod.LLMUnavailable("Ollama not reachable at http://127.0.0.1:11435")

    monkeypatch.setattr("boomerang.llm.structured", down)
    ctx = pipeline.plan(SHOPLITE, run_id="offline-plan")
    assert ctx.summary.status == "awaiting_approval"
    assert ctx.summary.lock_in_before is not None and ctx.summary.lock_in_before > 0
    assert (ctx.root / "pam.json").is_file() and (ctx.root / "assessment.json").is_file()
    events = read_events(ctx.root / "events.jsonl")
    assert any((e.data or {}).get("fallback") == "static" for e in events)
    assert any((e.data or {}).get("fallback") == "rules" for e in events)


# ============================================================================
# 3. Preserving kept AWS SQS queue messages across hybrid verification
# ============================================================================

class _FakeSQS:
    """In-memory SQS client matching boto3's get_queue_url / receive / send / delete."""

    def __init__(self, queues: dict[str, list[str]]) -> None:
        self.queues = {name: list(msgs) for name, msgs in queues.items()}
        self.sent_calls: list[dict[str, Any]] = []
        self._in_flight: dict[str, dict[str, str]] = {}
        self._seq = 0

    def get_queue_url(self, QueueName: str) -> dict[str, str]:
        if QueueName not in self.queues:
            raise KeyError(f"QueueDoesNotExist: {QueueName}")
        return {"QueueUrl": f"http://fake-sqs/{QueueName}"}

    def receive_message(self, QueueUrl: str, MaxNumberOfMessages: int = 1, **_kw) -> dict[str, Any]:
        name = QueueUrl.rsplit("/", 1)[-1]
        q = self.queues[name]
        batch = []
        while q and len(batch) < MaxNumberOfMessages:
            self._seq += 1
            handle = f"rh-{self._seq}"
            body = q.pop(0)
            self._in_flight.setdefault(name, {})[handle] = body
            batch.append({"MessageId": f"m-{self._seq}", "ReceiptHandle": handle, "Body": body})
        return {"Messages": batch} if batch else {}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> dict[str, Any]:
        name = QueueUrl.rsplit("/", 1)[-1]
        self._in_flight.get(name, {}).pop(ReceiptHandle, None)
        return {}

    def send_message(self, QueueUrl: str, MessageBody: str, **kwargs) -> dict[str, Any]:
        name = QueueUrl.rsplit("/", 1)[-1]
        self.queues[name].append(MessageBody)
        self.sent_calls.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody, **kwargs})
        return {"MessageId": f"sent-{len(self.sent_calls)}"}


def test_provisioner_snapshot_and_restore_queues_with_fake_sqs_client(monkeypatch):
    pam = AppModel(
        app_name="qapp",
        platform="aws",
        entrypoints=[],
        components=[
            Component(id="order_events", kind="queue", provider="aws.sqs", resource_name="order-events",
                      name_env_var="ORDER_EVENTS_QUEUE"),
            Component(id="audit_fifo", kind="queue", provider="aws.sqs", resource_name="audit.fifo",
                      name_env_var="AUDIT_QUEUE", fifo=True),
        ],
        files=[],
        env=[],
    )
    fake_sqs = _FakeSQS({
        "order-events": ['{"order_id":"o1"}', '{"order_id":"o2"}', '{"order_id":"o3"}'],
        "audit.fifo": ['{"event":"e1"}'],
    })
    monkeypatch.setattr(provisioner_aws, "_clients", lambda env: (None, None, fake_sqs, None))
    env = {
        "AWS_ENDPOINT_URL": "http://127.0.0.1:4566",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "ORDER_EVENTS_QUEUE": "order-events",
        "AUDIT_QUEUE": "audit.fifo",
    }

    snapshot = provisioner_aws.snapshot_queues(pam, ["order_events", "audit_fifo"], env)
    assert snapshot == {
        "order_events": ['{"order_id":"o1"}', '{"order_id":"o2"}', '{"order_id":"o3"}'],
        "audit_fifo": ['{"event":"e1"}'],
    }
    # Both queues are clean (empty) while verify runs
    assert fake_sqs.queues["order-events"] == []
    assert fake_sqs.queues["audit.fifo"] == []

    # Simulate verify tests leaving stray messages behind
    fake_sqs.send_message(QueueUrl="http://fake-sqs/order-events", MessageBody='{"order_id":"stray-test"}')
    fake_sqs.send_message(QueueUrl="http://fake-sqs/audit.fifo", MessageBody='{"event":"stray-fifo"}')

    restored = provisioner_aws.restore_queues(pam, snapshot, env)
    assert restored == {"order_events": 3, "audit_fifo": 1}
    assert fake_sqs.queues["order-events"] == ['{"order_id":"o1"}', '{"order_id":"o2"}', '{"order_id":"o3"}']
    assert fake_sqs.queues["audit.fifo"] == ['{"event":"e1"}']
    fifo_call = next(c for c in fake_sqs.sent_calls if c["QueueUrl"].endswith("audit.fifo") and "MessageGroupId" in c)
    assert fifo_call["MessageGroupId"] == "boomerang-restore"
    assert fifo_call["MessageDeduplicationId"].startswith("restore-audit_fifo-0-")


def test_hybrid_verify_snapshots_and_restores_kept_aws_queues(world, source, moves, llm, monkeypatch):
    aws_env = {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "ORDER_EVENTS_QUEUE": "order_events"}
    order: list[str] = []

    def fake_provision(pam, emit=None, **kw):
        world.calls.append("provision")
        return aws_env

    def fake_snapshot(pam, queue_ids, env=None):
        order.append(f"snapshot:{','.join(queue_ids)}")
        assert env == aws_env
        return {"order_events": ['{"order_id":"1"}', '{"order_id":"2"}', '{"order_id":"3"}']}

    def fake_restore(pam, snap, env=None):
        order.append(f"restore:{sum(len(v) for v in snap.values())}")
        assert env == aws_env
        return {k: len(v) for k, v in snap.items()}

    world.on_verify = lambda lw, attempt: order.append(f"verify:{attempt}")
    monkeypatch.setattr("boomerang.provisioner_aws.provision", fake_provision)
    monkeypatch.setattr("boomerang.provisioner_aws.snapshot_queues", fake_snapshot)
    monkeypatch.setattr("boomerang.provisioner_aws.restore_queues", fake_restore)

    pipeline.plan(source, run_id="h1")
    pipeline.approve("h1", scope=["orders", "receipts"])
    summary = pipeline.execute("h1")

    assert summary.status == "passed"
    assert order == ["snapshot:order_events", "verify:1", "restore:3"]
    texts = [e.message for e in read_events(root_of("h1") / "events.jsonl")]
    assert any("preserved 3 pending message(s)" in t for t in texts)
    assert any("restored 3 pending message(s)" in t for t in texts)


def test_hybrid_verify_restores_kept_queues_even_when_verify_raises(world, source, moves, llm, monkeypatch):
    aws_env = {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "ORDER_EVENTS_QUEUE": "order_events"}
    restored_calls: list[dict[str, list[str]]] = []

    monkeypatch.setattr("boomerang.provisioner_aws.provision", lambda pam, emit=None, **kw: aws_env)
    monkeypatch.setattr("boomerang.provisioner_aws.snapshot_queues",
                        lambda pam, qids, env=None: {"order_events": ["m1", "m2"]})
    monkeypatch.setattr("boomerang.provisioner_aws.restore_queues",
                        lambda pam, snap, env=None: (restored_calls.append(snap), {"order_events": 2})[1])

    def boom(leaseweb_dir, attempt, emit):
        raise RuntimeError("docker died")

    monkeypatch.setattr("boomerang.verifier.verify", boom)

    pipeline.plan(source, run_id="h2")
    pipeline.approve("h2", scope=["orders", "receipts"])
    with pytest.raises(RuntimeError, match="docker died"):
        pipeline.execute("h2")

    assert restored_calls == [{"order_events": ["m1", "m2"]}]


def test_full_scope_and_non_queue_kept_scopes_do_not_snapshot_queues(world, source, moves, llm, monkeypatch):
    aws_env = {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566"}
    called: list[str] = []

    monkeypatch.setattr("boomerang.provisioner_aws.provision", lambda pam, emit=None, **kw: aws_env)
    monkeypatch.setattr("boomerang.provisioner_aws.snapshot_queues",
                        lambda *a, **kw: (called.append("snapshot"), {})[1])

    # Full migration: no snapshot
    pipeline.migrate(source, yes=True, run_id="full1")
    # Hybrid where queue IS self-hosted (orders/receipts kept): no queue snapshot
    pipeline.plan(source, run_id="hyb-q")
    pipeline.approve("hyb-q", scope=["order_events"])
    pipeline.execute("hyb-q")
    assert called == []


@pytest.mark.integration
def test_live_moto_snapshot_and_restore_preserves_seeded_sqs_messages():
    from boomerang import lab

    lab.up()
    lab.reset()
    pam = scan_static(SHOPLITE)
    env = provisioner_aws.provision(pam)
    provisioner_aws.seed(pam, count=10, pending=3)
    before = provisioner_aws.counts_shoplite(env)
    assert before["order_events"] == 3

    snap = provisioner_aws.snapshot_queues(pam, ["order_events"], env)
    assert len(snap["order_events"]) == 3
    assert provisioner_aws.counts_shoplite(env)["order_events"] == 0

    # Simulate verify test writing a stray message
    _, _, sqs, _ = provisioner_aws._clients(env)
    url = sqs.get_queue_url(QueueName=env["ORDER_EVENTS_QUEUE"])["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody='{"order_id":"stray-from-verify"}')
    assert provisioner_aws.counts_shoplite(env)["order_events"] == 1

    restored = provisioner_aws.restore_queues(pam, snap, env)
    assert restored == {"order_events": 3}
    assert provisioner_aws.counts_shoplite(env)["order_events"] == 3

    # Verify exact original bodies came back (no stray message)
    after_snap = provisioner_aws.snapshot_queues(pam, ["order_events"], env)
    assert sorted(after_snap["order_events"]) == sorted(snap["order_events"])
    assert '{"order_id":"stray-from-verify"}' not in after_snap["order_events"]


# ============================================================================
# 4. Run-local de-lock cache & per-file relevant `kept` prompts
# ============================================================================

def test_move_over_and_move_back_reuse_delock_cache_with_zero_extra_llm_calls(world, source, moves, llm):
    # Step 1: hybrid execute (orders + receipts) → 2 AI calls for app/db.py
    pipeline.plan(source, run_id="c1")
    pipeline.approve("c1", scope=["orders", "receipts"])
    s1 = pipeline.execute("c1")
    assert s1.status == "passed"
    assert len(llm.calls) == 2
    assert (root_of("c1") / delock.DELOCK_CACHE).is_file()

    # Step 2: move_over order_events → 0 LLM calls (app/db.py hits delock-cache.json)
    llm.calls.clear()
    pipeline._set_active_stack("c1")
    s2 = pipeline.move_over("c1", ["order_events"])
    assert s2.status == "passed" and s2.scope is None
    assert llm.calls == []
    assert "kv.resource()" in (root_of("c1") / "leaseweb" / "app" / "app" / "db.py").read_text(encoding="utf-8")
    assert (root_of("c1") / "diffs" / "app_db.py__get_order.diff").is_file()
    events = read_events(root_of("c1") / "events.jsonl")
    assert any((e.data or {}).get("cached_delock") is True for e in events)

    # Step 3: move_back orders → 0 LLM calls (orders back on AWS)
    llm.calls.clear()
    pipeline._set_active_stack("c1")
    s3 = pipeline.move_back("c1", ["orders"])
    assert s3.status == "passed"
    assert llm.calls == []

    # Step 4: move_over orders again → STILL 0 LLM calls (hits delock-cache.json again!)
    llm.calls.clear()
    pipeline._set_active_stack("c1")
    s4 = pipeline.move_over("c1", ["orders"])
    assert s4.status == "passed" and s4.scope is None
    assert llm.calls == []
    assert "kv.resource()" in (root_of("c1") / "leaseweb" / "app" / "app" / "db.py").read_text(encoding="utf-8")


def test_re_execute_clears_delock_cache_for_fresh_attempt(world, source, moves, llm):
    pipeline.migrate(source, yes=True, run_id="c2")
    assert len(llm.calls) == 2
    assert (root_of("c2") / delock.DELOCK_CACHE).is_file()

    llm.calls.clear()
    pipeline.execute("c2")
    assert len(llm.calls) == 2


def test_mixed_file_invalidates_delock_cache_when_colocated_kept_component_changes(tmp_path, fake_llm):
    code = '''\
import boto3

dynamodb = boto3.resource("dynamodb")
orders = dynamodb.Table("orders")
sqs = boto3.client("sqs")


def load_and_notify(order_id):
    resp = orders.get_item(Key={"order_id": order_id}, ConsistentRead=True)
    sqs.send_message(QueueUrl="http://sqs/q", MessageBody=order_id)
    return resp.get("Item")
'''
    rewritten_hybrid = '''\
def load_and_notify(order_id):
    resp = orders.get_item(Key={"order_id": order_id})
    sqs.send_message(QueueUrl="http://sqs/q", MessageBody=order_id)
    return resp.get("Item")'''
    rewritten_full = '''\
def load_and_notify(order_id):
    resp = orders.get_item(Key={"order_id": order_id})
    sqs.send_message(QueueUrl="http://sqs/q", MessageBody=order_id)
    return resp.get("Item")'''

    pam = AppModel(
        app_name="mixed", platform="aws", entrypoints=[], files=[], env=[],
        components=[
            Component(id="orders", kind="kv_table", provider="aws.dynamodb", resource_name="orders",
                      partition_key=KeyAttr(name="order_id"),
                      usages=[CodeRef(file="app/mixed.py", line=9, symbol="load_and_notify",
                                      operation="get_item", snippet="orders.get_item(...)")]),
            Component(id="order_events", kind="queue", provider="aws.sqs", resource_name="order-events",
                      usages=[CodeRef(file="app/mixed.py", line=10, symbol="load_and_notify",
                                      operation="send_message", snippet="sqs.send_message(...)")]),
        ],
    )
    assessment = Assessment(overall="amber", summary="2 findings", findings=[
        Finding(analysis="a", component_id="orders", feature="get", light="amber", what_changes="kv"),
        Finding(analysis="a", component_id="order_events", feature="send", light="amber", what_changes="queue"),
    ])

    run_dir = tmp_path / "run"
    app = run_dir / "leaseweb" / "app"
    (app / "app").mkdir(parents=True)
    (app / "app" / "mixed.py").write_text(code, encoding="utf-8")

    # Run 1: hybrid scope=["orders"] (order_events kept on AWS)
    fake_llm.responses["delock:app/mixed.py:load_and_notify"] = FunctionRewrite(
        analysis="hybrid rewrite", new_source=rewritten_hybrid)
    delock.delock_detailed(app, pam, assessment, Emitter("t"), run_dir / "diffs", out_dir=run_dir, scope=["orders"])
    assert len(fake_llm.calls) == 1
    assert "order_events (aws.sqs)" in fake_llm.calls[0]["user"]

    # Run 2: same out_dir, fresh app copy, FULL scope (order_events now moves too!) -> MUST NOT reuse hybrid cache
    (app / "app" / "mixed.py").write_text(code, encoding="utf-8")
    fake_llm.responses["delock:app/mixed.py:load_and_notify"] = FunctionRewrite(
        analysis="full rewrite", new_source=rewritten_full)
    delock.delock_detailed(app, pam, assessment, Emitter("t"), run_dir / "diffs", out_dir=run_dir,
                           scope=["orders", "order_events"])
    assert len(fake_llm.calls) == 2
    assert "order_events (aws.sqs)" not in fake_llm.calls[1]["user"]
    text = (app / "app" / "mixed.py").read_text(encoding="utf-8")
    assert "sqs = queue.client()" in text and "boto3" not in text


def test_tampered_delock_cache_entry_is_rejected_and_falls_back_to_llm(tmp_path, fake_llm):
    from tests.test_shoplite_integration import GET_ORDER, LIST_BY_CUSTOMER

    pam = scan_static(SHOPLITE)
    lights = {"orders": "amber", "order_events": "amber", "receipts": "green"}
    assessment = Assessment(overall="amber", summary="s", findings=[
        Finding(analysis="a", component_id=c.id, feature="f", light=lights.get(c.id, "red"), what_changes="w")
        for c in pam.components])

    run_dir = tmp_path / "run"
    app = run_dir / "leaseweb" / "app"
    shutil.copytree(SHOPLITE, app, ignore=shutil.ignore_patterns("infra", "__pycache__"))

    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="clean", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="clean", new_source=LIST_BY_CUSTOMER),
    })
    delock.delock_detailed(app, pam, assessment, Emitter("t"), run_dir / "diffs", out_dir=run_dir)
    assert len(fake_llm.calls) == 2

    # Tamper get_order's cache entry with a forbidden os.system() call
    cache_path = run_dir / delock.DELOCK_CACHE
    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
    for entry in cache_data.values():
        if entry["function"] == "get_order":
            entry["new_source"] = GET_ORDER.replace("item =", 'os.system("id")\n    item =')
    cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    # Reset app and re-run: get_order must reject tampered cache and re-call LLM; list_orders still hits cache
    shutil.rmtree(app)
    shutil.copytree(SHOPLITE, app, ignore=shutil.ignore_patterns("infra", "__pycache__"))
    fake_llm.calls.clear()
    delock.delock_detailed(app, pam, assessment, Emitter("t"), run_dir / "diffs", out_dir=run_dir)
    assert [c["label"] for c in fake_llm.calls] == ["delock:app/db.py:get_order"]
    db = (app / "app" / "db.py").read_text(encoding="utf-8")
    assert "os.system" not in db and GET_ORDER in db


def test_single_service_file_gets_identical_llm_prompt_across_full_and_hybrid_scopes(tmp_path, fake_llm):
    from tests.test_shoplite_integration import GET_ORDER, LIST_BY_CUSTOMER

    pam = scan_static(SHOPLITE)
    lights = {"orders": "amber", "order_events": "amber", "receipts": "green"}
    assessment = Assessment(overall="amber", summary="s", findings=[
        Finding(analysis="a", component_id=c.id, feature="f", light=lights.get(c.id, "red"), what_changes="w")
        for c in pam.components])
    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="ok", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="ok", new_source=LIST_BY_CUSTOMER),
    })

    # Full migration run
    full_dir = tmp_path / "full"
    shutil.copytree(SHOPLITE, full_dir / "leaseweb" / "app", ignore=shutil.ignore_patterns("infra", "__pycache__"))
    delock.delock_detailed(full_dir / "leaseweb" / "app", pam, assessment, Emitter("f"),
                           full_dir / "diffs", out_dir=full_dir, scope=None)
    full_prompts = [(c["label"], c["system"], c["user"]) for c in fake_llm.calls]

    # Hybrid migration run (orders + receipts; order_events stays on AWS)
    fake_llm.calls.clear()
    hyb_dir = tmp_path / "hybrid"
    shutil.copytree(SHOPLITE, hyb_dir / "leaseweb" / "app", ignore=shutil.ignore_patterns("infra", "__pycache__"))
    delock.delock_detailed(hyb_dir / "leaseweb" / "app", pam, assessment, Emitter("h"),
                           hyb_dir / "diffs", out_dir=hyb_dir, scope=["orders", "receipts"])
    hyb_prompts = [(c["label"], c["system"], c["user"]) for c in fake_llm.calls]

    # Exact same prompts -> same demo/llm-cache keys for offline replay!
    assert hyb_prompts == full_prompts


@pytest.mark.parametrize("scope", [None, ["orders", "receipts"], ["orders"], ["order_events"]])
def test_generated_stack_with_kv_schema_passes_verifier_check_config(tmp_path, scope):
    pam = scan_static(SHOPLITE)
    lw = tmp_path / "leaseweb"
    for f in leaseweb_gen.render(pam, leaseweb_gen.make_secrets(pam), scope):
        target = lw / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f.content.replace("\r\n", "\n").encode("utf-8"))
    check = verifier.check_config(lw)
    assert check.ok, check.detail


def test_delock_cache_contains_no_secrets_and_passes_democache_check(world, source, moves, llm, tmp_path):
    from boomerang import democache

    pipeline.plan(source, run_id="dc1")
    pipeline.approve("dc1", scope=["orders", "receipts"])
    pipeline.execute("dc1")

    cache_file = root_of("dc1") / delock.DELOCK_CACHE
    assert cache_file.is_file()
    cache_dir = tmp_path / "demo-cache"
    cache_dir.mkdir()
    report = democache.check(cache_dir, [root_of("dc1")])
    # Only problem possible here would be secrets.env not in a git repo (becomes a note) — never delock-cache.json
    assert not any(delock.DELOCK_CACHE in p for p in report.problems), report.problems

