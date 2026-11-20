"""Orders in DynamoDB. A 40-line copy of ShopLite's shape (docs/04), plus the L13 shapes."""
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
orders = dynamodb.Table(os.environ.get("ORDERS_TABLE", "orders"))


def _clean(item):
    return {k: int(v) if isinstance(v, Decimal) else v for k, v in item.items()}


def put_order(order):
    try:
        orders.put_item(Item=order, ConditionExpression="attribute_not_exists(order_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError("duplicate order") from e
        raise
    return order


def get_order(order_id):
    resp = orders.get_item(Key={"order_id": order_id}, ConsistentRead=True)
    item = resp.get("Item")
    return _clean(item) if item else None


def list_orders_by_customer(customer_id):
    resp = orders.query(
        IndexName="customer_id-index",
        KeyConditionExpression="customer_id = :c",
        ExpressionAttributeValues={":c": customer_id},
    )
    return [_clean(i) for i in resp["Items"]]


def set_status(order_id, status):
    orders.update_item(
        Key={"order_id": order_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
    )
