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


def _ids(env):
    ddb = boto3.resource("dynamodb", endpoint_url=env["AWS_ENDPOINT_URL"], region_name="us-east-1",
                         aws_access_key_id="test", aws_secret_access_key="test")
    return sorted(i["order_id"] for i in ddb.Table(env["ORDERS_TABLE"]).scan()["Items"])


def test_seed_counts(fresh_aws):
    env = provisioner_aws.provision(PAM)
    counts = provisioner_aws.seed_shoplite(env, n_orders=25, pending=3)
    assert counts == {"orders": 25, "receipts": 25, "order_events": 3}


def test_seed_is_deterministic(fresh_aws):
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env)
    first = _ids(env)
    lab.reset_moto()
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env)
    assert _ids(env) == first and len(first) == 25
    lab.reset_moto()
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env, rng_seed=8)
    assert _ids(env) != first  # a different seed gives different data


def test_seeded_pending_orders_match_queued_messages(fresh_aws):
    env = provisioner_aws.provision(PAM)
    provisioner_aws.seed_shoplite(env, n_orders=10, pending=4)
    kw = dict(endpoint_url=env["AWS_ENDPOINT_URL"], region_name="us-east-1",
              aws_access_key_id="test", aws_secret_access_key="test")
    items = boto3.resource("dynamodb", **kw).Table("orders").scan()["Items"]
    pending = {i["order_id"] for i in items if i["status"] == "pending"}
    sqs = boto3.client("sqs", **kw)
    url = sqs.get_queue_url(QueueName="order-events")["QueueUrl"]
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    assert {json.loads(m["Body"])["order_id"] for m in msgs} == pending and len(pending) == 4
    assert all("processed_at" in i for i in items if i["status"] == "processed")


def test_other_shapes_sort_key_two_gsis_fifo(fresh_aws):
    pam = AppModel.model_validate({
        "app_name": "other", "platform": "aws", "entrypoints": [], "files": [], "env": [],
        "components": [
            {"id": "events", "kind": "kv_table", "provider": "aws.dynamodb", "resource_name": "events",
             "name_env_var": "EVENTS_TABLE",
             "partition_key": {"name": "tenant", "type": "S"}, "sort_key": {"name": "ts", "type": "N"},
             "indexes": [{"name": "by-user", "partition_key": {"name": "user", "type": "S"},
                          "sort_key": {"name": "ts", "type": "N"}},
                         {"name": "by-kind", "partition_key": {"name": "kind", "type": "S"}}]},
            {"id": "jobs", "kind": "queue", "provider": "aws.sqs", "resource_name": "jobs.fifo",
             "fifo": True, "max_receive_count": 5},
            {"id": "fn", "kind": "function", "provider": "aws.lambda", "resource_name": "fn"}]})
    env = provisioner_aws.provision(pam, run_tag="x")
    kw = dict(endpoint_url=env["AWS_ENDPOINT_URL"], region_name="us-east-1",
              aws_access_key_id="test", aws_secret_access_key="test")
    t = boto3.client("dynamodb", **kw).describe_table(TableName="events-x")["Table"]
    assert {k["AttributeName"]: k["KeyType"] for k in t["KeySchema"]} == {"tenant": "HASH", "ts": "RANGE"}
    assert {g["IndexName"] for g in t["GlobalSecondaryIndexes"]} == {"by-user", "by-kind"}
    types = {a["AttributeName"]: a["AttributeType"] for a in t["AttributeDefinitions"]}
    assert types == {"tenant": "S", "ts": "N", "user": "S", "kind": "S"}
    sqs = boto3.client("sqs", **kw)
    url = sqs.get_queue_url(QueueName="jobs-x.fifo")["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["FifoQueue"] == "true" and "jobs-x-dlq.fifo" in attrs["RedrivePolicy"]
    assert env["EVENTS_TABLE"] == "events-x"
    assert "fn" not in provisioner_aws.resource_names(pam)


def test_fifo_name_must_end_with_fifo(fresh_aws):
    pam = AppModel.model_validate({
        "app_name": "bad", "platform": "aws", "entrypoints": [], "files": [], "env": [],
        "components": [{"id": "q", "kind": "queue", "provider": "aws.sqs", "resource_name": "plain",
                        "fifo": True}]})
    with pytest.raises(ValueError):
        provisioner_aws.provision(pam)


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
