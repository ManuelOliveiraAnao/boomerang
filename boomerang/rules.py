"""Rules check after every LLM output (docs/13 S2, S3, S8). Owner C, task C3.

Spec: docs/15 §4.3. Stub until C3.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.slicer import FunctionSlice

FORBIDDEN_CALLS = {"os.system", "os.popen", "subprocess", "eval", "exec", "compile", "open",
                   "socket", "requests", "urllib", "httpx", "__import__", "importlib", "shutil", "pickle"}


class RuleViolation(Exception):
    """An LLM output broke a rule. The caller rejects it and emits an event."""


def check_path(app_dir: Path, rel: str) -> Path:
    """Resolved, inside app_dir, not under tests/ or sovereign_sdk/ (normcase on Windows)."""
    raise NotBuilt("rules.check_path", owner="C", doc="docs/15 §4.3")


def clean_source(raw: str) -> str:
    """Strip ``` fences, leading prose, dedent."""
    raise NotBuilt("rules.clean_source", owner="C", doc="docs/15 §4.3")


def check_function(original: FunctionSlice, new_source: str, allowed_extra_names: set[str],
                   module_globals: set[str]) -> list[str]:
    """Violations (empty = ok)."""
    raise NotBuilt("rules.check_function", owner="C", doc="docs/15 §4.3")


def check_module(text: str, rel: str) -> None:
    """``ast.parse`` + ``compile(..., "exec")`` on the stitched file."""
    raise NotBuilt("rules.check_module", owner="C", doc="docs/15 §4.3")
