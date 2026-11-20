from __future__ import annotations

import json
import os
import re
import threading
from decimal import Decimal
from typing import Iterable

try:
    import psycopg
except ImportError:  # pragma: no cover - reported clearly at runtime
    psycopg = None

try:
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - handled by project dependencies
    ClientError = Exception


class UnsupportedFeature(Exception):
    """Raised for DynamoDB expressions unsupported by the KV adapter."""


class ConditionalCheckFailedException(ClientError):
    """Boto3-like exception for a failed conditional write."""

    def __init__(self) -> None:
        super().__init__(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Conditional check failed"}},
            "PutItem",
        )


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise UnsupportedFeature(f"Unsafe SQL identifier: {value}")
    return value


def _quoted(value: str) -> str:
    return f'"{_identifier(value)}"'


def _component_value(component: object, name: str, default=None):
    if hasattr(component, name):
        return getattr(component, name)
    if isinstance(component, dict):
        return component.get(name, default)
    return default


def _key_attr(component: object, name: str) -> str | None:
    key = _component_value(component, name)
    if key is None:
        return None
    if isinstance(key, dict):
        return key.get("name")
    return getattr(key, "name", None)


def _decimal_default(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _normalize_item(item: dict) -> str:
    return json.dumps(item, default=_decimal_default)


def _decode_item(value) -> dict:
    if isinstance(value, str):
        return json.loads(value, parse_float=Decimal, parse_int=Decimal)
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    raise TypeError("Unexpected JSONB value")


def ddl(component: object) -> str:
    """Generate the Postgres DDL for one DynamoDB-compatible table component."""
    resource_name = _component_value(component, "resource_name")
    if resource_name is not None and str(resource_name).strip() == "":
        raise UnsupportedFeature("A KV table must have a resource name")
    table_name = resource_name or _component_value(component, "id")
    table_name = _identifier(str(table_name))
    partition_key = _key_attr(component, "partition_key")
    sort_key = _key_attr(component, "sort_key")
    if partition_key is None:
        raise UnsupportedFeature("A KV table must have a partition key")

    partition_key = _identifier(partition_key)
    sort_key = _identifier(sort_key) if sort_key else None
    lines = [f"{_quoted(partition_key)} TEXT NOT NULL"]
    if sort_key:
        lines.append(f"{_quoted(sort_key)} TEXT NOT NULL")
    indexes = _component_value(component, "indexes", []) or []
    index_keys: set[str] = set()
    for index in indexes:
        if isinstance(index, dict):
            index_partition = index.get("partition_key", {}).get("name")
            index_sort = index.get("sort_key")
            if isinstance(index_sort, dict):
                index_sort = index_sort.get("name")
        else:
            index_partition = getattr(index.partition_key, "name", None)
            index_sort = getattr(getattr(index, "sort_key", None), "name", None)
        if index_partition:
            index_keys.add(_identifier(index_partition))
        if index_sort:
            index_keys.add(_identifier(index_sort))
    for index_key in sorted(index_keys):
        if index_key not in {partition_key, sort_key}:
            lines.append(f"{_quoted(index_key)} TEXT")

    lines.append("data JSONB NOT NULL")
    primary_keys = [partition_key] + ([sort_key] if sort_key else [])
    primary_sql = ", ".join(_quoted(key) for key in primary_keys)
    statements = [
        f"CREATE TABLE IF NOT EXISTS {_quoted(table_name)} (\n  "
        + ",\n  ".join(lines)
        + f",\n  PRIMARY KEY ({primary_sql})\n);"
    ]
    for index in indexes:
        if isinstance(index, dict):
            index_name = index.get("name")
            index_partition = index.get("partition_key", {}).get("name")
            index_sort = index.get("sort_key")
            if isinstance(index_sort, dict):
                index_sort = index_sort.get("name")
        else:
            index_name = getattr(index, "name", None)
            index_partition = getattr(index.partition_key, "name", None)
            index_sort = getattr(getattr(index, "sort_key", None), "name", None)
        if not index_name or not index_partition:
            continue
        index_columns = [_identifier(index_partition)]
        if index_sort:
            index_columns.append(_identifier(index_sort))
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {_quoted(str(index_name).replace('-', '_'))} "
            f"ON {_quoted(table_name)} ({', '.join(_quoted(key) for key in index_columns)});"
        )
    return "\n".join(statements)


def resource(database_url: str | None = None) -> "KV":
    """Create a lazy KV resource; the database connects on the first real operation."""
    if psycopg is None:
        raise RuntimeError("psycopg is required to use the KV adapter")
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return KV(database_url)


class KV:
    def __init__(self, database_url: str | object) -> None:
        self.database_url = (
            str(database_url)
            if not hasattr(database_url, "execute")
            else None
        )
        self.connection = (
            database_url if hasattr(database_url, "execute") else None
        )
        self._lock = threading.RLock()

    def _get_connection(self):
        with self._lock:
            if self.connection is None or getattr(self.connection, "closed", False):
                self.connection = psycopg.connect(self.database_url)
            return self.connection

    def Table(self, name: str) -> "Table":
        return Table(self, name)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


class Key:
    def __init__(self, name: str) -> None:
        self.name = name

    def eq(self, value) -> tuple[str, object]:
        return self.name, value


def _condition_parse(expression: str | None) -> list[tuple[str, str, str]]:
    if expression is None:
        return []
    if expression in {"attribute_not_exists(order_id)", "attribute_not_exists(#pk)"}:
        return [("order_id", "not_exists", "")]
    if expression in {"attribute_exists(order_id)", "attribute_exists(#pk)"}:
        return [("order_id", "exists", "")]
    raise UnsupportedFeature(f"Unsupported ConditionExpression: {expression}")


class Table:
    def __init__(self, connection, name: str) -> None:
        self.owner = connection if isinstance(connection, KV) else None
        self.connection = connection if hasattr(connection, "execute") else None
        self.name = _identifier(name)

    def _connection(self):
        if self.owner is not None:
            return self.owner._get_connection()
        if self.connection is None:
            raise RuntimeError("No database connection is configured")
        return self.connection

    def _parse_query_condition(self, condition) -> tuple[str, object]:
        if isinstance(condition, tuple) and len(condition) == 2:
            key_name, value = condition
            if isinstance(key_name, str):
                return key_name, value

        if hasattr(condition, "get_expression"):
            expression = condition.get_expression()
            values = expression.get("values", ())
            if not values:
                raise UnsupportedFeature("Only Key(...).eq(...) conditions are supported")
            key_name = getattr(values[0], "name", None)
            if not key_name:
                return getattr(condition, "key_name", ""), getattr(condition, "value", None)
            value = expression["value"] if "value" in expression else (
                values[-1] if len(values) > 1 else None
            )
            return str(key_name), value

        if hasattr(condition, "_values"):
            key, value = condition._values
            key_name = getattr(key, "name", None)
            if key_name is not None:
                return str(key_name), value

        raise UnsupportedFeature("Only Key(...).eq(...) conditions are supported")

    def _data_columns(self) -> list[str]:
        with self._connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                  AND column_name <> 'data'
                """,
                (self.name,),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def _partition_key(self) -> str:
        with self._connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                WHERE c.relname = %s AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                LIMIT 1
                """,
                (self.name,),
            )
            row = cursor.fetchone()
        if not row:
            raise RuntimeError(f"No primary key found for table {self.name}")
        return str(row[0])

    def put_item(self, Item: dict, ConditionExpression=None) -> dict:
        partition_key = self._partition_key()
        partition_value = Item.get(partition_key)
        if partition_value is None:
            raise ValueError(f"Missing partition key {partition_key}")
        with self._connection().cursor() as cursor:
            conditions = _condition_parse(ConditionExpression)
            if conditions:
                name, mode, _ = conditions[0]
                if mode == "not_exists":
                    cursor.execute(
                        f"SELECT 1 FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s",
                        (str(partition_value),),
                    )
                    if cursor.fetchone():
                        raise ConditionalCheckFailedException()
                elif mode == "exists":
                    cursor.execute(
                        f"SELECT 1 FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s",
                        (str(partition_value),),
                    )
                    if not cursor.fetchone():
                        raise ConditionalCheckFailedException()

            columns = [partition_key]
            values = [str(partition_value)]
            for sort_key in ([self._sort_key()] if self._sort_key() else []):
                if sort_key not in Item:
                    raise ValueError(f"Missing sort key {sort_key}")
                columns.append(sort_key)
                values.append(str(Item[sort_key]))
            for column in self._data_columns():
                if column not in columns and column in Item:
                    columns.append(column)
                    values.append(str(Item[column]))

            column_names = ", ".join(_quoted(column) for column in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            update_columns = ", ".join(
                f"{_quoted(column)} = EXCLUDED.{_quoted(column)}"
                for column in columns
            )
            primary_columns = [partition_key]
            sort_key_row = self._sort_key()
            if sort_key_row:
                primary_columns.append(sort_key_row)
            primary_names = ", ".join(_quoted(column) for column in primary_columns)
            cursor.execute(
                f"INSERT INTO {_quoted(self.name)} ({column_names}, data) "
                f"VALUES ({placeholders}, %s::jsonb) "
                f"ON CONFLICT ({primary_names}) DO UPDATE SET data = EXCLUDED.data, "
                f"{update_columns}",
                (*values, _normalize_item(Item)),
            )
        self._connection().commit()
        return {}

    def _sort_key(self) -> str | None:
        with self._connection().cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                WHERE c.relname = %s AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                OFFSET 1
                LIMIT 1
                """,
                (self.name,),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def get_item(self, Key: dict) -> dict:
        partition_key = self._partition_key()
        partition_value = Key.get(partition_key)
        if partition_value is None:
            raise ValueError(f"Missing partition key {partition_key}")
        sort_key = self._sort_key()
        sql = f"SELECT data FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s"
        parameters: list[object] = [str(partition_value)]
        if sort_key:
            if sort_key not in Key:
                raise ValueError(f"Missing sort key {sort_key}")
            sql += f" AND {_quoted(sort_key)} = %s"
            parameters.append(str(Key[sort_key]))
        with self._connection().cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
        if not row:
            return {}
        return {"Item": _decode_item(row[0])}

    def update_item(
        self,
        Key: dict,
        UpdateExpression: str,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
        ConditionExpression=None,
    ) -> dict:
        item = self.get_item(Key).get("Item")
        if item is None:
            raise ConditionalCheckFailedException()
        if not UpdateExpression.strip().upper().startswith("SET "):
            raise UnsupportedFeature("Only SET update expressions are supported")
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        set_body = UpdateExpression.strip()[4:]
        for assignment in set_body.split(","):
            left, right = (part.strip() for part in assignment.split("=", 1))
            target = names.get(left, left)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
                raise UnsupportedFeature(f"Invalid update target: {target}")
            if right not in values:
                raise UnsupportedFeature(f"Unknown update value: {right}")
            item[target] = values[right]

        partition_key = self._partition_key()
        sort_key = self._sort_key()
        columns = [partition_key] + ([sort_key] if sort_key else [])
        key_values = [str(Key[column]) for column in columns]
        self._replace_item(item, columns, key_values)
        return {}

    def _replace_item(self, item: dict, columns: list[str], values: list[str]) -> None:
        replace_columns = list(columns)
        replace_values = list(values)
        for column in self._data_columns():
            if column not in replace_columns and column in item:
                replace_columns.append(column)
                replace_values.append(str(item[column]))
        set_sql = ", ".join(
            f"{_quoted(column)} = EXCLUDED.{_quoted(column)}" for column in replace_columns
        )
        column_names = ", ".join(_quoted(column) for column in replace_columns)
        placeholders = ", ".join(["%s"] * len(replace_columns))
        conflict_columns = ", ".join(_quoted(column) for column in columns)
        with self._connection().cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {_quoted(self.name)} ({column_names}, data) "
                f"VALUES ({placeholders}, %s::jsonb) "
                f"ON CONFLICT ({conflict_columns}) DO UPDATE SET data = EXCLUDED.data, {set_sql}",
                (*replace_values, _normalize_item(item)),
            )
        self._connection().commit()

    def delete_item(self, Key: dict) -> dict:
        partition_key = self._partition_key()
        if partition_key not in Key:
            raise ValueError(f"Missing partition key {partition_key}")
        sort_key = self._sort_key()
        sql = f"DELETE FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s"
        parameters: list[object] = [str(Key[partition_key])]
        if sort_key:
            if sort_key not in Key:
                raise ValueError(f"Missing sort key {sort_key}")
            sql += f" AND {_quoted(sort_key)} = %s"
            parameters.append(str(Key[sort_key]))
        with self._connection().cursor() as cursor:
            cursor.execute(sql, parameters)
        self._connection().commit()
        return {}

    def query(self, KeyConditionExpression, IndexName=None) -> dict:
        key_name, value = self._parse_query_condition(KeyConditionExpression)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_name):
            raise UnsupportedFeature(f"Invalid query key: {key_name}")
        sql = f"SELECT data FROM {_quoted(self.name)} WHERE {_quoted(key_name)} = %s"
        if IndexName:
            # GSI columns are populated in put_item and selected directly.
            pass
        with self._connection().cursor() as cursor:
            cursor.execute(sql, (str(value),))
            rows = cursor.fetchall()
        return {"Items": [_decode_item(row[0]) for row in rows]}

    def scan(self) -> dict:
        with self._connection().cursor() as cursor:
            cursor.execute(f"SELECT data FROM {_quoted(self.name)}")
            rows = cursor.fetchall()
        return {"Items": [_decode_item(row[0]) for row in rows]}
