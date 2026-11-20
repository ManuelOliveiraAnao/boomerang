import os
from decimal import Decimal

import pytest

from sovereign_sdk.kv import (
    ConditionalCheckFailedException,
    UnsupportedFeature,
    Key,
    ddl,
    resource,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def table():
    kv = resource(os.getenv("DATABASE_URL"))
    connection = kv._get_connection()
    table_name = "orders_member_b"
    connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    component = {
        "id": "orders",
        "resource_name": table_name,
        "partition_key": {"name": "order_id", "type": "S"},
        "sort_key": None,
        "indexes": [
            {
                "name": "customer_id-index",
                "partition_key": {"name": "customer_id", "type": "S"},
                "sort_key": None,
            }
        ],
    }
    connection.execute(ddl(component))
    connection.commit()
    yield kv.Table(table_name)
    connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    connection.commit()
    kv.close()


def test_put_get_update_delete(table):
    table.put_item(
        {"order_id": "order-1", "customer_id": "customer-1", "status": "pending", "total_cents": Decimal("420")},
        ConditionExpression="attribute_not_exists(order_id)",
    )
    assert table.get_item({"order_id": "order-1"})["Item"]["total_cents"] == Decimal("420")
    table.update_item(
        {"order_id": "order-1"},
        "SET #status = :status",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "processed"},
    )
    assert table.get_item({"order_id": "order-1"})["Item"]["status"] == "processed"
    table.delete_item({"order_id": "order-1"})
    assert table.get_item({"order_id": "order-1"}) == {}


def test_scan_gsi_and_sov_key(table):
    table.put_item({"order_id": "1", "customer_id": "customer-1"})
    table.put_item({"order_id": "2", "customer_id": "customer-1"})
    table.put_item({"order_id": "3", "customer_id": "customer-2"})
    assert len(table.scan()["Items"]) == 3
    assert len(table.query(Key("customer_id").eq("customer-1"), IndexName="customer_id-index")["Items"]) == 2


def test_boto3_key_condition(table):
    from boto3.dynamodb.conditions import Key as BotoKey

    table.put_item({"order_id": "1", "customer_id": "customer-1"})
    items = table.query(BotoKey("customer_id").eq("customer-1"), IndexName="customer_id-index")
    assert items["Items"][0]["order_id"] == "1"


def test_attribute_conditions(table):
    table.put_item({"order_id": "1"}, ConditionExpression="attribute_not_exists(order_id)")
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item({"order_id": "1"}, ConditionExpression="attribute_not_exists(order_id)")
    table.put_item({"order_id": "1", "status": "updated"}, ConditionExpression="attribute_exists(order_id)")


def test_resource_is_lazy(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:55432/test")
    kv = resource()
    assert kv.connection is None
