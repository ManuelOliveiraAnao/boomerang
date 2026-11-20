import json
from decimal import Decimal

import pytest

from boomerang.assess import assess
from boomerang.lockin import WEIGHTS, score
from boomerang.lost import build
from boomerang.scanner.static import scan_static
from sovereign_sdk.kv import (
    ConditionalCheckFailedException,
    UnsupportedFeature,
    ddl,
)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("aws.dynamodb", 90),
        ("aws.sqs", 70),
        ("aws.s3", 35),
        ("aws.lambda", 95),
        ("aws.iam", 80),
        ("lw.postgres", 10),
        ("lw.rabbitmq", 10),
        ("lw.object_storage", 10),
        ("none", 10),
        ("aws.cloudwatch", 10),
    ],
)
def test_lockin_provider_weights(provider, expected):
    assert score({"components": [{"provider": provider, "usages": []}]}) == expected


@pytest.mark.parametrize(
    "provider",
    [
        "aws.dynamodb",
        "aws.sqs",
        "aws.s3",
        "aws.lambda",
        "aws.iam",
        "aws.cloudwatch",
        "aws.other",
        "lw.postgres",
        "lw.rabbitmq",
        "lw.object_storage",
        "none",
    ],
)
def test_lockin_usage_count_is_minimum_one(provider):
    assert score({"components": [{"provider": provider, "usages": []}]}) >= 10


@pytest.mark.parametrize(
    ("kind", "features", "expected_light"),
    [
        ("object_store", ["put"], "green"),
        ("object_store", ["get"], "green"),
        ("object_store", ["list"], "green"),
        ("object_store", ["delete"], "green"),
        ("object_store", ["head"], "green"),
        ("object_store", ["event_notification"], "red"),
        ("kv_table", ["conditional_write"], "amber"),
        ("kv_table", ["gsi"], "amber"),
        ("kv_table", ["put"], "amber"),
        ("kv_table", ["transactions"], "red"),
        ("kv_table", ["streams"], "red"),
        ("kv_table", ["ttl"], "red"),
        ("queue", ["send"], "amber"),
        ("queue", ["receive"], "amber"),
        ("queue", ["delete"], "amber"),
        ("queue", ["visibility_timeout"], "amber"),
        ("queue", ["dlq"], "amber"),
        ("queue", ["fifo"], "red"),
        ("function", ["event_notification"], "red"),
        ("identity", ["role"], "red"),
        ("identity", ["policy"], "red"),
        ("monitoring", ["alarm"], "red"),
    ],
)
def test_assessment_rule_matrix(kind, features, expected_light):
    assessment = assess(
        {
            "components": [
                {
                    "id": "component",
                    "kind": kind,
                    "provider": "unknown",
                    "features": features,
                    "usages": [],
                }
            ]
        }
    )
    assert assessment["findings"][0]["light"] == expected_light


@pytest.mark.parametrize(
    ("light", "status"),
    [
        ("green", "preserved"),
        ("amber", "changed"),
        ("red", "lost"),
    ],
)
def test_lost_report_status_matrix(light, status):
    pam = {"components": [{"id": "component"}]}
    assessment = {
        "findings": [
            {
                "component_id": "component",
                "feature": "feature",
                "light": light,
                "what_changes": "details",
            }
        ]
    }
    lost = build(pam, assessment)
    assert lost[0]["status"] == status


@pytest.mark.parametrize(
    "notes",
    [
        [],
        ["Decimal normalized"],
        ["Object metadata not copied"],
        ["In-flight messages excluded"],
        ["Decimal normalized", "Object metadata not copied"],
    ],
)
def test_lost_report_adds_data_move_notes(notes):
    pam = {"components": [{"id": "orders", "kind": "kv_table"}]}
    assessment = {"findings": []}
    data_moves = {
        "counts": [
            {
                "component_id": "orders",
                "kind": "kv_table",
                "source_count": 25,
                "target_count": 25,
                "checksum_match": True,
                "notes": notes,
            }
        ]
    }
    lost = build(pam, assessment, data_moves)
    if notes:
        assert lost[-1]["status"] == "changed"
        assert lost[-1]["detail"] == "; ".join(notes)
    else:
        assert lost[-1]["status"] == "preserved"


@pytest.mark.parametrize(
    ("service", "kind"),
    [
        ("dynamodb", "kv_table"),
        ("s3", "object_store"),
        ("sqs", "queue"),
    ],
)
def test_static_scanner_python_services(tmp_path, service, kind):
    app = tmp_path / "app"
    app.mkdir()
    (app / "sample.py").write_text(
        "import boto3\n"
        f"client = boto3.client('{service}')\n"
        "def operation():\n"
        "    return client.list_objects_v2() if hasattr(client, 'list_objects_v2') else client\n",
        encoding="utf-8",
    )
    pam = scan_static(app)
    assert kind in {component["kind"] for component in pam["components"]}


@pytest.mark.parametrize(
    ("resource", "kind"),
    [
        ("aws_s3_bucket", "object_store"),
        ("aws_dynamodb_table", "kv_table"),
        ("aws_sqs_queue", "queue"),
        ("aws_lambda_function", "function"),
        ("aws_iam_role", "identity"),
        ("aws_iam_policy", "identity"),
        ("aws_cloudwatch_metric_alarm", "monitoring"),
    ],
)
def test_static_scanner_terraform_resource_matrix(tmp_path, resource, kind):
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.tf").write_text(
        f'resource "{resource}" "test_resource" {{}}\n',
        encoding="utf-8",
    )
    pam = scan_static(app)
    assert kind in {component["kind"] for component in pam["components"]}


@pytest.mark.parametrize(
    "env_line",
    [
        "AWS_REGION=us-east-1",
        "AWS_ACCESS_KEY_ID=test",
        "AWS_SECRET_ACCESS_KEY=test",
        "AWS_ENDPOINT_URL=http://127.0.0.1:4566",
    ],
)
def test_static_scanner_reads_aws_env(tmp_path, env_line):
    app = tmp_path / "app"
    app.mkdir()
    (app / ".env.example").write_text(env_line + "\n", encoding="utf-8")
    pam = scan_static(app)
    assert pam["env"][0]["name"] == env_line.split("=", 1)[0]


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM python:3.12-slim\n",
        "FROM python:3.12-slim\nENV APP_NAME=shoplite\n",
        "FROM python:3.12-slim\nRUN true\n",
    ],
)
def test_static_scanner_detects_implicit_iam(tmp_path, dockerfile):
    app = tmp_path / "app"
    app.mkdir()
    (app / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    pam = scan_static(app)
    assert "implicit_iam_role" in {component["id"] for component in pam["components"]}


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM python:3.12-slim\nENV AWS_REGION=us-east-1\n",
        "FROM python:3.12-slim\nENV AWS_ACCESS_KEY_ID=test\n",
        "FROM python:3.12-slim\nENV AWS_SECRET_ACCESS_KEY=test\n",
    ],
)
def test_static_scanner_does_not_add_implicit_iam_with_explicit_aws(tmp_path, dockerfile):
    app = tmp_path / "app"
    app.mkdir()
    (app / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    pam = scan_static(app)
    assert "implicit_iam_role" not in {component["id"] for component in pam["components"]}


@pytest.mark.parametrize(
    ("table_name", "partition", "sort_key", "index_count"),
    [
        ("empty", "id", None, 0),
        ("orders", "order_id", None, 1),
        ("events", "account_id", "event_id", 0),
        ("composite", "tenant_id", "item_id", 3),
    ],
)
def test_ddl_matrix(table_name, partition, sort_key, index_count):
    indexes = [
        {
            "name": f"index_{index}",
            "partition_key": {"name": f"key_{index}", "type": "S"},
            "sort_key": None,
        }
        for index in range(index_count)
    ]
    text = ddl(
        {
            "id": table_name,
            "resource_name": table_name,
            "partition_key": {"name": partition, "type": "S"},
            "sort_key": (
                {"name": sort_key, "type": "S"} if sort_key is not None else None
            ),
            "indexes": indexes,
        }
    )
    assert f'CREATE TABLE IF NOT EXISTS "{table_name}"' in text
    assert f'"{partition}" TEXT NOT NULL' in text
    if sort_key:
        assert f'"{sort_key}" TEXT NOT NULL' in text
    assert text.count("CREATE INDEX IF NOT EXISTS") == index_count


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "orders; DROP TABLE",
        "orders-table",
        "1orders",
        "order table",
        "order.table",
        "order/table",
        "order\"table",
        "order-table$",
    ],
)
def test_identifier_validation_rejects_bad_names(bad_name):
    with pytest.raises(UnsupportedFeature):
        ddl(
            {
                "id": "invalid",
                "resource_name": bad_name,
                "partition_key": {"name": "id", "type": "S"},
                "sort_key": None,
                "indexes": [],
            }
        )


@pytest.mark.parametrize(
    "bad_partition",
    [
        None,
        {},
        {"name": "", "type": "S"},
        {"name": "bad name", "type": "S"},
        {"name": "1bad", "type": "S"},
    ],
)
def test_ddl_rejects_bad_partition_keys(bad_partition):
    with pytest.raises((UnsupportedFeature, AttributeError)):
        ddl(
            {
                "id": "orders",
                "resource_name": "orders",
                "partition_key": bad_partition,
                "sort_key": None,
                "indexes": [],
            }
        )


@pytest.mark.parametrize(
    ("value", "json_safe"),
    [
        (Decimal("0"), True),
        (Decimal("1"), True),
        (Decimal("-1"), True),
        (Decimal("123456789"), True),
        (Decimal("1.25"), True),
        (Decimal("-0.125"), True),
    ],
)
def test_decimal_normalization_is_json_safe(value, json_safe):
    from sovereign_sdk.kv import _decimal_default

    result = _decimal_default(value)
    assert isinstance(result, (int, float))
    assert json_safe
