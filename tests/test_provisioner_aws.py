import json
from pathlib import Path

import boto3
import pytest

from boomerang import lab, provisioner_aws, verifier
from boomerang.models import AppModel

ROOT = Path(__file__).resolve().parent.parent
PAM = AppModel.model_validate_json((ROOT / "tests/fixtures/pam_shoplite.json").read_text(encoding="utf-8"))


@pytest.fixture
def fresh_aws():
    lab.up()
    lab.reset_moto()
    yield
    lab.reset_moto()


def test_provision_creates_resources(fresh_aws):
    env = provisioner_aws.provision(PAM)
    kw = dict(endpoint_url=env["AWS_ENDPOINT_URL"], region_name="us-east-1",
              aws_access_key_id="test", aws_secret_access_key="test")
    assert "receipts" in [b["Name"] for b in boto3.client("s3", **kw).list_buckets()["Buckets"]]
    table = boto3.client("dynamodb", **kw).describe_table(TableName="orders")["Table"]
    assert table["GlobalSecondaryIndexes"][0]["IndexName"] == "customer_id-index"
    sqs = boto3.client("sqs", **kw)
    url = sqs.get_queue_url(QueueName="order-events")["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["VisibilityTimeout"] == "30"
    assert int(json.loads(attrs["RedrivePolicy"])["maxReceiveCount"]) == 3
    # lambda + iam are flagged, never created
    assert env["ORDERS_TABLE"] == "orders"


def test_provision_is_idempotent(fresh_aws):
    provisioner_aws.provision(PAM)
    provisioner_aws.provision(PAM)


def test_run_tag_isolates_names(fresh_aws):
    env = provisioner_aws.provision(PAM, run_tag="r1")
    assert env["ORDERS_TABLE"] == "orders-r1" and env["ORDER_EVENTS_QUEUE"] == "order-events-r1"


def test_seed_is_deterministic(fresh_aws):
    env = provisioner_aws.provision(PAM)
    counts = provisioner_aws.seed_shoplite(env, n_orders=25, pending=3)
    assert counts == {"orders": 25, "receipts": 25, "order_events": 3}
    lab.reset_moto()
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env)
    ddb = boto3.resource("dynamodb", endpoint_url=env["AWS_ENDPOINT_URL"], region_name="us-east-1",
                         aws_access_key_id="test", aws_secret_access_key="test")
    ids = sorted(i["order_id"] for i in ddb.Table("orders").scan()["Items"])
    assert ids[0] == sorted(ids)[0] and len(set(ids)) == 25


def test_shoplite_passes_its_tests_on_fake_aws(fresh_aws):
    env = provisioner_aws.provision(PAM)
    report = verifier.run_pytest(ROOT / "sample-app/aws", ROOT / "sample-app/tests", env, "aws")
    assert report.ok, [f.message for f in report.failures] or report.output_tail
    assert report.total == 7


def test_shoplite_reads_seeded_orders(fresh_aws, monkeypatch):
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env)
    report = verifier.run_pytest(ROOT / "sample-app/aws", ROOT / "sample-app/tests", env, "aws")
    assert report.ok
