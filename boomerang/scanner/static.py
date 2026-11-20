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
    # Helpers that return a receiver (ShopLite: `def _table(): return dynamodb.Table(...)`): calls through
    # `_table().put_item(...)` are usages of that service too. Two passes: a helper may call a helper.
    for _ in range(2):
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in services:
                continue
            for ret in ast.walk(fn):
                root = _root_name(ret.value) if isinstance(ret, ast.Return) and ret.value is not None else None
                if root is not None and root.id in services:
                    services[fn.name] = services[root.id]
                    break
    return services


ENV_HINT = {"dynamodb": "TABLE", "s3": "BUCKET", "sqs": "QUEUE"}


def _env_names(tree: ast.Module) -> list[str]:
    """Names read with os.environ[...], os.environ.get(...) or os.getenv(...), in source order."""
    names: list[str] = []
    for node in ast.walk(tree):
        arg: ast.expr | None = None
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "os.environ":
            arg = node.slice
        elif isinstance(node, ast.Call) and _dotted(node.func) in ("os.environ.get", "os.getenv") and node.args:
            arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in names:
            names.append(arg.value)
    return names


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _entrypoints(tree: ast.Module, relative: str) -> list[str]:
    """`app = FastAPI(...)` at module level in app/main.py -> "app.main:app"."""
    module = relative[:-3].replace("/", ".")
    out: list[str] = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and (_dotted(node.value.func) or "").split(".")[-1] in ("FastAPI", "Flask", "Starlette")):
            out += [f"{module}:{t.id}" for t in node.targets if isinstance(t, ast.Name)]
    return out


# ---------- Terraform blocks ----------

def _matching(text: str, start: int) -> int:
    """Index just after the `}` closing the block whose `{` ends right before `start`."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return i


def _tf_blocks(source: str) -> list[tuple[str, str, str]]:
    """(resource type, name, body) for every `resource "aws_x" "name" { ... }` (brace-matched)."""
    out: list[tuple[str, str, str]] = []
    for m in re.finditer(r'resource\s+"(aws_[^"]+)"\s+"([^"]+)"\s*\{', source):
        end = _matching(source, m.end())
        out.append((m.group(1), m.group(2), source[m.end():end - 1]))
    return out


def _tf_nested(body: str, block: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(rf"\b{block}\s*\{{", body):
        end = _matching(body, m.end())
        out.append(body[m.end():end - 1])
    return out


def _tf_top(body: str) -> str:
    """The body without nested blocks (so a GSI's `name` isn't read as the table's)."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _tf_attr(body: str, name: str) -> str | None:
    m = re.search(rf'(?m)^\s*{name}\s*=\s*"?([^"\n]+?)"?\s*$', body)
    return m.group(1).strip() if m else None


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
    entrypoints: list[str] = []
    env_vars: list[dict] = []

    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if any(part in exclude for part in path.parts):
            continue
        relative = path.relative_to(app_dir).as_posix()

        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            receiver_services = _module_aws_receiver_services(tree)
            env_names = _env_names(tree)
            entrypoints.extend(e for e in _entrypoints(tree, relative) if e not in entrypoints)
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
                if not component.get("name_env_var"):
                    hint = next((n for n in env_names if ENV_HINT[service] in n.upper()), None)
                    if hint:
                        component["name_env_var"] = hint
            files.append(
                {
                    "path": relative,
                    "role": "test" if "tests" in path.parts else "app",
                    "component_ids": sorted(set(component_ids)),
                }
            )

        elif path.name in {"main.tf", "variables.tf", "outputs.tf"} or path.suffix == ".tf":
            source = path.read_text(encoding="utf-8")
            dlq_targets = set(re.findall(r"deadLetterTargetArn\s*=\s*aws_sqs_queue\.(\w+)\.arn", source))
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
                    if name in dlq_targets:
                        continue                      # a DLQ is a feature of its main queue, not a component
                    kind, provider = "queue", "aws.sqs"
                    features = ["send", "receive", "delete"]
                    block = next((b for r, n, b in _tf_blocks(source) if r == resource and n == name), "")
                    if "redrive_policy" in block:
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
                    _apply_terraform(component, resource, name, source)

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
        "entrypoints": entrypoints,
        "test_command": "pytest -q tests",
        "components": list(components.values()),
        "files": files,
        "env": env_vars,
    })


def _apply_terraform(component: dict, resource: str, name: str, source: str) -> None:
    """Real resource name, keys, GSIs and queue settings from the Terraform block (the PAM contract)."""
    body = next((b for r, n, b in _tf_blocks(source) if r == resource and n == name), None)
    if body is None:
        return
    top = _tf_top(body)
    real = _tf_attr(top, "bucket") or _tf_attr(top, "name") or _tf_attr(top, "function_name")
    if real and "${" not in real and not real.startswith(("aws_", "var.")):
        component["resource_name"] = real
    if resource == "aws_dynamodb_table":
        types = {}
        for attr in _tf_nested(body, "attribute"):
            n, t = _tf_attr(attr, "name"), _tf_attr(attr, "type")
            if n:
                types[n] = t if t in ("S", "N") else "S"
        hash_key, range_key = _tf_attr(top, "hash_key"), _tf_attr(top, "range_key")
        if hash_key:
            component["partition_key"] = {"name": hash_key, "type": types.get(hash_key, "S")}
        if range_key:
            component["sort_key"] = {"name": range_key, "type": types.get(range_key, "S")}
        indexes = []
        for gsi in _tf_nested(body, "global_secondary_index"):
            n, hk, rk = _tf_attr(gsi, "name"), _tf_attr(gsi, "hash_key"), _tf_attr(gsi, "range_key")
            if n and hk:
                ix = {"name": n, "partition_key": {"name": hk, "type": types.get(hk, "S")}}
                if rk:
                    ix["sort_key"] = {"name": rk, "type": types.get(rk, "S")}
                indexes.append(ix)
        if indexes:
            component["indexes"] = indexes
    elif resource == "aws_sqs_queue":
        vt = _tf_attr(top, "visibility_timeout_seconds")
        if vt and vt.isdigit():
            component["visibility_timeout_s"] = int(vt)
        mrc = re.search(r"maxReceiveCount\s*=\s*(\d+)", body)
        if mrc:
            component["max_receive_count"] = int(mrc.group(1))
        if (_tf_attr(top, "fifo_queue") or "").lower() == "true":
            component["fifo"] = True
