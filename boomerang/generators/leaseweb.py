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


TEMPLATE_DIR = Path(__file__).with_name("templates")


def _model_to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def generate_secrets() -> dict[str, str]:
    """Generate random credentials that are never sent to the LLM."""
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(24),
        "SEAWEEDFS_ACCESS_KEY": secrets.token_urlsafe(24),
        "SEAWEEDFS_SECRET_KEY": secrets.token_urlsafe(32),
    }


def _render_template(name: str, context: dict[str, Any]) -> str:
    if Environment is None:
        raise RuntimeError("jinja2 is required to generate Leaseweb files")
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return environment.get_template(name).render(**context)


def render(pam: Any, secret_values: dict[str, str] | None = None, out_dir: Path | None = None) -> list[Path]:
    """Render every required Leaseweb target file and copy the sovereign SDK."""
    pam_dict = _model_to_dict(pam)
    secret_values = secret_values or generate_secrets()
    output_dir = Path(out_dir) if out_dir is not None else Path("runs") / "leaseweb"
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "rabbitmq").mkdir(parents=True, exist_ok=True)
    (output_dir / "seaweedfs").mkdir(parents=True, exist_ok=True)
    (output_dir / "app").mkdir(parents=True, exist_ok=True)
    (output_dir / "backup").mkdir(parents=True, exist_ok=True)

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
    }

    generated: list[Path] = []

    docker_compose = _render_template("docker-compose.yml.j2", template_context)
    generated.append(output_dir / "docker-compose.yml")
    (output_dir / "docker-compose.yml").write_text(docker_compose, encoding="utf-8")

    env_example = _render_template(".env.example.j2", template_context)
    generated.append(output_dir / ".env.example")
    (output_dir / ".env.example").write_text(env_example, encoding="utf-8")

    secrets_env = "\n".join(
        [
            f"POSTGRES_PASSWORD={database_password}",
            f"RABBITMQ_PASSWORD={rabbit_password}",
            f"SEAWEEDFS_ACCESS_KEY={access_key}",
            f"SEAWEEDFS_SECRET_KEY={secret_key}",
        ]
    ) + "\n"
    secrets_path = output_dir / "secrets.env"
    secrets_path.write_text(secrets_env, encoding="utf-8")
    os.chmod(secrets_path, 0o600)
    generated.append(secrets_path)

    kv_components = [
        component
        for component in pam_dict.get("components", [])
        if component.get("kind") == "kv_table"
    ]
    schema_statements = [ddl(component) for component in kv_components]
    schema = "\n\n".join(schema_statements) + "\n"
    generated.append(output_dir / "schema.sql")
    (output_dir / "schema.sql").write_text(schema, encoding="utf-8")

    definitions = _render_template(
        "rabbitmq-definitions.json.j2",
        template_context,
    )
    definitions_path = output_dir / "rabbitmq" / "definitions.json"
    definitions_path.write_text(definitions, encoding="utf-8")
    json.loads(definitions)
    generated.append(definitions_path)

    seaweed_config = _render_template(
        "seaweedfs-s3.json.j2",
        template_context,
    )
    seaweed_path = output_dir / "seaweedfs" / "s3.json"
    seaweed_path.write_text(seaweed_config, encoding="utf-8")
    json.loads(seaweed_config)
    generated.append(seaweed_path)

    cloud_init = _render_template("cloud-init.yaml.j2", template_context)
    generated.append(output_dir / "cloud-init.yaml")
    (output_dir / "cloud-init.yaml").write_text(cloud_init, encoding="utf-8")

    deploy_script = _render_template("deploy.sh.j2", template_context)
    deploy_path = output_dir / "deploy.sh"
    deploy_path.write_text(deploy_script, encoding="utf-8")
    os.chmod(deploy_path, 0o750)
    generated.append(deploy_path)

    backup_script = _render_template("pg_backup.sh.j2", template_context)
    backup_path = output_dir / "backup" / "pg_backup.sh"
    backup_path.write_text(backup_script, encoding="utf-8")
    os.chmod(backup_path, 0o750)
    generated.append(backup_path)

    deploy_doc = _render_template("DEPLOY.md.j2", template_context)
    generated.append(output_dir / "DEPLOY.md")
    (output_dir / "DEPLOY.md").write_text(deploy_doc, encoding="utf-8")

    app_dockerfile = _render_template("app-Dockerfile.j2", template_context)
    generated.append(output_dir / "app" / "Dockerfile")
    (output_dir / "app" / "Dockerfile").write_text(app_dockerfile, encoding="utf-8")

    app_requirements = _render_template("app-requirements.txt.j2", template_context)
    generated.append(output_dir / "app" / "requirements.txt")
    (output_dir / "app" / "requirements.txt").write_text(app_requirements, encoding="utf-8")

    sdk_source = Path(__file__).parents[2] / "sovereign_sdk"
    sdk_target = output_dir / "app" / "sovereign_sdk"
    if sdk_target.exists():
        shutil.rmtree(sdk_target)
    shutil.copytree(sdk_source, sdk_target)
    generated.append(sdk_target)

    return generated
