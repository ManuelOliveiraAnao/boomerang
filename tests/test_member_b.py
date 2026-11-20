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
    assert score(pam) == 125


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
        "rabbitmq/definitions.json",
        "seaweedfs/s3.json",
        "deploy.sh",
        "backup/pg_backup.sh",
        "DEPLOY.md",
        "app/Dockerfile",
        "app/requirements.txt",
    }.issubset(paths)

    laptop = next(generated.content for generated in files if generated.path == "laptop.env")
    assert "127.0.0.1:5432" in laptop
    assert "CHANGE_ME" not in laptop
    rabbitmq = next(generated.content for generated in files if generated.path == "rabbitmq/definitions.json")
    definitions = json.loads(rabbitmq)
    main = next(queue for queue in definitions["queues"] if queue["name"] == "order-events")
    assert main["arguments"]["x-queue-type"] == "quorum"
    assert main["arguments"]["x-delivery-limit"] == 2


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
