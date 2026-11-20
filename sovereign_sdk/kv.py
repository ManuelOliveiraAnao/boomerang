"""DynamoDB-like key-value adapter on Postgres. Owner B, task B5.

Spec: docs/02 §5, docs/15 L6/L15. Stub created by C1: B replaces the bodies.

Proposed for B: ``Table.query`` accepts boto3 ``Key(x).eq(v)`` conditions and
``ConditionalCheckFailedException`` subclasses ``botocore.exceptions.ClientError`` (L6); ``resource()``
and ``Table()`` open no connection, the first real call does, a dropped connection is reopened (L15).
"""

from __future__ import annotations

from boomerang._stub import NotBuilt

_DOC = "docs/02 §5"


class UnsupportedFeature(Exception):
    """An expression or feature the adapter doesn't support (→ 🔴 in the traffic lights)."""


def resource() -> KV:
    """Reads DATABASE_URL."""
    raise NotBuilt("sovereign_sdk.kv.resource", owner="B", doc=_DOC)


def ddl(component) -> str:
    """Postgres schema for one kv_table component (used by the generator for schema.sql)."""
    raise NotBuilt("sovereign_sdk.kv.ddl", owner="B", doc=_DOC)


class KV:
    def Table(self, name: str) -> Table:
        raise NotBuilt("sovereign_sdk.kv.KV.Table", owner="B", doc=_DOC)


class Table:
    def put_item(self, Item: dict, ConditionExpression=None) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.put_item", owner="B", doc=_DOC)

    def get_item(self, Key: dict) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.get_item", owner="B", doc=_DOC)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, ConditionExpression=None) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.update_item", owner="B", doc=_DOC)

    def delete_item(self, Key: dict) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.delete_item", owner="B", doc=_DOC)

    def query(self, KeyConditionExpression, IndexName=None) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.query", owner="B", doc=_DOC)

    def scan(self) -> dict:
        raise NotBuilt("sovereign_sdk.kv.Table.scan", owner="B", doc=_DOC)
