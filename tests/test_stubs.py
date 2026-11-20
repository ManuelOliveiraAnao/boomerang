"""C1 stubs: every module imports, and every unbuilt binding raises NotBuilt with its owner."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from boomerang._stub import NotBuilt

P = Path(".")

STUBS = [
    # (module, callable, args, kwargs, owner)
    ("boomerang.scanner", "scan", (P, None), {}, "B"),
    ("boomerang.scanner.static", "scan_static", (P,), {}, "B"),
    ("boomerang.assess", "assess", (None, None), {}, "B"),
    ("boomerang.lockin", "score", (None,), {}, "B"),
    ("boomerang.generators.leaseweb", "make_secrets", (None,), {}, "B"),
    ("boomerang.generators.leaseweb", "render", (None, {}), {}, "B"),
    ("boomerang.lost", "build", (None, None, []), {}, "B"),
    ("sovereign_sdk.kv", "resource", (), {}, "B"),
    ("sovereign_sdk.kv", "ddl", (None,), {}, "B"),
    ("boomerang.lab", "up", (), {}, "A"),
    ("boomerang.lab", "status", (), {}, "A"),
    ("boomerang.lab", "reset", (), {}, "A"),
    ("boomerang.verifier", "verify", (P, 1, None), {}, "A"),
    ("boomerang.verifier", "run_tests", (P, {}, "aws", 0, None), {}, "A"),
    ("boomerang.verifier", "aws_env", (), {}, "A"),
    ("boomerang.verifier", "leaseweb_env", (P,), {}, "A"),
    ("boomerang.verifier", "reset_data", (P,), {}, "A"),
    ("boomerang.verifier", "stop_service", (P, "worker"), {}, "A"),
    ("boomerang.verifier", "start_service", (P, "worker"), {}, "A"),
    ("boomerang.provisioner_aws", "provision", (None,), {}, "A"),
    ("boomerang.provisioner_aws", "seed", (None, 25), {}, "A"),
    ("boomerang.datamover", "move", (None, "aws->leaseweb", {}, {}, None), {}, "A"),
    ("sovereign_sdk.queue", "client", (), {}, "A"),
    ("boomerang.cost", "estimate", ({}, {}, {}), {}, "D"),
    ("boomerang.slicer", "replace_function", ("", None, "", "\n"), {}, "C"),
    ("boomerang.rules", "check_module", ("", "a.py"), {}, "C"),
    ("boomerang.delock", "delock_detailed", (P, None, None, None, P), {}, "C"),
    ("boomerang.fixer", "frames", ("",), {}, "C"),
    ("boomerang.report", "write", (None,), {}, "C"),
    ("boomerang.pipeline", "plan", (P,), {}, "C"),
    ("boomerang.pipeline", "approve", ("r1",), {}, "C"),
    ("boomerang.pipeline", "execute", ("r1",), {}, "C"),
    ("boomerang.pipeline", "roundtrip", ("r1",), {}, "C"),
    ("sovereign_sdk.storage", "s3_client", (), {}, "C"),
]


def test_not_built_is_a_not_implemented_error():
    assert issubclass(NotBuilt, NotImplementedError)
    assert "owner B" in str(NotBuilt("x", owner="B", doc="docs/03"))


@pytest.mark.parametrize("module, name, args, kwargs, owner", STUBS, ids=[f"{m}.{n}" for m, n, *_ in STUBS])
def test_stub_raises_not_built_with_owner(module, name, args, kwargs, owner):
    fn = getattr(importlib.import_module(module), name)
    with pytest.raises(NotBuilt) as info:
        fn(*args, **kwargs)
    assert info.value.owner == owner


def test_adapter_classes_are_stubbed_too():
    from sovereign_sdk.kv import Table
    from sovereign_sdk.queue import Queue

    with pytest.raises(NotBuilt):
        Table().get_item(Key={"order_id": "1"})
    with pytest.raises(NotBuilt):
        Queue().send_message(QueueUrl="q", MessageBody="{}")


def test_every_module_imports():
    for module in ("boomerang.pipeline", "sovereign_sdk.storage", "boomerang.cli", "boomerang.scanner.ai",
                   "boomerang.generators", "sovereign_sdk"):
        importlib.import_module(module)
