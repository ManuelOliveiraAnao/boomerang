from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:  # pragma: no cover - install declared runtime dependency
    Environment = None

from sovereign_sdk.kv import ddl
from sovereign_sdk.queue import queue_arguments
from boomerang.models import GeneratedFile


TEMPLATE_DIR = Path(__file__).with_name("templates")


def _model_to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def make_secrets(pam: Any = None) -> dict[str, str]:
    """Generate random credentials that are never sent to the LLM."""
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(24),
        "SEAWEEDFS_ACCESS_KEY": secrets.token_urlsafe(24),
        "SEAWEEDFS_SECRET_KEY": secrets.token_urlsafe(32),
    }


def generate_secrets() -> dict[str, str]:
    """Backward-compatible alias for make_secrets."""
    return make_secrets()


def _render_template(name: str, context: dict[str, Any]) -> str:
    if Environment is None:
        raise RuntimeError("jinja2 is required to generate Leaseweb files")
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return environment.get_template(name).render(**context)


def render(pam: Any, secret_values: dict[str, str] | None = None, out_dir: Path | None = None) -> list[GeneratedFile]:
    """Render every required Leaseweb target file as pure GeneratedFile values."""
    pam_dict = _model_to_dict(pam)
    secret_values = secret_values or make_secrets(pam_dict)

    order_events = next(
        (component for component in pam_dict.get("components", []) if component.get("kind") == "queue"),
        None,
    )
    max_receive_count = order_events.get("max_receive_count", 3) if order_events else 3
    max_receive_count = max_receive_count or 3

    database_password = secret_values["POSTGRES_PASSWORD"]
    rabbit_password = secret_values["RABBITMQ_PASSWORD"]
    access_key = secret_values["SEAWEEDFS_ACCESS_KEY"]
    secret_key = secret_values["SEAWEEDFS_SECRET_KEY"]
    template_context = {
        "pam": pam_dict,
        "database_password": database_password,
        "rabbit_password": rabbit_password,
        "access_key": access_key,
        "secret_key": secret_key,
        "order_events_queue_arguments": queue_arguments("order-events", max_receive_count),
    }

    generated: list[GeneratedFile] = [
        GeneratedFile(path="docker-compose.yml", content=_render_template("docker-compose.yml.j2", template_context)),
        GeneratedFile(path=".env.example", content=_render_template(".env.example.j2", template_context)),
        GeneratedFile(
            path="secrets.env",
            content="\n".join([
                f"POSTGRES_PASSWORD={database_password}",
                f"RABBITMQ_PASSWORD={rabbit_password}",
                f"SEAWEEDFS_ACCESS_KEY={access_key}",
                f"SEAWEEDFS_SECRET_KEY={secret_key}",
            ]) + "\n",
            secret=True,
        ),
        GeneratedFile(
            path="laptop.env",
            content="\n".join([
                f"DATABASE_URL=postgresql://shoplite:{database_password}@127.0.0.1:5432/shoplite",
                f"AMQP_URL=amqp://shoplite:{rabbit_password}@127.0.0.1:5672/shoplite",
                "S3_ENDPOINT_URL=http://127.0.0.1:8333",
                f"S3_ACCESS_KEY_ID={access_key}",
                f"S3_SECRET_ACCESS_KEY={secret_key}",
                "ORDERS_TABLE=orders",
                "RECEIPTS_BUCKET=receipts",
                "ORDER_EVENTS_QUEUE=order-events",
            ]) + "\n",
            secret=True,
        ),
    ]

    kv_components = [component for component in pam_dict.get("components", []) if component.get("kind") == "kv_table"]
    generated.append(GeneratedFile(
        path="schema.sql",
        content="\n\n".join(ddl(component) for component in kv_components) + "\n",
    ))
    rabbitmq = _render_template("rabbitmq-definitions.json.j2", template_context)
    seaweed = _render_template("seaweedfs-s3.json.j2", template_context)
    json.loads(rabbitmq)
    json.loads(seaweed)
    generated.extend([
        GeneratedFile(path="rabbitmq/definitions.json", content=rabbitmq),
        GeneratedFile(path="seaweedfs/s3.json", content=seaweed),
        GeneratedFile(path="cloud-init.yaml", content=_render_template("cloud-init.yaml.j2", template_context)),
        GeneratedFile(path="deploy.sh", content=_render_template("deploy.sh.j2", template_context), executable=True),
        GeneratedFile(path="backup/pg_backup.sh", content=_render_template("pg_backup.sh.j2", template_context), executable=True),
        GeneratedFile(path="DEPLOY.md", content=_render_template("DEPLOY.md.j2", template_context)),
        GeneratedFile(path="app/Dockerfile", content=_render_template("app-Dockerfile.j2", template_context)),
        GeneratedFile(path="app/requirements.txt", content=_render_template("app-requirements.txt.j2", template_context)),
    ])

    if out_dir is not None:
        output_dir = Path(out_dir)
        for generated_file in generated:
            destination = output_dir / generated_file.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(generated_file.content, encoding="utf-8", newline="\n")
            if generated_file.executable:
                os.chmod(destination, 0o750)
            if generated_file.secret:
                os.chmod(destination, 0o600)

    return generated
