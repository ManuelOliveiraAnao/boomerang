import boto3
import pytest

from boomerang import lab


@pytest.fixture(scope="module")
def running_lab():
    lab.up(backend="auto")
    yield
    # leave it running for other test modules; `boomerang lab down` stops it


def test_moto_reachable(running_lab):
    assert lab.status()["moto"] is True


def test_aws_env_works_with_boto3(running_lab, monkeypatch):
    for k, v in lab.aws_env().items():
        monkeypatch.setenv(k, v)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="lab-smoke")
    assert "lab-smoke" in [b["Name"] for b in s3.list_buckets()["Buckets"]]


def test_reset_wipes_state(running_lab, monkeypatch):
    for k, v in lab.aws_env().items():
        monkeypatch.setenv(k, v)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="lab-reset-me")
    lab.reset_moto()
    assert s3.list_buckets()["Buckets"] == []


def test_up_is_idempotent(running_lab):
    lab.up(backend="auto")
    assert lab.moto_ready()
