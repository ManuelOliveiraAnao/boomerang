"""PostgreSQL implementation of a small boto3 DynamoDB-like KV adapter."""

from __future__ import annotations

import json
import os
import re
import threading
from decimal import Decimal
from typing import Any

import psycopg

try:
    from botocore.exceptions import ClientError
except ImportError:
    class ClientError(Exception):
        pass


class UnsupportedFeature(Exception):
    """An expression or feature that the KV adapter does not support."""


class ConditionalCheckFailedException(ClientError):
    def __init__(self) -> None:
        super().__init__(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Conditional check failed",
                }
            },
            "PutItem",
        )


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise UnsupportedFeature(f"Unsafe SQL identifier: {value}")
    return value


def _quoted(value: str) -> str:
    return f'"{_identifier(value)}"'


def _component_value(component: Any, name: str, default: Any = None) -> Any:
    if hasattr(component, name):
        return getattr(component, name)
    if isinstance(component, dict):
        return component.get(name, default)
    return default


def _key_attr(component: Any, name: str) -> str | None:
    key = _component_value(component, name)
    if key is None:
        return None
    if isinstance(key, dict):
        return key.get("name")
    return getattr(key, "name", None)


def ddl(component: Any) -> str:
    """Generate Postgres DDL for one DynamoDB-compatible table."""
    table_name = _component_value(component, "resource_name") or _component_value(component, "id")
    resource_name = _component_value(component, "resource_name")
    if resource_name is not None and not str(resource_name).strip():
        raise UnsupportedFeature("A KV table must have a resource name")
    if table_name is None or not str(table_name).strip():
        raise UnsupportedFeature("A KV table must have a resource name")
    table_name = _identifier(str(table_name))

    partition_key = _key_attr(component, "partition_key")
    sort_key = _key_attr(component, "sort_key")
    if partition_key is None:
        raise UnsupportedFeature("A KV table must have a partition key")
    partition_key = _identifier(partition_key)
    sort_key = _identifier(sort_key) if sort_key else None

    indexes = _component_value(component, "indexes", []) or []
    index_keys: set[str] = set()
    for index in indexes:
        if isinstance(index, dict):
            partition = index.get("partition_key", {}).get("name")
            sort = index.get("sort_key")
            if isinstance(sort, dict):
                sort = sort.get("name")
        else:
            partition = getattr(getattr(index, "partition_key", None), "name", None)
            sort = getattr(getattr(index, "sort_key", None), "name", None)
        if partition:
            index_keys.add(_identifier(partition))
        if sort:
            index_keys.add(_identifier(sort))

    lines = [f"{_quoted(partition_key)} TEXT NOT NULL"]
    if sort_key:
        lines.append(f"{_quoted(sort_key)} TEXT NOT NULL")
    for index_key in sorted(index_keys):
        if index_key not in {partition_key, sort_key}:
            lines.append(f"{_quoted(index_key)} TEXT")
    lines.append("data JSONB NOT NULL")
    primary = [partition_key] + ([sort_key] if sort_key else [])
    statements = [
        f"CREATE TABLE IF NOT EXISTS {_quoted(table_name)} (\n"
        + "  "
        + ",\n  ".join(lines)
        + f",\n  PRIMARY KEY ({', '.join(_quoted(key) for key in primary)})\n);"
    ]

    for index in indexes:
        if isinstance(index, dict):
            index_name = index.get("name")
            partition = index.get("partition_key", {}).get("name")
            sort = index.get("sort_key")
            if isinstance(sort, dict):
                sort = sort.get("name")
        else:
            index_name = getattr(index, "name", None)
            partition = getattr(getattr(index, "partition_key", None), "name", None)
            sort = getattr(getattr(index, "sort_key", None), "name", None)
        if not index_name or not partition:
            continue
        columns = [_identifier(partition)]
        if sort:
            columns.append(_identifier(sort))
        index_name = str(index_name).replace("-", "_")
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {_quoted(index_name)} "
            f"ON {_quoted(table_name)} ({', '.join(_quoted(column) for column in columns)});"
        )
    return "\n".join(statements)


def resource(database_url: str | None = None) -> "KV":
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return KV(url)


class KV:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.connection = None
        self._lock = threading.RLock()

    def _get_connection(self):
        with self._lock:
            if self.connection is None or getattr(self.connection, "closed", False):
                self.connection = psycopg.connect(self.database_url)
            return self.connection

    def Table(self, name: str) -> "Table":
        return Table(self, name)

    def close(self) -> None:
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None


class Key:
    def __init__(self, name: str) -> None:
        self.name = name

    def eq(self, value: Any) -> tuple[str, Any]:
        return self.name, value


def _decimal_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _normalize_item(item: dict) -> str:
    return json.dumps(item, default=_decimal_json)


def _to_decimal(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    return value


def _decode_item(value: Any) -> dict:
    if isinstance(value, str):
        return json.loads(value, parse_float=Decimal, parse_int=Decimal)
    if isinstance(value, dict):
        return _to_decimal(value)
    return {}


_CONDITION_RE = re.compile(r"^\s*attribute_(exists|not_exists)\(\s*(#?[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$")


def _parse_env_schema() -> dict[str, dict[str, Any]]:
    raw = os.getenv("BOOMERANG_KV_SCHEMA", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class Table:
    def __init__(self, owner: KV, name: str) -> None:
        self.owner = owner
        self.name = _identifier(name)
        self._pk_cache: str | None = None
        self._sk_cache: str | None = None
        self._cols_cache: list[str] | None = None
        self._schema_loaded: bool = False

    def _connection(self):
        return self.owner._get_connection()

    def invalidate_schema_cache(self) -> None:
        self._pk_cache = None
        self._sk_cache = None
        self._cols_cache = None
        self._schema_loaded = False

    def _load_schema(self) -> None:
        if self._schema_loaded:
            return
        env_entry = _parse_env_schema().get(self.name)
        if isinstance(env_entry, dict) and isinstance(env_entry.get("partition_key"), str):
            pk = _identifier(env_entry["partition_key"])
            sk_raw = env_entry.get("sort_key")
            sk = _identifier(sk_raw) if isinstance(sk_raw, str) and sk_raw else None
            cols_raw = env_entry.get("columns")
            cols = (
                [_identifier(str(col)) for col in cols_raw]
                if isinstance(cols_raw, list)
                else ([pk] + ([sk] if sk else []))
            )
            self._pk_cache = pk
            self._sk_cache = sk
            self._cols_cache = cols
            self._schema_loaded = True
            return

        with self.owner._lock:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    """
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                    WHERE c.relname = %s AND i.indisprimary
                    ORDER BY array_position(i.indkey, a.attnum)
                    """,
                    (self.name,),
                )
                pk_rows = [str(row[0]) for row in cursor.fetchall()]
                if not pk_rows:
                    raise RuntimeError(f"No primary key found for table {self.name}")
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                      AND column_name <> 'data'
                    """,
                    (self.name,),
                )
                cols = [str(row[0]) for row in cursor.fetchall()]
        self._pk_cache = pk_rows[0]
        self._sk_cache = pk_rows[1] if len(pk_rows) > 1 else None
        self._cols_cache = cols
        self._schema_loaded = True

    def _partition_key(self) -> str:
        self._load_schema()
        assert self._pk_cache is not None
        return self._pk_cache

    def _sort_key(self) -> str | None:
        self._load_schema()
        return self._sk_cache

    def _data_columns(self) -> list[str]:
        self._load_schema()
        return list(self._cols_cache or [])

    def _fetch_item(self, Key: dict, partition_key: str, sort_key: str | None) -> dict | None:
        if partition_key not in Key:
            raise ValueError(f"Missing partition key {partition_key}")
        sql = f"SELECT data FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s"
        parameters: list[Any] = [str(Key[partition_key])]
        if sort_key:
            if sort_key not in Key:
                raise ValueError(f"Missing sort key {sort_key}")
            sql += f" AND {_quoted(sort_key)} = %s"
            parameters.append(str(Key[sort_key]))
        with self._connection().cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
        if not row:
            return None
        return _decode_item(row[0])

    def _check_condition(
        self,
        condition: str,
        *,
        existing_item: dict | None,
        partition_key: str,
        sort_key: str | None,
        expression_attribute_names: dict[str, str] | None = None,
    ) -> None:
        match = _CONDITION_RE.match(condition)
        if not match:
            raise UnsupportedFeature(f"Unsupported ConditionExpression: {condition}")
        op, raw_attr = match.group(1), match.group(2)
        if raw_attr.startswith("#"):
            names = expression_attribute_names or {}
            if raw_attr in names:
                attr_name = _identifier(names[raw_attr])
            elif raw_attr == "#pk":
                attr_name = partition_key
            else:
                raise UnsupportedFeature(f"Unknown condition attribute name: {raw_attr}")
        else:
            attr_name = _identifier(raw_attr)

        if attr_name == partition_key or (sort_key is not None and attr_name == sort_key):
            present = existing_item is not None
        else:
            present = existing_item is not None and attr_name in existing_item

        if op == "exists" and not present:
            raise ConditionalCheckFailedException()
        if op == "not_exists" and present:
            raise ConditionalCheckFailedException()

    def put_item(self, Item: dict, ConditionExpression: str | None = None) -> dict:
        with self.owner._lock:
            partition_key = self._partition_key()
            sort_key = self._sort_key()
            if partition_key not in Item:
                raise ValueError(f"Missing partition key {partition_key}")
            if sort_key and sort_key not in Item:
                raise ValueError(f"Missing sort key {sort_key}")

            if ConditionExpression:
                existing = self._fetch_item(Item, partition_key, sort_key)
                self._check_condition(
                    ConditionExpression,
                    existing_item=existing,
                    partition_key=partition_key,
                    sort_key=sort_key,
                )

            columns = [partition_key] + ([sort_key] if sort_key else [])
            values = [str(Item[partition_key])] + ([str(Item[sort_key])] if sort_key else [])
            for column in self._data_columns():
                if column not in columns and column in Item:
                    columns.append(column)
                    values.append(str(Item[column]))

            column_names = ", ".join(_quoted(column) for column in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            primary_names = ", ".join(_quoted(column) for column in ([partition_key] + ([sort_key] if sort_key else [])))
            update_columns = ", ".join(
                f"{_quoted(column)} = EXCLUDED.{_quoted(column)}"
                for column in columns
            )
            with self._connection().cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {_quoted(self.name)} ({column_names}, data) "
                    f"VALUES ({placeholders}, %s::jsonb) "
                    f"ON CONFLICT ({primary_names}) DO UPDATE SET data = EXCLUDED.data, {update_columns}",
                    (*values, _normalize_item(Item)),
                )
            self._connection().commit()
        return {}

    def get_item(self, Key: dict) -> dict:
        with self.owner._lock:
            partition_key = self._partition_key()
            sort_key = self._sort_key()
            item = self._fetch_item(Key, partition_key, sort_key)
        if item is None:
            return {}
        return {"Item": item}

    def update_item(
        self,
        Key: dict,
        UpdateExpression: str,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
        ConditionExpression: str | None = None,
    ) -> dict:
        with self.owner._lock:
            if not UpdateExpression.strip().upper().startswith("SET "):
                raise UnsupportedFeature("Only SET update expressions are supported")
            names = ExpressionAttributeNames or {}
            values = ExpressionAttributeValues or {}
            assignments: list[tuple[str, Any]] = []
            for assignment in UpdateExpression.strip()[4:].split(","):
                if "=" not in assignment:
                    raise UnsupportedFeature(f"Invalid update assignment: {assignment}")
                left, right = (part.strip() for part in assignment.split("=", 1))
                target = names.get(left, left)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
                    raise UnsupportedFeature(f"Invalid update target: {target}")
                if right not in values:
                    raise UnsupportedFeature(f"Unknown update value: {right}")
                assignments.append((target, values[right]))

            partition_key = self._partition_key()
            sort_key = self._sort_key()
            primary = [partition_key] + ([sort_key] if sort_key else [])
            for key in primary:
                if key not in Key:
                    raise ValueError(f"Missing key {key}")

            existing = self._fetch_item(Key, partition_key, sort_key)
            if ConditionExpression:
                self._check_condition(
                    ConditionExpression,
                    existing_item=existing,
                    partition_key=partition_key,
                    sort_key=sort_key,
                    expression_attribute_names=ExpressionAttributeNames,
                )
            item = dict(existing) if existing is not None else dict(Key)
            for target, value in assignments:
                item[target] = value

            replace_columns = list(primary)
            replace_values = [str(Key[key]) for key in primary]
            for column in self._data_columns():
                if column not in replace_columns and column in item:
                    replace_columns.append(column)
                    replace_values.append(str(item[column]))
            set_sql = ", ".join(
                f"{_quoted(column)} = EXCLUDED.{_quoted(column)}"
                for column in replace_columns
            )
            column_names = ", ".join(_quoted(column) for column in replace_columns)
            placeholders = ", ".join(["%s"] * len(replace_columns))
            primary_names = ", ".join(_quoted(column) for column in primary)
            with self._connection().cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {_quoted(self.name)} ({column_names}, data) "
                    f"VALUES ({placeholders}, %s::jsonb) "
                    f"ON CONFLICT ({primary_names}) DO UPDATE SET data = EXCLUDED.data, {set_sql}",
                    (*replace_values, _normalize_item(item)),
                )
            self._connection().commit()
        return {}

    def delete_item(self, Key: dict) -> dict:
        with self.owner._lock:
            partition_key = self._partition_key()
            sort_key = self._sort_key()
            if partition_key not in Key:
                raise ValueError(f"Missing partition key {partition_key}")
            sql = f"DELETE FROM {_quoted(self.name)} WHERE {_quoted(partition_key)} = %s"
            parameters: list[Any] = [str(Key[partition_key])]
            if sort_key:
                if sort_key not in Key:
                    raise ValueError(f"Missing sort key {sort_key}")
                sql += f" AND {_quoted(sort_key)} = %s"
                parameters.append(str(Key[sort_key]))
            with self._connection().cursor() as cursor:
                cursor.execute(sql, parameters)
            self._connection().commit()
        return {}

    def _parse_query_condition(self, condition: Any) -> tuple[str, Any]:
        if isinstance(condition, tuple) and len(condition) == 2:
            key_name, value = condition
            if isinstance(key_name, str):
                return key_name, value

        if hasattr(condition, "_values"):
            key, value = condition._values
            key_name = getattr(key, "name", None)
            if key_name:
                return str(key_name), value

        if hasattr(condition, "get_expression"):
            expression = condition.get_expression()
            values = expression.get("values", ())
            if values:
                key = getattr(values[0], "name", None)
                if key:
                    value = expression.get("value")
                    if value is None and len(values) > 1:
                        value = values[-1]
                    return str(key), value

        raise UnsupportedFeature("Only Key(...).eq(...) conditions are supported")

    def query(self, KeyConditionExpression: Any, IndexName: str | None = None) -> dict:
        key_name, value = self._parse_query_condition(KeyConditionExpression)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_name):
            raise UnsupportedFeature(f"Invalid query key: {key_name}")
        with self.owner._lock:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {_quoted(self.name)} WHERE {_quoted(key_name)} = %s",
                    (str(value),),
                )
                rows = cursor.fetchall()
        return {"Items": [_decode_item(row[0]) for row in rows]}

    def scan(self) -> dict:
        with self.owner._lock:
            with self._connection().cursor() as cursor:
                cursor.execute(f"SELECT data FROM {_quoted(self.name)}")
                rows = cursor.fetchall()
        return {"Items": [_decode_item(row[0]) for row in rows]}
