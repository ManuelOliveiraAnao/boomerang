"""mini_app's own tests (need moto). Never collected by Boomerang's unit run (tests/conftest.py ignores data/*).

The de-lock must leave this file byte-identical.
"""
import uuid

from app import db, queue, storage


def test_order_round_trip():
    order = {"order_id": uuid.uuid4().hex, "customer_id": "c1", "total_cents": 1200, "status": "pending"}
    db.put_order(order)
    assert db.get_order(order["order_id"])["total_cents"] == 1200
    assert [o["order_id"] for o in db.list_orders_by_customer("c1")] == [order["order_id"]]
    db.set_status(order["order_id"], "processed")
    assert db.get_order(order["order_id"])["status"] == "processed"


def test_queue_and_receipt():
    queue.publish_order_created("o1")
    order_id, handle = queue.receive_one()
    queue.ack(handle)
    storage.put_receipt({"order_id": order_id, "total_cents": 5})
    assert storage.get_receipt(order_id)["total_cents"] == 5
