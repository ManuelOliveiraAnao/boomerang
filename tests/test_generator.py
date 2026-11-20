import json
import os
from pathlib import Path

import pytest

from boomerang.generators.leaseweb import render
from boomerang.generators.leaseweb import generate_secrets


def test_generator_writes_required_files(tmp_path):
    fixture_path = Path(__file__).parents[1] / "fixtures" / "pam.aws.json"
    pam = json.loads(fixture_path.read_text(encoding="utf-8"))
    secrets = {
        "POSTGRES_PASSWORD": "postgres-test-password",
        "RABBITMQ_PASSWORD": "rabbitmq-test-password",
        "SEAWEEDFS_ACCESS_KEY": "access-test-key",
        "SEAWEEDFS_SECRET_KEY": "secret-test-key",
    }

    files = render(pam, secrets, tmp_path)
    relative_names = {path.relative_to(tmp_path).as_posix() for path in files}

    assert "docker-compose.yml" in relative_names
    assert ".env.example" in relative_names
    assert "secrets.env" in relative_names
    assert "schema.sql" in relative_names
    assert "rabbitmq/definitions.json" in relative_names
    assert "seaweedfs/s3.json" in relative_names
    assert "cloud-init.yaml" in relative_names
    assert "deploy.sh" in relative_names
    assert "backup/pg_backup.sh" in relative_names
    assert "DEPLOY.md" in relative_names
    assert "app/Dockerfile" in relative_names
    assert "app/requirements.txt" in relative_names

    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:" in compose
    assert "rabbitmq:" in compose
    assert "seaweedfs:" in compose

    definitions = json.loads(
        (tmp_path / "rabbitmq" / "definitions.json").read_text(encoding="utf-8")
    )
    assert definitions["vhosts"] == [{"name": "shoplite"}]
    assert definitions["permissions"][0]["vhost"] == "shoplite"
    assert any(
        queue["name"] == "order-events"
        and queue["arguments"]["x-delivery-limit"] == 3
        for queue in definitions["queues"]
    )

    seaweed_config = json.loads(
        (tmp_path / "seaweedfs" / "s3.json").read_text(encoding="utf-8")
    )
    identity = seaweed_config["identities"][0]
    assert identity["name"] == "shoplite"
    assert identity["actions"] == [
        "Read:receipts",
        "Write:receipts",
        "List:receipts",
    ]

    secrets_mode = os.stat(tmp_path / "secrets.env").st_mode & 0o777
    deploy_mode = os.stat(tmp_path / "deploy.sh").st_mode & 0o777
    backup_mode = os.stat(tmp_path / "backup" / "pg_backup.sh").st_mode & 0o777
    if os.name != "nt":
        assert secrets_mode == 0o600
    if os.name != "nt":
        assert deploy_mode & 0o100
        assert backup_mode & 0o100


def test_generate_secrets_has_separate_random_long_values():
    values = generate_secrets()

    assert set(values) == {
        "POSTGRES_PASSWORD",
        "RABBITMQ_PASSWORD",
        "SEAWEEDFS_ACCESS_KEY",
        "SEAWEEDFS_SECRET_KEY",
    }
    assert len(set(values.values())) == len(values)
    assert all(len(value) >= 24 for value in values.values())


@pytest.mark.parametrize(
    ("fixture_value", "expected_column"),
    [
        ({"name": "order_id", "type": "S"}, '"order_id" TEXT NOT NULL'),
        ({"name": "order_id", "type": "N"}, '"order_id" TEXT NOT NULL'),
    ],
)
def test_generator_schema_contains_primary_key(fixture_value, expected_column):
    from sovereign_sdk.kv import ddl

    ddl_text = ddl(
        {
            "id": "orders",
            "resource_name": "orders",
            "partition_key": fixture_value,
            "sort_key": None,
            "indexes": [],
        }
    )
    assert expected_column in ddl_text
    assert "PRIMARY KEY (\"order_id\")" in ddl_text
