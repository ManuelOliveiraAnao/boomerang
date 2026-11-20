"""Black-box tests for ShopLite. They use only HTTP and app.worker.process_once,
so the same file runs against every platform. Never edited by the migration."""
import uuid

from app.worker import process_once


def _order(client, customer_id=None, items=None):
    body = {"customer_id": customer_id or uuid.uuid4().hex,
            "items": items or [{"sku": "A-1", "qty": 2, "price_cents": 1250},
                               {"sku": "B-2", "qty": 1, "price_cents": 499}]}
    return client.post("/orders", json=body)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_create_order(client):
    r = _order(client)
    assert r.status_code == 201
    order = r.json()
    assert order["total_cents"] == 2 * 1250 + 499
    assert order["status"] == "pending"
    assert order["order_id"]


def test_get_order(client):
    created = _order(client).json()
    r = client.get(f"/orders/{created['order_id']}")
    assert r.status_code == 200
    got = r.json()
    for field in ("order_id", "customer_id", "items", "total_cents", "status", "created_at"):
        assert got[field] == created[field]
    assert client.get(f"/orders/{uuid.uuid4().hex}").status_code == 404


def test_validation(client):
    assert client.post("/orders", json={"customer_id": "c", "items": []}).status_code == 422
    bad = {"customer_id": "c", "items": [{"sku": "A", "qty": 0, "price_cents": 1}]}
    assert client.post("/orders", json=bad).status_code == 422


def test_receipt(client):
    created = _order(client).json()
    r = client.get(f"/orders/{created['order_id']}/receipt")
    assert r.status_code == 200
    receipt = r.json()
    assert receipt["order_id"] == created["order_id"]
    assert receipt["total_cents"] == created["total_cents"]


def test_list_by_customer(client):
    customer = uuid.uuid4().hex
    ids = {_order(client, customer).json()["order_id"] for _ in range(2)}
    r = client.get(f"/customers/{customer}/orders")
    assert r.status_code == 200
    assert {o["order_id"] for o in r.json()} == ids


def test_worker_processes_order(client):
    created = _order(client).json()
    for _ in range(10):
        if process_once(wait_seconds=1) is None:
            break
    assert client.get(f"/orders/{created['order_id']}").json()["status"] == "processed"
