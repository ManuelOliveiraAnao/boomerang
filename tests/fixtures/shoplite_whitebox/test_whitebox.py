"""Role A's internal ShopLite tests (AWS flavor). Not part of the migration spec:
they poke at internals, so they are run through verifier.run_pytest by tests/test_shoplite.py."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import db, queue, storage
from app.worker import process_once

client = TestClient(main.app)
BODY = {"customer_id": "c-wb", "items": [{"sku": "A", "qty": 3, "price_cents": 333}]}


def test_receipt_404_for_unknown_order():
    assert client.get(f"/orders/{uuid.uuid4().hex}/receipt").status_code == 404


def test_duplicate_order_id_returns_409(monkeypatch):
    fixed = uuid.UUID(int=42)
    monkeypatch.setattr(main.uuid, "uuid4", lambda: fixed)
    assert client.post("/orders", json=BODY).status_code == 201
    r = client.post("/orders", json=BODY)
    assert r.status_code == 409
    # the first order is untouched
    assert client.get(f"/orders/{fixed.hex}").json()["total_cents"] == 999


def test_set_status_on_missing_order_raises():
    with pytest.raises(db.OrderNotFound):
        db.set_status("ghost-" + uuid.uuid4().hex, "processed", "2026-01-01T00:00:00+00:00")


def test_worker_drops_event_for_unknown_order_without_creating_it():
    ghost = "ghost-" + uuid.uuid4().hex
    queue.publish_order_created(ghost)
    seen = []
    for _ in range(20):
        oid = process_once(wait_seconds=1)
        if oid is None:
            break
        seen.append(oid)
    assert ghost in seen
    assert client.get(f"/orders/{ghost}").status_code == 404


def test_list_for_unknown_customer_is_empty():
    assert client.get(f"/customers/{uuid.uuid4().hex}/orders").json() == []


def test_get_order_returns_ints_not_decimals():
    oid = client.post("/orders", json=BODY | {"customer_id": uuid.uuid4().hex}).json()["order_id"]
    order = db.get_order(oid)
    assert type(order["total_cents"]) is int and type(order["items"][0]["qty"]) is int


def test_receipt_is_stored_as_json_with_content_type():
    oid = client.post("/orders", json=BODY | {"customer_id": uuid.uuid4().hex}).json()["order_id"]
    import os
    obj = storage.s3.get_object(Bucket=os.environ["RECEIPTS_BUCKET"], Key=f"receipts/{oid}.json")
    assert obj["ContentType"] == "application/json"
    assert json.loads(obj["Body"].read())["order_id"] == oid


def test_unacked_message_is_redelivered():
    """SQS semantics ShopLite relies on: a crash between receive and ack redelivers the event."""
    oid = client.post("/orders", json=BODY | {"customer_id": uuid.uuid4().hex}).json()["order_id"]
    resp = queue.sqs.receive_message(QueueUrl=queue._url(), MaxNumberOfMessages=10, VisibilityTimeout=1,
                                     WaitTimeSeconds=1)
    assert any(json.loads(m["Body"])["order_id"] == oid for m in resp.get("Messages", []))
    import time
    time.sleep(1.5)
    for _ in range(20):
        if process_once(wait_seconds=1) is None:
            break
    assert client.get(f"/orders/{oid}").json()["status"] == "processed"


@pytest.mark.parametrize("bad", [
    {"customer_id": "c"},                                                   # no items
    {"items": [{"sku": "A", "qty": 1, "price_cents": 1}]},                  # no customer
    {"customer_id": "c", "items": [{"sku": "A", "qty": -1, "price_cents": 1}]},
    {"customer_id": "c", "items": [{"sku": "A", "qty": 1, "price_cents": -5}]},
    {"customer_id": "c", "items": [{"sku": "A", "qty": "x", "price_cents": 1}]},
])
def test_more_validation(bad):
    assert client.post("/orders", json=bad).status_code == 422
