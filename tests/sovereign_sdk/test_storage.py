"""sovereign_sdk.storage (docs/15 §4.1). Unit tests build the client offline; integration tests need a store.

No ``__init__.py`` in this folder on purpose: it would make pytest import it as a top-level ``sovereign_sdk``
package and shadow the real one.
"""

from __future__ import annotations

import ast
import os
import socket
import uuid
from pathlib import Path

import pytest

from sovereign_sdk import storage

S3_VARS = ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_REGION")


@pytest.fixture
def s3_env(monkeypatch: pytest.MonkeyPatch):
    for name in (*S3_VARS, "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                 "AWS_PROFILE", "AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _set(mp: pytest.MonkeyPatch, endpoint: str = "http://127.0.0.1:8333", key: str = "AKIDSEAWEED",
         secret: str = "seaweed-secret") -> None:
    mp.setenv("S3_ENDPOINT_URL", endpoint)
    mp.setenv("S3_ACCESS_KEY_ID", key)
    mp.setenv("S3_SECRET_ACCESS_KEY", secret)


def test_missing_env_vars_give_a_clear_key_error(s3_env):
    s3_env.setenv("S3_ENDPOINT_URL", "http://127.0.0.1:8333")
    with pytest.raises(KeyError) as info:
        storage.s3_client()
    message = str(info.value)
    assert "S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY not set" in message
    assert "S3_ENDPOINT_URL" in message.split("needs", 1)[1]


def test_client_is_path_style_sigv4_with_checksums_when_required(s3_env):
    _set(s3_env)
    client = storage.s3_client()
    cfg = client.meta.config
    assert client.meta.endpoint_url == "http://127.0.0.1:8333"
    assert client.meta.region_name == "us-east-1"
    assert cfg.s3 == {"addressing_style": "path"}
    assert cfg.signature_version == "s3v4"
    assert cfg.request_checksum_calculation == "when_required"
    assert cfg.response_checksum_validation == "when_required"
    assert cfg.retries["total_max_attempts"] == 4        # botocore: max_attempts=3 retries → 4 attempts in all


def test_uses_the_s3_credentials_and_region_not_the_aws_ones(s3_env):
    _set(s3_env, key="AKIDSEAWEED", secret="seaweed-secret")
    s3_env.setenv("S3_REGION", "nl-ams")
    s3_env.setenv("AWS_ACCESS_KEY_ID", "wrong-aws-key-id")
    s3_env.setenv("AWS_SECRET_ACCESS_KEY", "wrong")
    s3_env.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:4566")          # a leftover from the AWS lab
    client = storage.s3_client()
    creds = client._request_signer._credentials
    assert (creds.access_key, creds.secret_key) == ("AKIDSEAWEED", "seaweed-secret")
    assert client.meta.endpoint_url == "http://127.0.0.1:8333"
    assert client.meta.region_name == "nl-ams"


def test_storage_never_imports_boomerang():
    """It ships inside the migrated app, where the engine doesn't exist."""
    tree = ast.parse(Path(storage.__file__).read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not [m for m in modules if m.startswith("boomerang")]


# ---------- integration: a real S3-compatible endpoint ----------

def _reachable(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.5):
            return True
    except OSError:
        return False


ENDPOINTS = [
    pytest.param(("http://127.0.0.1:4566", "test", "test"), id="moto-lab"),
    pytest.param((os.environ.get("S3_TEST_ENDPOINT_URL", "http://127.0.0.1:8333"),
                  os.environ.get("S3_TEST_ACCESS_KEY_ID", ""), os.environ.get("S3_TEST_SECRET_ACCESS_KEY", "")),
                 id="seaweedfs"),
]


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_put_get_list_delete(s3_env, endpoint):
    url, key, secret = endpoint
    if not key or not secret:
        pytest.skip("set S3_TEST_ACCESS_KEY_ID / S3_TEST_SECRET_ACCESS_KEY for this endpoint")
    if not _reachable(url):
        pytest.skip(f"{url} is not reachable")
    _set(s3_env, endpoint=url, key=key, secret=secret)
    client = storage.s3_client()
    bucket = f"boomerang-test-{uuid.uuid4().hex[:8]}"
    client.create_bucket(Bucket=bucket)
    try:
        client.put_object(Bucket=bucket, Key="receipts/a.json", Body=b'{"total_cents": 1200}')
        assert client.get_object(Bucket=bucket, Key="receipts/a.json")["Body"].read() == b'{"total_cents": 1200}'
        listed = client.list_objects_v2(Bucket=bucket, Prefix="receipts/")
        assert [o["Key"] for o in listed.get("Contents", [])] == ["receipts/a.json"]
        client.delete_object(Bucket=bucket, Key="receipts/a.json")
        assert client.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 0
    finally:
        for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
        client.delete_bucket(Bucket=bucket)
