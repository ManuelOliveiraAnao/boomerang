"""Runs ShopLite's spec tests and A's white-box tests against the fake AWS."""
from pathlib import Path

import pytest

from boomerang import lab, provisioner_aws, verifier
from boomerang.models import AppModel

ROOT = Path(__file__).resolve().parent.parent
PAM = AppModel.model_validate_json((ROOT / "tests/fixtures/pam_shoplite.json").read_text(encoding="utf-8"))
APP = ROOT / "sample-app/aws"


@pytest.fixture
def env():
    lab.up()
    lab.reset_moto()
    yield provisioner_aws.provision(PAM)
    lab.reset_moto()


def test_spec_tests_pass(env):
    r = verifier.run_pytest(APP, ROOT / "sample-app/tests", env, "aws")
    assert r.ok and r.total == 7, r.output_tail


def test_whitebox_tests_pass(env):
    r = verifier.run_pytest(APP, ROOT / "tests/fixtures/shoplite_whitebox", env, "aws")
    assert r.ok, [(f.test, f.message) for f in r.failures] or r.output_tail
    assert r.total >= 13


def test_spec_tests_fail_without_resources():
    """Sanity check of the harness: no tables/buckets -> the spec tests must fail, not pass."""
    lab.up()
    lab.reset_moto()
    r = verifier.run_pytest(APP, ROOT / "sample-app/tests", lab.aws_env() | {
        "ORDERS_TABLE": "missing", "RECEIPTS_BUCKET": "missing", "ORDER_EVENTS_QUEUE": "missing"}, "aws")
    assert not r.ok and r.passed <= 2  # only /health and validation can pass


def test_terraform_declares_every_red_item():
    tf = (APP / "infra/main.tf").read_text(encoding="utf-8")
    for resource in ("aws_lambda_function", "aws_s3_bucket_notification", "aws_iam_role",
                     "aws_iam_role_policy", "aws_cloudwatch_metric_alarm", "redrive_policy",
                     "global_secondary_index"):
        assert resource in tf


def test_app_layer_has_no_sdk_calls_outside_data_modules():
    for f in ("main.py", "worker.py"):
        assert "boto3" not in (APP / "app" / f).read_text(encoding="utf-8")
