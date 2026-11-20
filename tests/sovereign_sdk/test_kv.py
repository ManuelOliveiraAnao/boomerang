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


def test_ddl_rejects_missing_partition_key():
    with pytest.raises(UnsupportedFeature):
        ddl(
            {
                "id": "invalid",
                "resource_name": "invalid",
                "partition_key": None,
                "sort_key": None,
                "indexes": [],
            }
        )


def test_ddl_rejects_invalid_identifier():
    with pytest.raises(UnsupportedFeature):
        ddl(
            {
                "id": "invalid",
                "resource_name": "orders; DROP TABLE users",
                "partition_key": {"name": "order_id", "type": "S"},
                "sort_key": None,
                "indexes": [],
            }
        )


def test_ddl_includes_multiple_gsi_indexes():
    ddl_text = ddl(
        {
            "id": "orders",
            "resource_name": "orders",
            "partition_key": {"name": "order_id", "type": "S"},
            "sort_key": None,
            "indexes": [
                {
                    "name": "customer_id-index",
                    "partition_key": {"name": "customer_id", "type": "S"},
                    "sort_key": None,
                },
                {
                    "name": "status-created-index",
                    "partition_key": {"name": "status", "type": "S"},
                    "sort_key": {"name": "created_at", "type": "S"},
                },
            ],
        }
    )

    assert 'CREATE INDEX IF NOT EXISTS "customer_id_index"' in ddl_text
    assert 'CREATE INDEX IF NOT EXISTS "status_created_index"' in ddl_text
    assert '"created_at" TEXT' in ddl_text


def test_attribute_exists_missing_item_is_rejected(table):
    with pytest.raises(ConditionalCheckFailedException):
        table.put_item(
            {"order_id": "missing"},
            ConditionExpression="attribute_exists(order_id)",
        )


def test_update_rejects_unsupported_expression(table):
    table.put_item({"order_id": "order-1", "status": "pending"})

    with pytest.raises(UnsupportedFeature):
        table.update_item(
            {"order_id": "order-1"},
            "ADD quantity :one",
            ExpressionAttributeValues={":one": 1},
        )


def test_update_rejects_unknown_placeholder(table):
    table.put_item({"order_id": "order-1", "status": "pending"})

    with pytest.raises(UnsupportedFeature):
        table.update_item(
            {"order_id": "order-1"},
            "SET status = :missing",
            ExpressionAttributeValues={},
        )


def test_query_rejects_invalid_key_expression(table):
    with pytest.raises(UnsupportedFeature):
        table.query("customer_id = :customer")


def test_decimal_values_round_trip(table):
    table.put_item(
        {
            "order_id": "decimal-order",
            "total_cents": Decimal("1234"),
        }
    )

    item = table.get_item({"order_id": "decimal-order"})["Item"]
    assert item["total_cents"] == Decimal("1234")


def test_resource_is_lazy(monkeypatch):
    calls = []

    class FakeConnection:
        closed = False

    def fake_connect(database_url):
        calls.append(database_url)
        return FakeConnection()

    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:55432/test")
    monkeypatch.setattr("psycopg.connect", fake_connect)

    kv = resource()
    assert kv.connection is None
    assert calls == []


def test_boto3_key_condition_is_accepted():
    from boto3.dynamodb.conditions import Key

    from sovereign_sdk.kv import Table

    table = Table(None, "orders")
    condition = Key("customer_id").eq("customer-1")
    assert table._parse_query_condition(condition) == (
        "customer_id",
        "customer-1",
    )
