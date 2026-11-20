"""Orders data layer (Amazon DynamoDB)."""
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")


def _table():
    return dynamodb.Table(os.environ["ORDERS_TABLE"])


def _from_ddb(value):
    """DynamoDB returns numbers as Decimal; the API uses plain ints."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    return value


def put_order(order: dict) -> None:
    _table().put_item(Item=order, ConditionExpression="attribute_not_exists(order_id)")


def get_order(order_id: str) -> dict | None:
    item = _table().get_item(Key={"order_id": order_id}).get("Item")
    return _from_ddb(item) if item else None


def list_orders_by_customer(customer_id: str) -> list[dict]:
    resp = _table().query(
        IndexName="customer_id-index",
        KeyConditionExpression=Key("customer_id").eq(customer_id),
    )
    return [_from_ddb(i) for i in resp.get("Items", [])]


def set_status(order_id: str, status: str, processed_at: str) -> None:
    _table().update_item(
        Key={"order_id": order_id},
        UpdateExpression="SET #s = :s, processed_at = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":p": processed_at},
    )
