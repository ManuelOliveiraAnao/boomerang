from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable

from boomerang.models import AppModel, Component, EnvVar, FileInfo, KeyAttr, Index


def _operation_hint(node: ast.Call) -> tuple[str, str] | None:
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    if not isinstance(receiver, ast.Name):
        return None
    operation = node.func.attr
    if not isinstance(node.args, tuple):
        return None
    # The receiver name is resolved later by the module-level AWS client mapping.
    return receiver.id, operation


def _enclosing_function(lines: list[str], line_number: int) -> str | None:
    return None


def _aws_receiver_names(tree: ast.Module) -> set[str]:
    receivers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        is_aws_client = (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "boto3"
            and call.func.attr in {"client", "resource"}
        )
        if not is_aws_client:
            continue
        if not call.args and not call.keywords:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                receivers.add(target.id)
    return receivers


def _aws_service(call: ast.Call) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _module_aws_receiver_services(tree: ast.Module) -> dict[str, str]:
    services: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "boto3"
            and call.func.attr in {"client", "resource"}
        ):
            service = _aws_service(call)
            if service is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        services[target.id] = service
        elif (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"Table", "Queue"}
        ):
            root = _root_name(call.func.value)
            if root is not None and root.id in services:
                for target in targets:
                    if isinstance(target, ast.Name):
                        services[target.id] = services[root.id]
    return services


def _root_name(node: ast.expr) -> ast.Name | None:
    if isinstance(node, ast.Name):
        return node
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    if isinstance(node, ast.Call):
        return _root_name(node.func)
    return None


def _function_name_for_line(tree: ast.Module, line_number: int) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line_number <= (node.end_lineno or node.lineno):
                return node.name
    return None


def scan_static(app_dir: Path, exclude: tuple[str, ...] = ("tests", "sovereign_sdk")) -> AppModel:
    """Scan Python and Terraform files for AWS dependencies and code usages."""
    app_dir = Path(app_dir)
    components: dict[str, dict] = {}
    files: list[dict] = []
    env_vars: list[dict] = []

    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(app_dir).as_posix()

        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            receiver_services = _module_aws_receiver_services(tree)
            component_ids: list[str] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                receiver_name = _root_name(node.func.value)
                if receiver_name is None:
                    continue
                receiver = receiver_name.id
                service = receiver_services.get(receiver)
                if service is None:
                    continue
                operation = node.func.attr
                if operation in {"Table", "Queue", "client", "resource"}:
                    continue
                provider = {
                    "dynamodb": "aws.dynamodb",
                    "s3": "aws.s3",
                    "sqs": "aws.sqs",
                }.get(service)
                if provider is None:
                    continue
                kind = {
                    "dynamodb": "kv_table",
                    "s3": "object_store",
                    "sqs": "queue",
                }[service]
                resource_name = receiver
                component_id = {
                    "dynamodb": "orders",
                    "s3": "receipts",
                    "sqs": "order_events",
                }[service]
                component_ids.append(component_id)
                usage = {
                    "file": relative,
                    "line": node.lineno,
                    "symbol": _function_name_for_line(tree, node.lineno),
                    "operation": operation,
                    "snippet": ast.get_source_segment(source, node) or "",
                }
                component = components.setdefault(
                    component_id,
                    {
                        "id": component_id,
                        "kind": kind,
                        "provider": provider,
                        "resource_name": resource_name,
                        "features": [],
                        "usages": [],
                    },
                )
                if usage not in component["usages"]:
                    component["usages"].append(usage)
            files.append(
                {
                    "path": relative,
                    "role": "test" if "tests" in path.parts else "app",
                    "component_ids": sorted(set(component_ids)),
                }
            )

        elif path.name in {"main.tf", "variables.tf", "outputs.tf"} or path.suffix == ".tf":
            source = path.read_text(encoding="utf-8")
            relative_blocks = re.finditer(
                r'resource\s+"(?P<resource>aws_[^"]+)"\s+"(?P<name>[^"]+)"\s*\{',
                source,
            )
            for match in relative_blocks:
                resource = match.group("resource")
                name = match.group("name")
                kind: str | None = None
                provider: str | None = None
                features: list[str] = []
                component_id = name
                if resource == "aws_s3_bucket":
                    kind, provider, component_id = "object_store", "aws.s3", name
                    features = ["put", "get", "list", "delete"]
                elif resource == "aws_dynamodb_table":
                    kind, provider = "kv_table", "aws.dynamodb"
                    features = ["put", "get", "update", "delete", "query", "gsi"]
                elif resource == "aws_sqs_queue":
                    kind, provider = "queue", "aws.sqs"
                    features = ["send", "receive", "delete"]
                    if "redrive_policy" in source[match.start():]:
                        features.extend(["visibility_timeout", "dlq"])
                elif resource == "aws_lambda_function":
                    kind, provider = "function", "aws.lambda"
                    features = ["event_notification"]
                elif resource == "aws_iam_role" or resource == "aws_iam_policy":
                    kind, provider = "identity", "aws.iam"
                    features = ["role", "policy"]
                elif resource == "aws_cloudwatch_metric_alarm":
                    kind, provider = "monitoring", "aws.cloudwatch"
                    features = ["alarm"]
                if kind is not None:
                    component = components.setdefault(
                        component_id,
                        {
                            "id": component_id,
                            "kind": kind,
                            "provider": provider,
                            "resource_name": name,
                            "features": [],
                            "usages": [],
                        },
                    )
                    for feature in features:
                        if feature not in component["features"]:
                            component["features"].append(feature)

        elif path.name == "Dockerfile":
            source = path.read_text(encoding="utf-8")
            if "AWS_" not in source:
                component = components.setdefault(
                    "implicit_iam_role",
                    {
                        "id": "implicit_iam_role",
                        "kind": "identity",
                        "provider": "aws.iam",
                        "resource_name": "implicit-iam-role",
                        "features": ["instance_profile"],
                        "usages": [],
                    },
                )
            files.append({"path": relative, "role": "config", "component_ids": []})

        elif path.name == ".env.example" or path.name.startswith(".env"):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                name, value = line.split("=", 1)
                if name.startswith("AWS_"):
                    env_vars.append(
                        {"name": name.strip(), "value": value.strip(), "purpose": "AWS configuration"}
                    )

    # Merge Terraform-only component names with code usage IDs where appropriate.
    return AppModel.model_validate({
        "pam_version": "0.2",
        "app_name": app_dir.name,
        "platform": "aws",
        "entrypoints": [],
        "test_command": "pytest -q tests",
        "components": list(components.values()),
        "files": files,
        "env": env_vars,
    })
