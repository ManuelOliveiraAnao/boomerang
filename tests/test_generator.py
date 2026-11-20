import json
from pathlib import Path

from boomerang.generators.leaseweb import render


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
