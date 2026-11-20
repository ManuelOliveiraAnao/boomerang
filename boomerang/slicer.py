"""Function-level slicing and stitching with ``ast`` (docs/02 D4). Owner C, task C3.

Spec: docs/03 §3.6, docs/15 §4.2.

- A ``CodeRef`` maps to the **innermost** function whose ``lineno <= line <= end_lineno``. If
  ``CodeRef.symbol`` disagrees, the AST wins (warning). Refs outside any function are module level:
  the de-lock's deterministic client swap handles them, never the AI.
- ``replace_function`` re-indents with the ``def`` line's literal whitespace (tabs stay tabs; ``col_offset``
  counts UTF-8 bytes), keeps the file's newline style (CRLF on Windows clones) and never touches lines that
  sit inside a multi-line string (their leading whitespace is string content). Callers apply replacements
  **bottom-up** so earlier line numbers stay valid.
- Files are read as bytes and decoded as UTF-8: ``Path.read_text`` would turn CRLF into LF.
"""

from __future__ import annotations

import ast
import io
import os
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from boomerang.models import AppModel, Component

if TYPE_CHECKING:
    from boomerang.events import Emitter

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

CONTEXT_MAX_LINES = 30
HELPER_MAX_LINES = 6                 # helpers this short come whole into the AI context (they return clients)
BOM = "﻿"

# One line with its ending, split exactly where the tokenizer counts lines (\r\n, \n, \r). Not
# str.splitlines(): it also splits on \x0c, \x1c,  , …, which would shift every line number after them.
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+\Z")
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)


@dataclass
class FunctionSlice:
    file: str                # relative, posix
    name: str                # "get_order"
    qualname: str            # "Repo.get_order"
    start: int               # lineno of `def` (decorators stay outside the slice)
    end: int                 # end_lineno
    col: int                 # indentation of the def (col_offset: UTF-8 bytes)
    source: str              # dedented, LF
    is_async: bool
    module_names: list[str] = field(default_factory=list)   # module-level names the function reads
    context: str = ""        # source lines that define those names (assignments/imports), max ~30 lines


@dataclass
class ModuleInfo:
    """One parsed app file. ``lines`` keep their endings; ``string_lines`` are 1-based."""

    rel: str
    text: str
    tree: ast.Module
    newline: str
    lines: list[str]
    string_lines: set[int]
    functions: list[tuple[FunctionNode, str]]           # (node, qualname), in source order
    bound: dict[str, list[ast.stmt]]                    # module-level name → top-level statements binding it


# ---------- text helpers ----------

def split_lines(text: str) -> list[str]:
    """Lines with their endings; ``"".join(split_lines(t)) == t``."""
    return _LINE.findall(text)


def newline_style(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _ending(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def string_lines(source: str) -> set[int]:
    """1-based numbers of the lines that continue a multi-line string literal (not its first line).

    Their leading whitespace is part of the string, so (de)indenting must leave them alone.
    """
    inside: set[int] = set()
    fstring_starts: list[int] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(_lf(source)).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                inside.update(range(tok.start[0] + 1, tok.end[0] + 1))
            elif tok.type == _FSTRING_START:
                fstring_starts.append(tok.start[0])
            elif tok.type == _FSTRING_END and fstring_starts:
                first = fstring_starts.pop()
                if tok.end[0] > first:
                    inside.update(range(first + 1, tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        pass                                # best effort: unparsable code is rejected later anyway
    return inside


def _dedent_lines(lines: list[str], prefix: str, skip: set[int]) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(lines, 1):
        if i in skip:
            out.append(line)
        elif line.startswith(prefix):
            out.append(line[len(prefix):])
        elif not line.strip():
            out.append("")
        else:
            out.append(line)                # e.g. a comment at column 0 inside a method
    return out


def _indent_lines(lines: list[str], prefix: str, skip: set[int]) -> list[str]:
    return [line if i in skip or not line.strip() else prefix + line for i, line in enumerate(lines, 1)]


def dedent_source(source: str) -> str:
    """Remove the first code line's indentation from every line outside multi-line strings. LF out."""
    lines = _lf(source).split("\n")
    first = next((ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")), "")
    prefix = _indent_of(first)
    if not prefix:
        return "\n".join(lines)
    return "\n".join(_dedent_lines(lines, prefix, string_lines("\n".join(lines))))


def normalize_source(source: str) -> str:
    """Comparable form of a function's source: LF, dedented, no leading blank lines, no trailing whitespace."""
    lines = dedent_source(source.removeprefix(BOM)).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).rstrip()


def normalize_path(file: str, app_dir: Path | None = None) -> str | None:
    """``app\\db.py`` / ``./app/db.py`` / an absolute path under ``app_dir`` → ``app/db.py``.

    ``None`` for an absolute path outside ``app_dir`` (or any absolute path when ``app_dir`` is None).
    """
    raw = file.strip()
    win = PureWindowsPath(raw)
    if win.drive and not win.is_absolute():         # "C:foo" is relative to the cwd of drive C
        return None
    if win.is_absolute() or raw.startswith(("/", "\\")):
        if app_dir is None:
            return None
        try:
            raw = os.path.relpath(Path(raw).resolve(), Path(app_dir).resolve())
        except ValueError:                  # another drive on Windows
            return None
        if raw == ".." or raw.startswith(("..\\", "../")):
            return None
    rel = raw.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return PurePosixPath(rel).as_posix() if rel else None


# ---------- AST helpers (shared with rules.py and delock.py) ----------

def index_functions(tree: ast.AST) -> list[tuple[FunctionNode, str]]:
    """Every function and method with a dotted qualname (``Repo.get_order``, ``outer.inner``)."""
    out: list[tuple[FunctionNode, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                out.append((child, qual))
                visit(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    out.sort(key=lambda item: (item[0].lineno, item[0].col_offset))
    return out


def _target_names(target: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _alias_name(alias: ast.alias) -> str:
    return alias.asname or alias.name.split(".")[0]


def bound_by(stmt: ast.stmt) -> set[str]:
    """Names a top-level statement binds in the module namespace (not inside nested defs/classes)."""
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            return                                       # their bodies are another scope
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_alias_name(a) for a in node.names if a.name != "*")
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return                                       # own scope (walrus inside them is ignored)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(stmt)
    return names


def module_bindings(tree: ast.Module) -> dict[str, list[ast.stmt]]:
    bound: dict[str, list[ast.stmt]] = {}
    for stmt in tree.body:
        for name in bound_by(stmt):
            bound.setdefault(name, []).append(stmt)
    return bound


def module_bound_names(tree: ast.Module) -> set[str]:
    return set(module_bindings(tree))


def names_read(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def local_names(fn: FunctionNode) -> set[str]:
    """Names bound anywhere inside the function (flow-insensitive), minus those declared ``global``."""
    names: set[str] = set()
    declared_global: set[str] = set()
    for node in ast.walk(fn):
        if node is fn:
            continue
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_alias_name(a) for a in node.names if a.name != "*")
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
    return names - declared_global


# ---------- modules ----------

def load_module(path: Path) -> tuple[str, ast.Module, str]:
    """Text, tree, newline style ("\\r\\n" or "\\n")."""
    text = Path(path).read_bytes().decode("utf-8")
    tree = ast.parse(text.removeprefix(BOM), filename=str(path))
    return text, tree, newline_style(text)


def module_info(app_dir: Path, rel: str) -> ModuleInfo:
    text, tree, newline = load_module(Path(app_dir) / rel)
    return ModuleInfo(
        rel=rel, text=text, tree=tree, newline=newline, lines=split_lines(text),
        string_lines=string_lines(text.removeprefix(BOM)), functions=index_functions(tree),
        bound=module_bindings(tree),
    )


def innermost(info: ModuleInfo, line: int) -> tuple[FunctionNode, str] | None:
    """The innermost function whose ``lineno <= line <= end_lineno`` (decorator lines are outside)."""
    best: tuple[FunctionNode, str] | None = None
    for node, qual in info.functions:
        inside = node.lineno <= line <= (node.end_lineno or node.lineno)
        if inside and (best is None or node.lineno >= best[0].lineno):
            best = (node, qual)
    return best


def make_slice(info: ModuleInfo, node: FunctionNode, qualname: str) -> FunctionSlice:
    start, end = node.lineno, node.end_lineno or node.lineno
    raw = [ln.rstrip("\r\n") for ln in info.lines[start - 1:end]]
    if start == 1:
        raw[0] = raw[0].removeprefix(BOM)
    prefix = _indent_of(raw[0])
    skip = {i - start + 1 for i in info.string_lines if start <= i <= end}
    source = "\n".join(_dedent_lines(raw, prefix, skip))

    used = sorted((names_read(node) & set(info.bound)) - local_names(node))
    return FunctionSlice(
        file=info.rel, name=node.name, qualname=qualname, start=start, end=end, col=node.col_offset,
        source=source, is_async=isinstance(node, ast.AsyncFunctionDef),
        module_names=used, context=module_context(info, used),
    )


def module_context(info: ModuleInfo, names: list[str], max_lines: int = CONTEXT_MAX_LINES) -> str:
    """Top-level statements that define ``names``, plus what those assignments depend on, in file order.

    Functions and classes contribute only their header line (the AI must not edit them), except small
    helpers (fewer than ``HELPER_MAX_LINES`` lines, e.g. ``def _table(): return dynamodb.Table(...)``): the AI
    has to see what they return, so they come whole, with the module-level names they read.
    """
    def small_helper(stmt: ast.stmt) -> bool:
        return (isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (stmt.end_lineno or stmt.lineno) - stmt.lineno < HELPER_MAX_LINES)

    seen: dict[int, ast.stmt] = {}
    todo = list(names)
    while todo:
        name = todo.pop()
        for stmt in info.bound.get(name, []):
            if id(stmt) in seen:
                continue
            seen[id(stmt)] = stmt
            if small_helper(stmt):
                todo.extend((names_read(stmt) - local_names(stmt)) & set(info.bound))  # type: ignore[arg-type]
            elif not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                todo.extend(names_read(stmt) & set(info.bound))

    out: list[str] = []
    for stmt in sorted(seen.values(), key=lambda s: s.lineno):
        if small_helper(stmt):
            out.extend(ln.rstrip("\r\n") for ln in info.lines[stmt.lineno - 1:stmt.end_lineno or stmt.lineno])
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            header_end = max(stmt.lineno, stmt.body[0].lineno - 1) if stmt.body else stmt.lineno
            chunk = [ln.rstrip("\r\n") for ln in info.lines[stmt.lineno - 1:header_end]] + ["    ..."]
        else:
            chunk = [ln.rstrip("\r\n") for ln in info.lines[stmt.lineno - 1:stmt.end_lineno or stmt.lineno]]
        out.extend(chunk)
    if out:
        out[0] = out[0].removeprefix(BOM)
    if len(out) > max_lines:
        out = out[:max_lines] + ["# … (more module-level lines omitted)"]
    return "\n".join(out)


# ---------- public API ----------

def functions_with_usages(app_dir: Path, pam: AppModel, emit: Emitter | None = None
                          ) -> dict[str, list[tuple[FunctionSlice, list[Component]]]]:
    """``{file: [(slice, components used in it), …]}``, functions in source order.

    Only ``.py`` files under ``app_dir``; module-level refs (no enclosing function) are left out.
    """
    app_dir = Path(app_dir)
    infos: dict[str, ModuleInfo | None] = {}
    found: dict[str, dict[int, tuple[FunctionNode, str, list[Component]]]] = {}

    for comp in pam.components:
        for ref in comp.usages:
            rel = normalize_path(ref.file, app_dir)
            if rel is None or not rel.endswith(".py"):
                continue
            if rel not in infos:
                infos[rel] = _try_load(app_dir, rel, emit)
            info = infos[rel]
            if info is None:
                continue
            hit = innermost(info, ref.line)
            if hit is None:
                continue                                    # module level: client swap, not the AI
            node, qual = hit
            if ref.symbol and ref.symbol not in (node.name, qual):
                _warn(emit, f"{rel}:{ref.line}: the scan says {ref.symbol!r}, the code says {qual!r}; "
                            "using the code", file=rel, line=ref.line, symbol=ref.symbol, function=qual)
            entry = found.setdefault(rel, {}).setdefault(node.lineno, (node, qual, []))
            if all(c.id != comp.id for c in entry[2]):
                entry[2].append(comp)

    result: dict[str, list[tuple[FunctionSlice, list[Component]]]] = {}
    for rel in sorted(found):
        info = infos[rel]
        assert info is not None
        result[rel] = [(make_slice(info, node, qual), comps)
                       for _, (node, qual, comps) in sorted(found[rel].items())]
    return result


def function_at(app_dir: Path, file: str, line: int) -> FunctionSlice | None:
    """Fixer: traceback frame → function. ``None`` if the file is missing, unreadable or the line is module level."""
    rel = normalize_path(file, Path(app_dir))
    if rel is None:
        return None
    try:
        info = module_info(Path(app_dir), rel)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    hit = innermost(info, line)
    return make_slice(info, *hit) if hit else None


def replace_function(text: str, sl: FunctionSlice, new_source: str, newline: str) -> str:
    """Replace lines ``sl.start..sl.end`` of ``text`` with ``new_source``, re-indented like the ``def`` line.

    An unchanged function returns ``text`` itself (byte-identical, whatever its whitespace).
    """
    if normalize_source(new_source) == normalize_source(sl.source):
        return text
    lines = split_lines(text)
    if not 1 <= sl.start <= sl.end <= len(lines):
        raise ValueError(f"{sl.file}: lines {sl.start}..{sl.end} are outside the file ({len(lines)} lines)")

    first = lines[sl.start - 1]
    bom = BOM if sl.start == 1 and first.startswith(BOM) else ""
    prefix = _indent_of(first.removeprefix(BOM))

    new = normalize_source(new_source)
    new_lines = new.split("\n")
    body = _indent_lines(new_lines, prefix, string_lines(new))
    block = bom + newline.join(body) + _ending(lines[sl.end - 1])
    return "".join(lines[: sl.start - 1]) + block + "".join(lines[sl.end:])


# ---------- internals ----------

def _try_load(app_dir: Path, rel: str, emit: Emitter | None) -> ModuleInfo | None:
    path = app_dir / rel
    if not path.is_file():
        _warn(emit, f"{rel}: the scan points at a file that isn't in the app; skipped", file=rel)
        return None
    try:
        return module_info(app_dir, rel)
    except (SyntaxError, UnicodeDecodeError) as e:
        _warn(emit, f"{rel}: can't parse ({type(e).__name__}: {e}); skipped", file=rel)
        return None


def _warn(emit: Emitter | None, message: str, **data) -> None:
    if emit is not None:
        emit.emit("delock", message, "warning", **data)
