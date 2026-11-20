"""A+B+C on the real ShopLite (sample-app/aws): scan → generate, and the de-lock split (L13/L14).

No Docker, no model: the LLM is faked with the rewrites a correct model gives. Guards the integration bugs
found after the merge: the scanner missed every DynamoDB call made through `_table()`, and the Terraform
keys, GSI, real names and DLQ, so the generator crashed ("A KV table must have a partition key").
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from boomerang import delock, rules, slicer
from boomerang.events import Emitter
from boomerang.generators import leaseweb
from boomerang.models import Assessment, Event, Finding, FunctionRewrite
from boomerang.scanner.static import scan_static

SHOPLITE = Path(__file__).resolve().parents[1] / "sample-app" / "aws"

GET_ORDER = '''\
def get_order(order_id: str) -> dict | None:
    # strongly consistent read: reads its own writes immediately after put_order
    item = _table().get_item(Key={"order_id": order_id}).get("Item")
    return _from_ddb(item) if item else None'''
LIST_BY_CUSTOMER = '''\
def list_orders_by_customer(customer_id: str) -> list[dict]:
    resp = _table().query(
        IndexName="customer_id-index",
        KeyConditionExpression=Key("customer_id").eq(customer_id),
    )
    return [_from_ddb(i) for i in resp.get("Items", [])]'''

pytestmark = pytest.mark.skipif(not SHOPLITE.is_dir(), reason="sample-app/aws (role A) not present")


@pytest.fixture(scope="module")
def pam():
    return scan_static(SHOPLITE)


def comp(pam, cid):
    return next(c for c in pam.components if c.id == cid)


# ---------- the scan (B) has what A's provisioner/mover and B's generator need ----------

def test_scan_finds_the_dynamodb_calls_made_through_the_table_helper(pam):
    orders = comp(pam, "orders")
    by_symbol = {(u.symbol, u.operation) for u in orders.usages}
    assert {("put_order", "put_item"), ("get_order", "get_item"), ("list_orders_by_customer", "query"),
            ("set_status", "update_item")} <= by_symbol
    assert all(u.file == "app/db.py" for u in orders.usages)


def test_scan_reads_names_keys_index_and_queue_settings(pam):
    orders, events, receipts = comp(pam, "orders"), comp(pam, "order_events"), comp(pam, "receipts")
    assert (orders.resource_name, orders.name_env_var) == ("orders", "ORDERS_TABLE")
    assert orders.partition_key is not None and orders.partition_key.name == "order_id"
    assert [(i.name, i.partition_key.name) for i in orders.indexes] == [("customer_id-index", "customer_id")]
    assert (events.resource_name, events.name_env_var) == ("order-events", "ORDER_EVENTS_QUEUE")
    assert (events.max_receive_count, events.visibility_timeout_s) == (3, 30) and "dlq" in events.features
    assert (receipts.resource_name, receipts.name_env_var) == ("receipts", "RECEIPTS_BUCKET")
    assert not any(c.id == "order_events_dlq" for c in pam.components)   # the DLQ is a feature of its queue
    assert pam.entrypoints == ["app.main:app"]
    reds = {c.id for c in pam.components if c.kind in ("function", "identity", "monitoring")}
    assert reds == {"receipt_thumbnailer", "shoplite_task", "dlq_not_empty"}


def test_the_generator_renders_the_scanned_pam(pam):
    files = leaseweb.render(pam, leaseweb.make_secrets(pam))
    paths = {f.path for f in files}
    assert {"docker-compose.yml", "secrets.env", "schema.sql", "rabbitmq/definitions.json"} <= paths
    schema = next(f.content for f in files if f.path == "schema.sql")
    assert "orders" in schema


# ---------- the de-lock (C) on the real code ----------

def test_rule_7_sees_adapter_calls_through_a_helper():
    info = slicer.module_info(SHOPLITE, "app/db.py")
    env = rules.module_adapter_types(info.tree, {"kv": "kv"})
    assert env["_table()"] == "kv.Table" and env["dynamodb"] == "kv.KV"
    node = next(n for n, q in info.functions if q == "get_order")
    problems = [p for c in rules.adapter_calls(node, env, {"kv": "kv"}, strict=False) for p in c.problems]
    assert problems == ["kv.Table.get_item() has no argument ConsistentRead"]


def test_delock_on_shoplite_sends_only_the_two_l13_shapes_to_the_ai(tmp_path, pam, fake_llm):
    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="ConsistentRead dropped: Postgres reads are "
                                                               "consistent.", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="string condition → Key().eq()",
                                                                    new_source=LIST_BY_CUSTOMER),
    })
    app = tmp_path / "run" / "leaseweb" / "app"
    shutil.copytree(SHOPLITE, app, ignore=shutil.ignore_patterns("infra", "__pycache__"))
    lights = {"orders": "amber", "order_events": "amber", "receipts": "green"}
    assessment = Assessment(overall="amber", summary="s", findings=[
        Finding(analysis="a", component_id=c.id, feature="f", light=lights.get(c.id, "red"), what_changes="w")
        for c in pam.components])
    events: list[Event] = []
    records = delock.delock_detailed(app, pam, assessment, Emitter("t", [events.append]), tmp_path / "run" / "diffs")

    assert [c["label"] for c in fake_llm.calls] == ["delock:app/db.py:get_order",
                                                   "delock:app/db.py:list_orders_by_customer"]
    kinds = {(r.file, r.function): (r.kind, r.accepted, r.changed_lines) for r in records}
    assert kinds[("app/db.py", "get_order")] == ("ai", True, 1)
    assert kinds[("app/db.py", "list_orders_by_customer")][:2] == ("ai", True)
    for fn in ("put_order", "set_status"):
        assert kinds[("app/db.py", fn)] == ("config", True, 0)
    for fn in ("_url", "publish_order_created", "receive_one", "ack"):
        assert kinds[("app/queue.py", fn)][:2] == ("config", True)
    db = (app / "app" / "db.py").read_text(encoding="utf-8")
    rules.check_module(db, "app/db.py")
    assert "dynamodb = kv.resource()" in db and "from boto3.dynamodb.conditions import Key" in db
    assert "ConsistentRead" not in db and '"customer_id = :c"' not in db
    assert "s3 = storage.s3_client()" in (app / "app" / "storage.py").read_text(encoding="utf-8")
    assert "sqs = queue.client()" in (app / "app" / "queue.py").read_text(encoding="utf-8")
    assert not [e for e in events if e.level == "error"]
    # the AI sees what the helper returns, not just its name
    context = fake_llm.calls[0]["user"].split("<<<MODULE_CONTEXT\n")[1].split("\nMODULE_CONTEXT>>>")[0]
    assert 'return dynamodb.Table(os.environ["ORDERS_TABLE"])' in context and "dynamodb = kv.resource()" in context
