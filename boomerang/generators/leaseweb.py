"""Leaseweb files from the PAM (Jinja2 templates in generators/templates/). Owner B, task B6.

Spec: docs/03 §3.7, docs/14. Stub created by C1: B replaces the bodies.
"""

from __future__ import annotations

from boomerang.models import AppModel, GeneratedFile
from sovereign_sdk.kv import ddl

import json
import os
import secrets
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:
    Environment = None


TEMPLATE_DIR = Path(__file__).with_name("templates")


def make_secrets(pam: AppModel) -> dict[str, str]:
    """Generate passwords and keys only. No hosts, names or ports."""
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(24),
        "SEAWEEDFS_ACCESS_KEY": secrets.token_urlsafe(24),
        "SEAWEEDFS_SECRET_KEY": secrets.token_urlsafe(32),
    }


def render(pam: AppModel, secrets: dict[str, str]) -> list[GeneratedFile]:
    """Binding (docs/03 §3.7). Pure: returns files with paths relative to runs/<id>/leaseweb/.
    The pipeline writes them (LF) and copies sovereign_sdk/ into app/ (docs/15 L7)."""
    context = {
        "database_password": secrets["POSTGRES_PASSWORD"],
        "rabbit_password": secrets["RABBITMQ_PASSWORD"],
        "access_key": secrets["SEAWEEDFS_ACCESS_KEY"],
        "secret_key": secrets["SEAWEEDFS_SECRET_KEY"],
    }

    def render_template(name: str) -> str:
        if Environment is None:
            raise RuntimeError("jinja2 is required to generate Leaseweb files")
        environment = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        return environment.get_template(name).render(**context)

    kv_components = [component for component in pam.components if component.kind == "kv_table"]
    schema = "\n\n".join(ddl(component) for component in kv_components) + "\n"
    secrets_env = "\n".join([
        f"POSTGRES_PASSWORD={secrets['POSTGRES_PASSWORD']}",
        f"RABBITMQ_PASSWORD={secrets['RABBITMQ_PASSWORD']}",
        f"SEAWEEDFS_ACCESS_KEY={secrets['SEAWEEDFS_ACCESS_KEY']}",
        f"SEAWEEDFS_SECRET_KEY={secrets['SEAWEEDFS_SECRET_KEY']}",
    ]) + "\n"
    laptop_env = "\n".join([
        f"DATABASE_URL=postgresql://shoplite:{secrets['POSTGRES_PASSWORD']}@127.0.0.1:5432/shoplite",
        f"AMQP_URL=amqp://shoplite:{secrets['RABBITMQ_PASSWORD']}@127.0.0.1:5672/shoplite",
        "S3_ENDPOINT_URL=http://127.0.0.1:8333",
        f"S3_ACCESS_KEY_ID={secrets['SEAWEEDFS_ACCESS_KEY']}",
        f"S3_SECRET_ACCESS_KEY={secrets['SEAWEEDFS_SECRET_KEY']}",
        "ORDERS_TABLE=orders",
        "RECEIPTS_BUCKET=receipts",
        "ORDER_EVENTS_QUEUE=order-events",
    ]) + "\n"

    rabbitmq = render_template("rabbitmq-definitions.json.j2")
    seaweed = render_template("seaweedfs-s3.json.j2")
    json.loads(rabbitmq)
    json.loads(seaweed)

    return [
        GeneratedFile(path="docker-compose.yml", content=render_template("docker-compose.yml.j2")),
        GeneratedFile(path=".env.example", content=render_template(".env.example.j2")),
        GeneratedFile(path="secrets.env", content=secrets_env, secret=True),
        GeneratedFile(path="laptop.env", content=laptop_env, secret=True),
        GeneratedFile(path="schema.sql", content=schema),
        GeneratedFile(path="rabbitmq/definitions.json", content=rabbitmq),
        GeneratedFile(path="seaweedfs/s3.json", content=seaweed),
        GeneratedFile(path="cloud-init.yaml", content=render_template("cloud-init.yaml.j2")),
        GeneratedFile(path="deploy.sh", content=render_template("deploy.sh.j2"), executable=True),
        GeneratedFile(path="backup/pg_backup.sh", content=render_template("pg_backup.sh.j2"), executable=True),
        GeneratedFile(path="DEPLOY.md", content=render_template("DEPLOY.md.j2")),
        GeneratedFile(path="app/Dockerfile", content=render_template("app-Dockerfile.j2")),
        GeneratedFile(path="app/requirements.txt", content=render_template("app-requirements.txt.j2")),
    ]
