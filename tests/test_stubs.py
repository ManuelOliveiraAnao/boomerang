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
    ("boomerang.cost", "estimate", ({}, {}, {}), {}, "D"),
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

    with pytest.raises(NotBuilt):
        Table().get_item(Key={"order_id": "1"})


def test_every_module_imports():
    for module in ("boomerang.pipeline", "sovereign_sdk.storage", "boomerang.cli", "boomerang.scanner.ai",
                   "boomerang.generators", "sovereign_sdk"):
        importlib.import_module(module)
