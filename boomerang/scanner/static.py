"""Static hints: Python ast, Terraform, Dockerfile, env. Owner B, task B2.

Spec: docs/03 §3.3. Stub created by C1: B replaces the bodies; internals are up to B.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from boomerang.models import (
    AppModel,
    CodeRef,
    Component,
    EnvVar,
    FileInfo,
    Index,
    KeyAttr,
)


def scan_static(app_dir: Path, exclude: tuple[str, ...] = ("tests", "sovereign_sdk")) -> AppModel:
    """Scan Python, Terraform, Dockerfile and environment files for AWS dependencies."""
    app_dir = Path(app_dir)
    components: dict[str, Component] = {}
    files: list[FileInfo] = []
    env: list[EnvVar] = []
    excluded = set(exclude)

    def add_component(
        component_id: str,
        kind: str,
        provider: str,
        resource_name: str,
        features: list[str] | None = None,
        name_env_var: str | None = None,
        partition_key: KeyAttr | None = None,
        sort_key: KeyAttr | None = None,
        indexes: list[Index] | None = None,
        max_receive_count: int | None = None,
        visibility_timeout_s: int | None = None,
    ) -> Component:
        component = components.get(component_id)
        if component is None:
            component = Component(
                id=component_id,
                kind=kind,
                provider=provider,
                resource_name=resource_name,
                name_env_var=name_env_var,
                partition_key=partition_key,
                sort_key=sort_key,
                indexes=indexes or [],
                max_receive_count=max_receive_count,
                visibility_timeout_s=visibility_timeout_s,
                features=features or [],
            )
            components[component_id] = component
        else:
            for feature in features or []:
                if feature not in component.features:
                    component.features.append(feature)
        return component

    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(app_dir).parts
        if any(part in excluded for part in relative_parts):
            continue
        relative = path.relative_to(app_dir).as_posix()

        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            receiver_services: dict[str, str] = {}
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                call = node.value
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "boto3"
                    and call.func.attr in {"client", "resource"}
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                ):
                    continue
                service = call.args[0].value
                for target in targets:
                    if isinstance(target, ast.Name):
                        receiver_services[target.id] = service
            _add_helper_receivers(tree, receiver_services)
            env_names = _env_names(tree)

            component_ids: list[str] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                receiver = node.func.value
                if isinstance(receiver, ast.Call):
                    receiver = receiver.func
                if isinstance(receiver, ast.Attribute):
                    receiver = receiver.value
                if not isinstance(receiver, ast.Name):
                    continue
                service = receiver_services.get(receiver.id)
                if service not in {"dynamodb", "s3", "sqs"}:
                    continue
                operation = node.func.attr
                if operation in {"Table", "Queue", "resource", "client"}:
                    continue
                kind = {
                    "dynamodb": ("kv_table", "aws.dynamodb", "orders"),
                    "s3": ("object_store", "aws.s3", "receipts"),
                    "sqs": ("queue", "aws.sqs", "order_events"),
                }[service]
                component_id = kind[2]
                component_ids.append(component_id)
                component = add_component(
                    component_id,
                    kind[0],
                    kind[1],
                    component_id,
                    features=["query", "gsi", "conditional_write"] if service == "dynamodb"
                    else ["put", "get", "list", "delete"] if service == "s3"
                    else ["send", "receive", "delete", "visibility_timeout", "dlq"],
                )
                usage = CodeRef(
                    file=relative,
                    line=node.lineno,
                    symbol=_function_name(tree, node.lineno),
                    operation=operation,
                    snippet=ast.get_source_segment(source, node) or "",
                )
                if usage not in component.usages:
                    component.usages.append(usage)
                if component.name_env_var is None:
                    component.name_env_var = next((n for n in env_names if ENV_HINT[service] in n.upper()), None)

            role = "test" if "tests" in relative_parts else "app"
            files.append(FileInfo(path=relative, role=role, component_ids=sorted(set(component_ids))))

        elif path.suffix == ".tf" or path.name in {"main.tf", "variables.tf", "outputs.tf"}:
            source = path.read_text(encoding="utf-8")
            dlq_targets = set(re.findall(r"deadLetterTargetArn\s*=\s*aws_sqs_queue\.(\w+)\.arn", source))
            for match in re.finditer(r'resource\s+"(aws_[^"]+)"\s+"([^"]+)"\s*\{', source):
                resource_type, name = match.groups()
                if resource_type == "aws_sqs_queue" and name in dlq_targets:
                    continue                      # a DLQ is a feature of its main queue, not a component
                if resource_type == "aws_s3_bucket":
                    add_component(name, "object_store", "aws.s3", name, ["put", "get", "list", "delete"])
                elif resource_type == "aws_dynamodb_table":
                    add_component(name, "kv_table", "aws.dynamodb", name, ["put", "get", "update", "delete", "query", "gsi", "conditional_write"])
                elif resource_type == "aws_sqs_queue":
                    block_end = source.find("\n}", match.start())
                    block = source[match.start(): block_end if block_end != -1 else len(source)]
                    features = ["send", "receive", "delete"]
                    if "redrive_policy" in block:
                        features += ["dlq"]
                    if "visibility_timeout_seconds" in block:
                        features += ["visibility_timeout"]
                    add_component(name, "queue", "aws.sqs", name, features)
                elif resource_type == "aws_lambda_function":
                    add_component(name, "function", "aws.lambda", name, ["event_notification"])
                elif resource_type in {"aws_iam_role", "aws_iam_policy"}:
                    add_component(name, "identity", "aws.iam", name, ["role", "policy"])
                elif resource_type == "aws_cloudwatch_metric_alarm":
                    add_component(name, "monitoring", "aws.cloudwatch", name, ["alarm"])
                if name in components:
                    _apply_terraform(components[name], resource_type, name, source)

        elif path.name == "Dockerfile":
            source = path.read_text(encoding="utf-8")
            if not re.search(r"\bAWS_(REGION|ACCESS_KEY_ID|SECRET_ACCESS_KEY)\b", source):
                add_component("implicit_iam_role", "identity", "aws.iam", "implicit-iam-role", ["instance_profile"])

        elif path.name.startswith(".env"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                name, value = line.split("=", 1)
                if name.strip().startswith("AWS_"):
                    env.append(EnvVar(name=name.strip(), value=value.strip(), purpose="AWS configuration"))

    entrypoints = ["app.main:app"] if (app_dir / "app" / "main.py").is_file() else []
    return AppModel(
        pam_version="0.2",
        app_name=app_dir.name,
        platform="aws",
        entrypoints=entrypoints,
        test_command="pytest -q tests",
        components=list(components.values()),
        files=files,
        env=env,
    )


def _function_name(tree: ast.Module, line_number: int) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line_number <= (node.end_lineno or node.lineno):
                return node.name
    return None


# ---------- integration additions (C, 20 Nov): the real ShopLite needs these ----------
# ShopLite reaches DynamoDB through a helper (`_table().put_item(...)`) and keeps names/keys in Terraform; without
# them orders had no usages and no partition key, so render() failed and A's provisioner got wrong names.

ENV_HINT = {"dynamodb": "TABLE", "s3": "BUCKET", "sqs": "QUEUE"}


def _root(node: ast.AST) -> ast.Name | None:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node if isinstance(node, ast.Name) else None


def _add_helper_receivers(tree: ast.Module, receivers: dict[str, str]) -> None:
    """Module-level functions that return a receiver are receivers too (two passes: helper → helper)."""
    for _ in range(2):
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in receivers:
                continue
            for ret in ast.walk(fn):
                root = _root(ret.value) if isinstance(ret, ast.Return) and ret.value is not None else None
                if root is not None and root.id in receivers:
                    receivers[fn.name] = receivers[root.id]
                    break


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _env_names(tree: ast.Module) -> list[str]:
    """Names read with os.environ[...], os.environ.get(...) or os.getenv(...), in source order."""
    names: list[str] = []
    for node in ast.walk(tree):
        arg: ast.AST | None = None
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "os.environ":
            arg = node.slice
        elif isinstance(node, ast.Call) and _dotted(node.func) in ("os.environ.get", "os.getenv") and node.args:
            arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in names:
            names.append(arg.value)
    return names


def _matching(text: str, start: int) -> int:
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return i


def _tf_body(source: str, resource: str, name: str) -> str | None:
    m = re.search(rf'resource\s+"{re.escape(resource)}"\s+"{re.escape(name)}"\s*\{{', source)
    return source[m.end():_matching(source, m.end()) - 1] if m else None


def _tf_nested(body: str, block: str) -> list[str]:
    return [body[m.end():_matching(body, m.end()) - 1] for m in re.finditer(rf"\b{block}\s*\{{", body)]


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


def _apply_terraform(component: Component, resource: str, name: str, source: str) -> None:
    """Real resource name, keys, GSIs and queue settings from the Terraform block (the PAM contract)."""
    body = _tf_body(source, resource, name)
    if body is None:
        return
    top = _tf_top(body)
    real = _tf_attr(top, "bucket") or _tf_attr(top, "name") or _tf_attr(top, "function_name")
    if real and "${" not in real and not real.startswith(("aws_", "var.")):
        component.resource_name = real
    if resource == "aws_dynamodb_table":
        types: dict[str, str] = {}
        for attr in _tf_nested(body, "attribute"):
            n, t = _tf_attr(attr, "name"), _tf_attr(attr, "type")
            if n:
                types[n] = t if t in ("S", "N") else "S"

        def key(n: str) -> KeyAttr:
            return KeyAttr(name=n, type=types.get(n, "S"))  # type: ignore[arg-type]

        hash_key, range_key = _tf_attr(top, "hash_key"), _tf_attr(top, "range_key")
        if hash_key:
            component.partition_key = key(hash_key)
        if range_key:
            component.sort_key = key(range_key)
        indexes = []
        for gsi in _tf_nested(body, "global_secondary_index"):
            n, hk, rk = _tf_attr(gsi, "name"), _tf_attr(gsi, "hash_key"), _tf_attr(gsi, "range_key")
            if n and hk:
                indexes.append(Index(name=n, partition_key=key(hk), sort_key=key(rk) if rk else None))
        if indexes:
            component.indexes = indexes
    elif resource == "aws_sqs_queue":
        vt = _tf_attr(top, "visibility_timeout_seconds")
        if vt and vt.isdigit():
            component.visibility_timeout_s = int(vt)
        mrc = re.search(r"maxReceiveCount\s*=\s*(\d+)", body)
        if mrc:
            component.max_receive_count = int(mrc.group(1))
        if (_tf_attr(top, "fifo_queue") or "").lower() == "true":
            component.fifo = True
