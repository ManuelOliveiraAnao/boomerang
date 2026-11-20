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
