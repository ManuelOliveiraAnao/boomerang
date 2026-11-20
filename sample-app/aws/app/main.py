"""ShopLite order service (HTTP API). No cloud SDK calls here."""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import db, queue, storage

app = FastAPI(title="ShopLite")


class Item(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    price_cents: int = Field(ge=0)


class OrderIn(BaseModel):
    customer_id: str
    items: list[Item] = Field(min_length=1)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders", status_code=201)
def create_order(body: OrderIn):
    order = {
        "order_id": uuid.uuid4().hex,
        "customer_id": body.customer_id,
        "items": [i.model_dump() for i in body.items],
        "total_cents": sum(i.qty * i.price_cents for i in body.items),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.put_order(order)
    storage.put_receipt(order["order_id"], {"order_id": order["order_id"],
                                            "customer_id": order["customer_id"],
                                            "items": order["items"],
                                            "total_cents": order["total_cents"]})
    queue.publish_order_created(order["order_id"])
    return order


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    order = db.get_order(order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    return order


@app.get("/customers/{customer_id}/orders")
def list_customer_orders(customer_id: str):
    return db.list_orders_by_customer(customer_id)


@app.get("/orders/{order_id}/receipt")
def get_receipt(order_id: str):
    receipt = storage.get_receipt(order_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return receipt
