import json
from pathlib import Path

from boomerang.assess import assess
from boomerang.lockin import score
from boomerang.lost import build
from boomerang.scanner.static import scan_static


def test_fixture_assessment_uses_rule_lights():
    fixture_path = Path(__file__).parents[1] / "fixtures" / "pam.aws.json"
    pam = json.loads(fixture_path.read_text(encoding="utf-8"))
    assessment = assess(pam)

    lights = {finding["light"] for finding in assessment["findings"]}
    assert {"green", "amber", "red"}.issubset(lights)
    assert score(pam) > 0
    lost = build(pam, assessment)
    assert any(item["status"] == "lost" for item in lost)


def test_static_scanner_finds_boto3_calls(tmp_path):
    app = tmp_path / "ShopLite"
    app.mkdir()
    (app / "db.py").write_text(
        'import boto3\n'
        'dynamodb = boto3.resource("dynamodb")\n'
        'def put_order(order_id):\n'
        '    return dynamodb.Table("orders").put_item(Item={"order_id": order_id})\n',
        encoding="utf-8",
    )
    (app / "main.tf").write_text(
        'resource "aws_dynamodb_table" "orders" {\n'
        '  name = "orders"\n'
        '}\n',
        encoding="utf-8",
    )

    pam = scan_static(app)
    components = {component["id"]: component for component in pam["components"]}
    assert "orders" in components
    usage = components["orders"]["usages"][0]
    assert usage["symbol"] == "put_order"
    assert usage["operation"] == "put_item"


def test_static_scanner_finds_s3_sqs_lambda_iam_and_alarm(tmp_path):
    app = tmp_path / "ShopLite"
    app.mkdir()
    (app / "main.tf").write_text(
        'resource "aws_s3_bucket" "receipts" {}\n'
        'resource "aws_sqs_queue" "order_events" {\n'
        '  visibility_timeout_seconds = 30\n'
        '  redrive_policy = "{}"\n'
        '}\n'
        'resource "aws_lambda_function" "thumbnailer" {}\n'
        'resource "aws_iam_role" "shoplite_task" {}\n'
        'resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {}\n',
        encoding="utf-8",
    )

    pam = scan_static(app)
    components = {component["id"]: component for component in pam["components"]}

    assert components["receipts"]["kind"] == "object_store"
    assert components["order_events"]["kind"] == "queue"
    assert {"visibility_timeout", "dlq"}.issubset(
        set(components["order_events"]["features"])
    )
    assert components["thumbnailer"]["kind"] == "function"
    assert components["shoplite_task"]["kind"] == "identity"
    assert components["dlq_not_empty"]["kind"] == "monitoring"


def test_assessment_and_lost_report_cover_unmigrated_features():
    pam = {
        "components": [
            {
                "id": "receipts",
                "kind": "object_store",
                "provider": "aws.s3",
                "features": ["event_notification"],
                "usages": [],
            },
            {
                "id": "thumbnailer",
                "kind": "function",
                "provider": "aws.lambda",
                "features": ["event_notification"],
                "usages": [],
            },
        ]
    }

    assessment = assess(pam)
    lights = {(finding["component_id"], finding["light"]) for finding in assessment["findings"]}

    assert ("receipts", "red") in lights
    assert ("thumbnailer", "red") in lights
    lost = build(pam, assessment)
    assert all(item["status"] == "lost" for item in lost)


def test_lockin_score_weights_components_and_usage():
    pam = {
        "components": [
            {
                "provider": "aws.dynamodb",
                "usages": [{"operation": "put_item"}, {"operation": "get_item"}],
            },
            {"provider": "aws.s3", "usages": [{"operation": "put_object"}]},
        ]
    }

    assert score(pam) == 90 * 2 + 35


def test_assessment_emit_reports_result():
    events = []

    def emit(stage, message, level="info", **data):
        events.append((stage, message, level, data))

    assessment = assess(
        {
            "components": [
                {
                    "id": "orders",
                    "kind": "kv_table",
                    "provider": "aws.dynamodb",
                    "features": ["gsi"],
                    "usages": [],
                }
            ]
        },
        emit=emit,
    )

    assert assessment["overall"] == "amber"
    assert events == [
        (
            "assess",
            "Traffic-light assessment completed",
            "success",
            {"findings": 1},
        )
    ]


def test_assessment_empty_pam_is_green():
    assessment = assess({"components": []})

    assert assessment["overall"] == "green"
    assert assessment["findings"] == []


def test_assessment_unknown_component_feature_is_not_invented():
    assessment = assess(
        {
            "components": [
                {
                    "id": "unknown",
                    "kind": "other",
                    "provider": "aws.other",
                    "features": ["unknown_feature"],
                    "usages": [],
                }
            ]
        }
    )

    assert assessment["findings"] == []
    assert assessment["overall"] == "green"


def test_static_scanner_deduplicates_repeated_usage(tmp_path):
    app = tmp_path / "ShopLite"
    app.mkdir()
    (app / "db.py").write_text(
        'import boto3\n'
        'dynamodb = boto3.resource("dynamodb")\n'
        'def put_order(order_id):\n'
        '    table = dynamodb.Table("orders")\n'
        '    table.put_item(Item={"order_id": order_id})\n'
        '    table.put_item(Item={"order_id": order_id})\n',
        encoding="utf-8",
    )

    pam = scan_static(app)
    orders = next(
        component for component in pam["components"] if component["id"] == "orders"
    )

    assert len({usage["operation"] for usage in orders["usages"]}) == 1
    assert len({(usage["line"], usage["operation"]) for usage in orders["usages"]}) == 2


def test_static_scanner_ignores_unrelated_boto3_calls(tmp_path):
    app = tmp_path / "ShopLite"
    app.mkdir()
    (app / "main.py").write_text(
        'import boto3\n'
        'sns = boto3.client("sns")\n'
        'def notify():\n'
        '    return sns.publish(Message="hello")\n',
        encoding="utf-8",
    )

    pam = scan_static(app)

    assert pam["components"] == []
