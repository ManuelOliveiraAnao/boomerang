"""Function-level slicing and stitching with ``ast`` (docs/02 D4). Owner C, task C3.

Spec: docs/03 §3.6, docs/15 §4.2. Stub until C3.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.models import AppModel, Component


@dataclass
class FunctionSlice:
    file: str                # relative, posix
    name: str                # "get_order"
    qualname: str            # "Repo.get_order"
    start: int               # lineno of `def` (decorators stay outside the slice)
    end: int                 # end_lineno
    col: int                 # indentation of the def
    source: str              # dedented
    is_async: bool
    module_names: list[str] = field(default_factory=list)   # module-level names the function reads
    context: str = ""        # source lines that define those names (assignments/imports), max ~30 lines


def load_module(path: Path) -> tuple[str, ast.Module, str]:
    """Text, tree, newline style ("\\r\\n" or "\\n")."""
    raise NotBuilt("slicer.load_module", owner="C", doc="docs/15 §4.2")


def functions_with_usages(app_dir: Path, pam: AppModel) -> dict[str, list[tuple[FunctionSlice, list[Component]]]]:
    raise NotBuilt("slicer.functions_with_usages", owner="C", doc="docs/15 §4.2")


def function_at(app_dir: Path, file: str, line: int) -> FunctionSlice | None:
    """Fixer: traceback frame → function."""
    raise NotBuilt("slicer.function_at", owner="C", doc="docs/15 §4.2")


def replace_function(text: str, sl: FunctionSlice, new_source: str, newline: str) -> str:
    raise NotBuilt("slicer.replace_function", owner="C", doc="docs/15 §4.2")
