import os
from decimal import Decimal

import pytest

from sovereign_sdk.kv import (
    ConditionalCheckFailedException,
    Key,
    UnsupportedFeature,
    ddl,
    resource,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


def _table_name() -> str:
    return "orders_test"


@pytest.fixture
def table():
    kv = resource(os.getenv("DATABASE_URL"))
    table_name = _table_name()
    kv.connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
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
    kv.connection.execute(ddl(component))
    kv.connection.commit()
    yield kv.Table(table_name)
    kv.connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    kv.connection.commit()
    kv.close()


def test_put_get_update_delete(table):
    table.put_item(
        {"order_id": "order-1", "customer_id": "customer-1", "status": "pending", "total_cents": 420},
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


def test_scan_and_gsi_query(table):
    table.put_item({"order_id": "1", "customer_id": "customer-1"})
    table.put_item({"order_id": "2", "customer_id": "customer-1"})
    table.put_item({"order_id": "3", "customer_id": "customer-2"})

    assert len(table.scan()["Items"]) == 3
    items = table.query(Key("customer_id").eq("customer-1"), IndexName="customer_id-index")
    assert len(items["Items"]) == 2


def test_condition_is_enforced(table):
    table.put_item({"order_id": "order-1"})
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item(
            {"order_id": "order-1"},
            ConditionExpression="attribute_not_exists(order_id)",
        )


def test_unsupported_expression(table):
    with pytest.raises(UnsupportedFeature):
        table.put_item({"order_id": "order-1"}, ConditionExpression="begins_with(order_id, 'a')")


def test_attribute_exists_condition(table):
    table.put_item({"order_id": "order-1"})
    table.put_item(
        {"order_id": "order-1", "status": "processed"},
        ConditionExpression="attribute_exists(order_id)",
    )
    assert table.get_item({"order_id": "order-1"})["Item"]["status"] == "processed"


def test_sort_key_round_trip(table):
    kv = table.connection
    table_name = "events_test"
    kv.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    component = {
        "id": "events",
        "resource_name": table_name,
        "partition_key": {"name": "account_id", "type": "S"},
        "sort_key": {"name": "event_id", "type": "S"},
        "indexes": [],
    }
    kv.execute(ddl(component))
    kv.commit()
    sort_table = __import__("sovereign_sdk.kv", fromlist=["Table"]).Table(kv, table_name)

    sort_table.put_item(
        {"account_id": "account-1", "event_id": "event-1", "message": "created"}
    )
    item = sort_table.get_item(
        {"account_id": "account-1", "event_id": "event-1"}
    )
    assert item["Item"]["message"] == "created"

    sort_table.delete_item({"account_id": "account-1", "event_id": "event-1"})
    assert sort_table.get_item(
        {"account_id": "account-1", "event_id": "event-1"}
    ) == {}
    kv.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    kv.commit()
