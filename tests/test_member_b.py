from __future__ import annotations

import json
from pathlib import Path

import pytest

from boomerang.assess import assess
from boomerang.generators.leaseweb import make_secrets, render
from boomerang.lockin import score
from boomerang.lost import build
from boomerang.models import AppModel, Assessment, Finding, DataMove, DataCount
from boomerang.scanner.static import scan_static
from sovereign_sdk.kv import ddl, UnsupportedFeature


def test_static_scanner_finds_shoplite_dependencies(tmp_path):
    app = tmp_path / "ShopLite"
    app.mkdir()
    (app / "app").mkdir()
    (app / "app" / "main.py").write_text(
        'import boto3\n'
        'dynamodb = boto3.resource("dynamodb")\n'
        's3 = boto3.client("s3")\n'
        'def put_order(order_id):\n'
        '    dynamodb.Table("orders").put_item(Item={"order_id": order_id})\n'
        '    s3.put_object(Bucket="receipts", Key=f"{order_id}.json", Body="{}")\n',
        encoding="utf-8",
    )
    (app / "infra").mkdir()
    (app / "infra" / "main.tf").write_text(
        'resource "aws_dynamodb_table" "orders" {}\n'
        'resource "aws_s3_bucket" "receipts" {}\n'
        'resource "aws_sqs_queue" "order_events" '
        '{ visibility_timeout_seconds = 30 redrive_policy = "{}" }\n'
        'resource "aws_lambda_function" "receipt_thumbnailer" {}\n'
        'resource "aws_iam_role" "shoplite_task" {}\n'
        'resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {}\n',
        encoding="utf-8",
    )

    pam = scan_static(app)
    kinds = {component.id: component.kind for component in pam.components}

    assert kinds["orders"] == "kv_table"
    assert kinds["receipts"] == "object_store"
    assert kinds["order_events"] == "queue"
    assert kinds["receipt_thumbnailer"] == "function"
    assert kinds["shoplite_task"] == "identity"
    assert kinds["dlq_not_empty"] == "monitoring"


def test_static_scanner_excludes_tests_and_sdk(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "tests").mkdir()
    (app / "sovereign_sdk").mkdir()
    (app / "tests" / "db.py").write_text(
        'import boto3\ndynamodb = boto3.resource("dynamodb")\n',
        encoding="utf-8",
    )
    (app / "sovereign_sdk" / "kv.py").write_text(
        'import boto3\ns3 = boto3.client("s3")\n',
        encoding="utf-8",
    )
    pam = scan_static(app)
    assert pam.components == []


def test_assessment_rules_and_ai_cannot_lower(monkeypatch):
    pam = AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=[],
        components=[
            {
                "id": "orders",
                "kind": "kv_table",
                "provider": "aws.dynamodb",
                "resource_name": "orders",
                "features": ["gsi", "conditional_write"],
            },
            {
                "id": "thumbnailer",
                "kind": "function",
                "provider": "aws.lambda",
                "resource_name": "thumbnailer",
                "features": ["event_notification"],
            },
        ],
        files=[],
        env=[],
    )
    fake = Assessment(
        findings=[
            Finding(
                analysis="AI tries to lower rules",
                component_id="orders",
                feature="gsi",
                light="green",
                what_changes="not valid",
            ),
            Finding(
                analysis="AI red explanation",
                component_id="thumbnailer",
                feature="event_notification",
                light="red",
                what_changes="Lambda notification needs a human",
            ),
        ],
        overall="green",
        summary="AI summary",
    )
    monkeypatch.setattr(
        "boomerang.llm.structured",
        lambda **kwargs: fake,
    )
    from boomerang.events import Emitter

    assessment = assess(pam, Emitter("test"))
    lights = {(finding.component_id, finding.feature): finding.light for finding in assessment.findings}
    assert lights[("orders", "gsi")] == "amber"
    assert lights[("thumbnailer", "event_notification")] == "red"
    assert assessment.overall == "red"


def test_lockin_score():
    pam = AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=[],
        components=[
            {
                "id": "orders",
                "kind": "kv_table",
                "provider": "aws.dynamodb",
                "resource_name": "orders",
                "usages": [{"file": "app/db.py", "line": 1, "operation": "put_item", "snippet": "x"}],
            },
            {
                "id": "receipts",
                "kind": "object_store",
                "provider": "aws.s3",
                "resource_name": "receipts",
                "usages": [],
            },
        ],
        files=[],
        env=[],
    )
    assert score(pam) == 0


def test_lost_report():
    pam = AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=[],
        components=[
            {"id": "orders", "kind": "kv_table", "provider": "aws.dynamodb", "resource_name": "orders"},
            {"id": "thumbnailer", "kind": "function", "provider": "aws.lambda", "resource_name": "thumbnailer"},
        ],
        files=[],
        env=[],
    )
    assessment = Assessment(
        findings=[
            Finding(
                analysis="GSI",
                component_id="orders",
                feature="gsi",
                light="amber",
                what_changes="Postgres index",
            ),
            Finding(
                analysis="Lambda",
                component_id="thumbnailer",
                feature="event_notification",
                light="red",
                what_changes="Not migrated",
            ),
        ],
        overall="red",
        summary="2 findings",
    )
    data = [
        DataMove(
            direction="aws->leaseweb",
            counts=[
                DataCount(
                    component_id="orders",
                    kind="kv_table",
                    source_count=25,
                    target_count=25,
                    checksum_match=True,
                    notes=["Decimal normalized"],
                )
            ],
        )
    ]
    lost = build(pam, assessment, data)
    statuses = {item.component_id: item.status for item in lost}
    assert statuses["orders"] == "changed"
    assert statuses["thumbnailer"] == "lost"


def test_generator_is_pure_and_contains_required_files():
    pam = AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=["app.main:app"],
        components=[
            {
                "id": "orders",
                "kind": "kv_table",
                "provider": "aws.dynamodb",
                "resource_name": "orders",
                "partition_key": {"name": "order_id", "type": "S"},
                "indexes": [{"name": "customer_id-index", "partition_key": {"name": "customer_id", "type": "S"}}],
            },
        ],
        files=[],
        env=[],
    )
    secrets = make_secrets(pam)
    files = render(pam, secrets)
    paths = {generated.path for generated in files}
    assert {
        "docker-compose.yml",
        "secrets.env",
        "laptop.env",
        "schema.sql",
        "deploy.sh",
        "backup/pg_backup.sh",
        "DEPLOY.md",
        "app/Dockerfile",
        "app/requirements.txt",
    }.issubset(paths)

    laptop = next(generated.content for generated in files if generated.path == "laptop.env")
    assert "127.0.0.1:5432" in laptop
    assert "CHANGE_ME" not in laptop


@pytest.mark.parametrize(
    "bad_name",
    ["", "orders-table", "orders; DROP TABLE", "1orders", "order table"],
)
def test_ddl_rejects_invalid_table_names(bad_name):
    component = AppModel(
        app_name="test",
        platform="aws",
        entrypoints=[],
        components=[
            {
                "id": bad_name or "bad",
                "kind": "kv_table",
                "provider": "aws.dynamodb",
                "resource_name": bad_name,
                "partition_key": {"name": "order_id", "type": "S"},
            },
        ],
        files=[],
        env=[],
    ).components[0]
    with pytest.raises(UnsupportedFeature):
        ddl(component)


def _partial_pam():
    return AppModel(
        app_name="ShopLite",
        platform="aws",
        entrypoints=["app.main:app"],
        components=[
            {
                "id": "orders",
                "kind": "kv_table",
                "provider": "aws.dynamodb",
                "resource_name": "orders",
                "name_env_var": "ORDERS_TABLE",
                "partition_key": {"name": "order_id", "type": "S"},
                "indexes": [],
            },
            {
                "id": "receipts",
                "kind": "object_store",
                "provider": "aws.s3",
                "resource_name": "receipts",
                "name_env_var": "RECEIPTS_BUCKET",
            },
            {
                "id": "order_events",
                "kind": "queue",
                "provider": "aws.sqs",
                "resource_name": "order-events",
                "name_env_var": "ORDER_EVENTS_QUEUE",
                "max_receive_count": 3,
            },
        ],
        files=[],
        env=[],
    )


def test_full_scope_uses_all_movable_services():
    pam = _partial_pam()
    secrets = make_secrets(pam)
    files = render(pam, secrets)
    paths = {generated.path for generated in files}
    assert "schema.sql" in paths
    assert "rabbitmq/definitions.json" in paths
    assert "seaweedfs/s3.json" in paths


@pytest.mark.parametrize(
    ("scope", "expected_schema", "expected_rabbitmq", "expected_seaweed"),
    [
        (["orders"], True, False, False),
        (["receipts"], False, False, True),
        (["order_events"], False, True, False),
        ([], False, False, False),
    ],
)
def test_single_component_scopes_only_include_needed_services(
    scope,
    expected_schema,
    expected_rabbitmq,
    expected_seaweed,
):
    pam = _partial_pam()
    files = render(pam, make_secrets(pam), scope)
    paths = {generated.path for generated in files}
    assert ("schema.sql" in paths) == expected_schema
    assert ("rabbitmq/definitions.json" in paths) == expected_rabbitmq
    assert ("seaweedfs/s3.json" in paths) == expected_seaweed


def test_hybrid_scope_adds_aws_laptop_environment():
    pam = _partial_pam()
    files = render(pam, make_secrets(pam), ["orders", "receipts"])
    laptop = next(generated.content for generated in files if generated.path == "laptop.env")
    compose = next(generated.content for generated in files if generated.path == "docker-compose.yml")
    assert "AWS_ENDPOINT_URL=http://127.0.0.1:4566" in laptop
    assert "ORDER_EVENTS_QUEUE=order-events" in laptop          # the real name: the tests use it against moto
    assert "host.docker.internal:host-gateway" in compose
    assert "AWS_ENDPOINT_URL: http://host.docker.internal:4566" in compose


def test_empty_scope_keeps_all_movable_components_on_aws():
    pam = _partial_pam()
    assert score(pam, []) == 195


def test_hybrid_assessment_contains_aws_credentials_finding(monkeypatch):
    pam = _partial_pam()
    from boomerang.events import Emitter
    monkeypatch.setattr(
        "boomerang.llm.structured",
        lambda **kwargs: Assessment(
            findings=[],
            overall="red",
            summary="Rules assessment",
        ),
    )

    assessment = assess(pam, Emitter("scope"), ["orders", "receipts"])
    hybrid = next(
        (finding for finding in assessment.findings if finding.component_id == "hybrid"),
        None,
    )
    assert hybrid is not None
    assert hybrid.light == "red"
    assert "order_events" in hybrid.what_changes


def test_lost_report_keeps_out_of_scope_components_on_aws():
    pam = _partial_pam()
    assessment = Assessment(
        findings=[
            Finding(
                analysis="Queue",
                component_id="order_events",
                feature="dlq",
                light="amber",
                what_changes="RabbitMQ emulates the SQS DLQ",
            )
        ],
        overall="amber",
        summary="1 finding",
    )
    lost = build(pam, assessment, [], ["orders"])
    item = next(item for item in lost if item.component_id == "order_events")
    assert item.status == "preserved"
    assert item.detail == "Stays on AWS"
