"""C1 stubs: every module imports, and no stub is left once A, B, C and D are merged."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import boomerang
import sovereign_sdk
from boomerang._stub import NotBuilt


def test_not_built_is_a_not_implemented_error():
    assert issubclass(NotBuilt, NotImplementedError)
    assert "owner B" in str(NotBuilt("x", owner="B", doc="docs/03"))


def test_every_module_imports():
    for module in ("boomerang.pipeline", "sovereign_sdk.storage", "boomerang.cli", "boomerang.scanner.ai",
                   "boomerang.generators", "sovereign_sdk", "boomerang.cost", "boomerang.democache"):
        importlib.import_module(module)


def test_no_stub_is_left():
    """After the A+B+C+D merge, no module may still raise NotBuilt (the L9 fixture fallback is for the build)."""
    left = []
    for pkg in (boomerang, sovereign_sdk):
        for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
            if info.name == "boomerang._stub":
                continue
            source = inspect.getsource(importlib.import_module(info.name))
            if "raise NotBuilt(" in source:
                left.append(info.name)
    assert left == [], f"still stubs: {left}"
