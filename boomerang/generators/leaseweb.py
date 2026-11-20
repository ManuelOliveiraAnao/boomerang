"""Leaseweb files from the PAM (Jinja2 templates in generators/templates/). Owner B, task B6.

Spec: docs/03 §3.7, docs/14. Stub created by C1: B replaces the bodies.
"""

from __future__ import annotations

from collections.abc import Iterable

from boomerang.models import AppModel, GeneratedFile, Scope
from boomerang.scope import normalize_scope
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


def render(pam: AppModel, secrets: dict[str, str], scope: Scope | Iterable[str] | None = None) -> list[GeneratedFile]:
    """Binding (docs/03 §3.7). Pure: returns files with paths relative to runs/<id>/leaseweb/.
    The pipeline writes them (LF) and copies sovereign_sdk/ into app/ (docs/15 L7)."""
    def render_template(name: str) -> str:
        if Environment is None:
            raise RuntimeError("jinja2 is required to generate Leaseweb files")
        environment = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        return environment.get_template(name).render(**context)

    normalized = normalize_scope(pam, scope)
    selfhosted = set(normalized.selfhosted)
    kv_components = [
        component for component in pam.components
        if component.kind == "kv_table" and component.id in selfhosted
    ]
    queue_components = [
        component for component in pam.components
        if component.kind == "queue" and component.id in selfhosted
    ]
    object_components = [
        component for component in pam.components
        if component.kind == "object_store" and component.id in selfhosted
    ]
    kept_movable = [
        component for component in pam.components
        if component.kind in {"kv_table", "queue", "object_store"} and component.id not in selfhosted
    ]

    aws_variables: list[str] = []
    if kept_movable:
        aws_variables = [
            "AWS_REGION=us-east-1",
            "AWS_ACCESS_KEY_ID=CHANGE_ME",
            "AWS_SECRET_ACCESS_KEY=CHANGE_ME",
        ]
        for component in kept_movable:
            if component.name_env_var:
                aws_variables.append(f"{component.name_env_var}={component.resource_name}")
    aws_environment = {}
    if kept_movable:
        aws_environment = {
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "CHANGE_ME",
            "AWS_SECRET_ACCESS_KEY": "CHANGE_ME",
            "AWS_ENDPOINT_URL": "http://host.docker.internal:4566",
        }
        for component in kept_movable:
            if component.name_env_var:
                aws_environment[component.name_env_var] = component.resource_name
    context = {
        "database_password": secrets["POSTGRES_PASSWORD"],
        "rabbit_password": secrets["RABBITMQ_PASSWORD"],
        "access_key": secrets["SEAWEEDFS_ACCESS_KEY"],
        "secret_key": secrets["SEAWEEDFS_SECRET_KEY"],
        "postgres_enabled": bool(kv_components),
        "rabbitmq_enabled": bool(queue_components),
        "seaweed_enabled": bool(object_components),
        "queue_components": queue_components,
        "aws_variables": aws_variables,
        "aws_environment": aws_environment,
    }
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
        *(
            [
                "AWS_REGION=us-east-1",
                "AWS_ENDPOINT_URL=http://127.0.0.1:4566",
                "AWS_ACCESS_KEY_ID=CHANGE_ME",
                "AWS_SECRET_ACCESS_KEY=CHANGE_ME",
                *[
                    f"{component.name_env_var}={component.resource_name}"
                    for component in kept_movable
                    if component.name_env_var
                ],
            ]
            if kept_movable
            else []
        ),
    ]) + "\n"

    rabbitmq = render_template("rabbitmq-definitions.json.j2") if queue_components else None
    seaweed = render_template("seaweedfs-s3.json.j2") if object_components else None
    if rabbitmq:
        json.loads(rabbitmq)
    if seaweed:
        json.loads(seaweed)

    files = [
        GeneratedFile(path="docker-compose.yml", content=render_template("docker-compose.yml.j2")),
        GeneratedFile(path=".env.example", content=render_template(".env.example.j2")),
        GeneratedFile(path="secrets.env", content=secrets_env, secret=True),
        GeneratedFile(path="laptop.env", content=laptop_env, secret=True),
        GeneratedFile(path="cloud-init.yaml", content=render_template("cloud-init.yaml.j2")),
        GeneratedFile(path="deploy.sh", content=render_template("deploy.sh.j2"), executable=True),
        GeneratedFile(path="backup/pg_backup.sh", content=render_template("pg_backup.sh.j2"), executable=True),
        GeneratedFile(path="DEPLOY.md", content=render_template("DEPLOY.md.j2")),
        GeneratedFile(path="app/Dockerfile", content=render_template("app-Dockerfile.j2")),
        GeneratedFile(path="app/requirements.txt", content=render_template("app-requirements.txt.j2")),
    ]
    if rabbitmq:
        files.append(GeneratedFile(path="rabbitmq/definitions.json", content=rabbitmq))
    if seaweed:
        files.append(GeneratedFile(path="seaweedfs/s3.json", content=seaweed))
    if kv_components:
        files.append(GeneratedFile(path="schema.sql", content=schema))
    return files
