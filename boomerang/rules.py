"""Rules check after every LLM output (docs/13 S2, S3, S8). Owner C, task C3.

Spec: docs/15 §4.3. The AI proposes code for one function; plain code decides whether it may land.

``check_function`` rejects an answer when any of these is true:

1. It doesn't parse, isn't **exactly one** top-level function, or still carries decorators.
2. The name, the parameters (names, defaults, kinds) or async-ness differ from the original.
3. It imports, declares ``global``/``nonlocal`` (unless the original did) or touches a forbidden name.
4. It calls ``boto3.client`` / ``boto3.resource`` / ``boto3.Session`` (``boto3.dynamodb.conditions.Key`` and
   ``botocore.exceptions.ClientError`` stay allowed: the adapters accept them, docs/15 L6).
5. It reads a module-level name the original didn't (other than the adapter clients), a builtin outside a
   safe set, a name that doesn't exist, a dunder, or an ``os.`` attribute other than ``environ``/``getenv``.
   The de-locked code runs on this laptop during verify, so it must not reach new capabilities.
6. It contains ``<REDACTED:``: the function held a secret literal; a human fixes it.
7. (L14) A call on an adapter object uses a method or keyword argument the adapter doesn't have, or an
   expression the adapter can't run (a string ``KeyConditionExpression``, an unsupported condition).

The same adapter-call analysis decides, before any AI call, whether a function is already a 1:1 mapping
(``one_to_one``, docs/15 L14).
"""

from __future__ import annotations

import ast
import builtins
import inspect
import os
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath

from boomerang.slicer import (
    BOM,
    FunctionNode,
    FunctionSlice,
    bound_by,
    dedent_source,
    local_names,
    names_read,
)

FORBIDDEN_CALLS = {"os.system", "os.popen", "subprocess", "eval", "exec", "compile", "open",
                   "socket", "requests", "urllib", "httpx", "__import__", "importlib", "shutil", "pickle"}

CLIENT_FACTORIES = {"boto3.client", "boto3.resource", "boto3.Session", "boto3.session.Session",
                    "boto3.setup_default_session"}

SAFE_BUILTINS = {
    "len", "int", "str", "float", "bool", "dict", "list", "tuple", "set", "sorted", "min", "max", "sum",
    "any", "all", "enumerate", "range", "zip", "isinstance", "round", "abs", "repr",
    "reversed", "frozenset", "bytes", "divmod", "next", "iter", "map", "filter",
    "Exception", "ValueError", "KeyError", "TypeError", "RuntimeError", "LookupError", "IndexError",
    "AttributeError", "NotImplementedError", "StopIteration", "ArithmeticError", "ZeroDivisionError",
}
OS_ALLOWED = {"environ", "getenv"}
BUILTIN_NAMES = set(dir(builtins))

PROTECTED_DIRS = {"tests", "test", "sovereign_sdk"}

# ---------- adapters (docs/02 §5) ----------
# boto3 factory → (adapter module, factory function, adapter type). Only these three are swapped (§4.4 step 5).
SWAPS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("resource", "dynamodb"): ("kv", "resource", "kv.KV"),
    ("client", "sqs"): ("queue", "client", "queue.Queue"),
    ("client", "s3"): ("storage", "s3_client", "storage.s3"),
}
ADAPTER_FACTORIES = {(mod, fn): typ for mod, fn, typ in SWAPS.values()}
CHECKED_TYPES = ("kv.KV", "kv.Table", "queue.Queue")     # storage.s3 is a real boto3 client: not checked
DEFAULT_ADAPTERS: dict[str, str] = {"kv": "kv", "queue": "queue", "storage": "storage"}   # local name → module
_CONDITION = re.compile(r"^\s*attribute_(?:not_)?exists\(\s*#?\w+\s*\)\s*$")
_UPDATE_SET = re.compile(r"^\s*SET\s+[^()]+$", re.IGNORECASE)
_UPDATE_OTHER = re.compile(r"\b(?:ADD|REMOVE|DELETE)\b")


class RuleViolation(Exception):
    """An LLM output broke a rule. The caller rejects it and emits an event."""


# ---------- paths ----------

def check_path(app_dir: Path, rel: str) -> Path:
    """Resolved, inside app_dir, not under tests/ or sovereign_sdk/ (normcase on Windows)."""
    if not rel or not rel.strip():
        raise RuleViolation("empty path")
    win = PureWindowsPath(rel)
    if win.drive or win.is_absolute() or PurePosixPath(rel).is_absolute() or rel.startswith(("/", "\\")):
        raise RuleViolation(f"{rel}: absolute and drive-relative paths are not allowed")
    root = Path(app_dir).resolve()
    target = (root / rel.replace("\\", "/")).resolve()
    nroot, ntarget = os.path.normcase(str(root)), os.path.normcase(str(target))
    try:
        inside = os.path.commonpath([nroot, ntarget]) == nroot and ntarget != nroot
    except ValueError:                                       # another drive
        inside = False
    if not inside:
        raise RuleViolation(f"{rel}: outside the app")
    parts = [p.lower() for p in PurePosixPath(os.path.relpath(ntarget, nroot).replace("\\", "/")).parts]
    if PROTECTED_DIRS.intersection(parts[:-1]) or _is_test_file(parts[-1]):
        raise RuleViolation(f"{rel}: tests/ and sovereign_sdk/ are never written by the AI")
    return target


def _is_test_file(name: str) -> bool:
    return name == "conftest.py" or (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))


# ---------- cleanup before the check ----------

_FENCE = re.compile(r"```[ \t]*[\w+-]*[ \t]*\n(.*?)(?:\n[ \t]*```|\Z)", re.DOTALL)
_CODE_START = re.compile(r"^[ \t]*(?:async[ \t]+def[ \t]|def[ \t]|@|import[ \t]|from[ \t]+\S+[ \t]+import[ \t]|class[ \t])")
_PROSE = re.compile(r"^[A-Za-z][^(){}\[\]=<>#]*$")         # a sentence at column 0: no brackets, no `=`


def clean_source(raw: str) -> str:
    """Strip ``` fences, leading prose, trailing prose; dedent. LF out. Deterministic."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text and "\\n" in text:                   # a double-escaped JSON string from a small model
        text = text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    blocks = _FENCE.findall(text)
    if blocks:
        text = next((b for b in blocks if re.search(r"^[ \t]*(?:async[ \t]+)?def[ \t]", b, re.MULTILINE)), blocks[0])
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if _CODE_START.match(ln)), None)
    if first is not None:
        lines = lines[first:]
    text = dedent_source("\n".join(lines))
    return _trim_trailing_prose(text).strip("\n").rstrip()


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def _trim_trailing_prose(text: str) -> str:
    if _parses(text):
        return text
    lines = text.split("\n")
    while lines and not _parses("\n".join(lines)):
        last = lines[-1]
        if not last.strip():
            lines.pop()
            continue
        if not _PROSE.match(last) or len(last.split()) < 3:
            return text                                     # looks like code: let the rules report it
        lines.pop()
    return "\n".join(lines) if lines else text


def module_imports(tree: ast.Module) -> set[str]:
    return {ast.unparse(s) for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))}


def strip_known(source: str, imports: set[str], decorators: set[str]) -> tuple[str, list[str]]:
    """Drop leading imports that **exactly** match the module's, and decorators that match the original's.

    Returns the cleaned source and a note per dropped line (logged in the event). Anything else stays and
    is judged by ``check_function``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []
    drop: set[int] = set()
    notes: list[str] = []
    body = list(tree.body)
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)) and ast.unparse(body[0]) in imports:
        stmt = body.pop(0)
        drop.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
        notes.append(f"dropped import already in the module: {ast.unparse(stmt)}")
    if body and isinstance(body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in body[0].decorator_list:
            if ast.unparse(dec) in decorators:
                drop.update(range(dec.lineno, (dec.end_lineno or dec.lineno) + 1))
                notes.append(f"dropped decorator kept in the file: @{ast.unparse(dec)}")
    if not drop:
        return source, []
    kept = [ln for i, ln in enumerate(source.split("\n"), 1) if i not in drop]
    return dedent_source("\n".join(kept)).strip("\n"), notes


# ---------- adapter calls (L14, rule 7) ----------

@dataclass
class AdapterCall:
    line: int
    end_line: int
    adapter: str                     # "kv.Table"
    method: str
    problems: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def adapter_api() -> dict[str, dict[str, inspect.Signature]]:
    """Public methods of the adapter classes, read from the real modules (B's and A's code once built)."""
    try:
        from sovereign_sdk import kv, queue
    except ImportError as e:                                 # pragma: no cover - environment problem
        raise RuntimeError(f"can't import sovereign_sdk.kv/queue to read the adapter interface: {e}") from e

    def public(cls: type) -> dict[str, inspect.Signature]:
        return {name: inspect.signature(fn) for name, fn in inspect.getmembers(cls, inspect.isfunction)
                if not name.startswith("_")}

    return {"kv.KV": public(kv.KV), "kv.Table": public(kv.Table), "queue.Queue": public(queue.Queue)}


def dotted(node: ast.AST) -> str | None:
    """``os.environ.get`` for an Attribute/Name chain rooted at a Name; None otherwise."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def boto3_service(call: ast.Call) -> tuple[str, str] | None:
    """``boto3.client("sqs", …)`` → ``("client", "sqs")``; same for ``boto3.resource``. None otherwise."""
    if dotted(call.func) not in ("boto3.client", "boto3.resource"):
        return None
    arg = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "service_name"), None)
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return call.func.attr, arg.value                     # type: ignore[attr-defined]
    return None


def expr_type(node: ast.AST, env: Mapping[str, str], adapters: Mapping[str, str]) -> str | None:
    """Adapter type of an expression: ``boto3.resource("dynamodb")`` / ``kv.resource()`` → ``kv.KV``, ``.Table()``
    on it → ``kv.Table``, … ``adapters`` maps the local names of the adapter modules in play to the module
    (``{"kv": "kv", "sovereign_queue": "queue"}``); a boto3 factory only counts if its adapter is in play."""
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name) and f"{node.func.id}()" in env:      # a helper: `_table()`
        return env[f"{node.func.id}()"]
    svc = boto3_service(node)
    if svc is not None:
        mod, _, typ = SWAPS.get(svc, (None, None, None))
        return typ if mod in adapters.values() else None
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id in adapters:
            return ADAPTER_FACTORIES.get((adapters[func.value.id], func.attr))
        if func.attr == "Table" and expr_type(func.value, env, adapters) == "kv.KV":
            return "kv.Table"
    return None


def module_adapter_types(tree: ast.Module, adapters: Mapping[str, str]) -> dict[str, str]:
    """Module-level names bound to adapter objects (``dynamodb`` → kv.KV, ``table`` → kv.Table, …), and
    helpers that return one (ShopLite: ``def _table(): return dynamodb.Table(...)`` → ``"_table()"``)."""
    env: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returns = [n.value for n in ast.walk(stmt) if isinstance(n, ast.Return) and n.value is not None]
            types = {expr_type(r, env, adapters) for r in returns}
            if len(types) == 1 and None not in types:
                env[f"{stmt.name}()"] = types.pop()  # type: ignore[assignment]
            else:
                env.pop(f"{stmt.name}()", None)
            env.pop(stmt.name, None)
            continue
        if isinstance(stmt, ast.Assign):
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            value: ast.AST | None = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            targets, value = [stmt.target.id], stmt.value
        else:
            for name in bound_by(stmt):
                env.pop(name, None)
            continue
        typ = expr_type(value, env, adapters) if value is not None else None
        for name in targets:
            if typ:
                env[name] = typ
            else:
                env.pop(name, None)
    return env


def function_env(fn: FunctionNode, module_env: Mapping[str, str], adapters: Mapping[str, str]) -> dict[str, str]:
    """Module adapter names minus those the function rebinds, plus simple local ``x = <adapter expr>``."""
    env = {k: v for k, v in module_env.items() if k not in local_names(fn)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            typ = expr_type(node.value, env, adapters)
            if typ:
                env[node.targets[0].id] = typ
    return env


def adapter_calls(fn: ast.AST, env: Mapping[str, str], adapters: Mapping[str, str], *,
                  strict: bool) -> list[AdapterCall]:
    """Every call (and attribute use) on an adapter object inside ``fn``, with what's wrong with it.

    ``strict`` (the L14 decision) also reports what code can't verify (a computed condition, ``**kwargs``);
    the rules check (``strict=False``) only reports what is definitely wrong.
    """
    api = adapter_api()
    as_func = {id(n.func): n for n in ast.walk(fn) if isinstance(n, ast.Call)}
    calls: list[AdapterCall] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Attribute):
            continue
        typ = expr_type(node.value, env, adapters)
        if typ not in CHECKED_TYPES:
            continue
        call = as_func.get(id(node))
        line, end = node.lineno, node.end_lineno or node.lineno
        if call is None:
            calls.append(AdapterCall(line, end, typ, node.attr, [f"{typ}.{node.attr} is not part of the adapter"]))
            continue
        line, end = call.lineno, call.end_lineno or call.lineno
        sig = api[typ].get(node.attr)
        if sig is None:
            calls.append(AdapterCall(line, end, typ, node.attr, [f"{typ} has no method {node.attr}()"]))
            continue
        calls.append(AdapterCall(line, end, typ, node.attr,
                                 _arg_problems(typ, node.attr, sig, call, strict)
                                 + _value_problems(typ, node.attr, call, strict)))
    calls.sort(key=lambda c: (c.line, c.method))
    return calls


def _arg_problems(typ: str, method: str, sig: inspect.Signature, call: ast.Call, strict: bool) -> list[str]:
    params = list(sig.parameters.values())[1:]                       # drop self
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return [f"{typ}.{method}() is called with */** unpacking; code can't check it"] if strict else []
    names = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    if not any(p.kind == p.VAR_KEYWORD for p in params):
        unknown = [k.arg for k in call.keywords if k.arg not in names]
        if unknown:
            return [f"{typ}.{method}() has no argument {', '.join(unknown)}"]
    try:
        sig.bind(None, *[None] * len(call.args), **{k.arg: None for k in call.keywords if k.arg})
    except TypeError as e:
        return [f"{typ}.{method}(): {e}"]
    return []


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _value_problems(typ: str, method: str, call: ast.Call, strict: bool) -> list[str]:
    if typ != "kv.Table":
        return []
    out: list[str] = []
    if method == "query":
        kce = _kwarg(call, "KeyConditionExpression") or (call.args[0] if call.args else None)
        if kce is not None:
            if isinstance(kce, (ast.Constant, ast.JoinedStr)):
                out.append("kv.Table.query(): KeyConditionExpression must be Key(name).eq(value), not a string")
            elif not _is_key_eq(kce) and strict:
                out.append("kv.Table.query(): code can't verify the KeyConditionExpression is Key(name).eq(value)")
            elif _is_key_call(kce) and not _is_key_eq(kce):
                out.append("kv.Table.query(): only Key(name).eq(value) is supported")
    if method in ("put_item", "update_item"):
        cond = _kwarg(call, "ConditionExpression")
        text = _const_str(cond)
        if text is not None and not _CONDITION.match(text):
            out.append(f"kv.Table.{method}(): ConditionExpression {text!r} is not supported "
                       "(only attribute_exists / attribute_not_exists)")
        elif cond is not None and text is None and strict:
            out.append(f"kv.Table.{method}(): code can't verify a computed ConditionExpression")
    if method == "update_item":
        upd = _kwarg(call, "UpdateExpression") or (call.args[1] if len(call.args) > 1 else None)
        text = _const_str(upd)
        if text is not None and (not _UPDATE_SET.match(text) or _UPDATE_OTHER.search(text)):
            out.append(f"kv.Table.update_item(): UpdateExpression {text!r} is not supported (only 'SET a = :v, …')")
        elif upd is not None and text is None and strict:
            out.append("kv.Table.update_item(): code can't verify a computed UpdateExpression")
    return out


def _is_key_call(node: ast.AST) -> bool:
    """``Key("x")…`` or ``boto3.dynamodb.conditions.Key("x")…`` somewhere at the root of the chain."""
    while isinstance(node, (ast.Call, ast.Attribute)):
        if isinstance(node, ast.Call) and (dotted(node.func) or "").split(".")[-1] == "Key":
            return True
        node = node.func if isinstance(node, ast.Call) else node.value
    return False


def _is_key_eq(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "eq"
            and isinstance(node.func.value, ast.Call)
            and (dotted(node.func.value.func) or "").split(".")[-1] == "Key")


def one_to_one(fn: FunctionNode, module_env: Mapping[str, str], adapters: Mapping[str, str],
               usage_lines: list[int]) -> list[str]:
    """L14: reasons the function is **not** already a 1:1 mapping onto the adapters; ``[]`` = it is.

    1:1 means: every adapter call uses only methods, kwargs and expressions the adapter has, and every AWS
    usage line the scan reported is one of those checked calls (code understood each of them).
    """
    calls = adapter_calls(fn, function_env(fn, module_env, adapters), adapters, strict=True)
    reasons = [p for c in calls for p in c.problems]
    covered = [c for c in calls if not c.problems]
    for line in sorted(set(usage_lines)):
        if not any(c.line <= line <= c.end_line for c in calls):
            reasons.append(f"line {line}: an AWS call code can't map onto an adapter call")
    if not covered and not reasons:
        reasons.append("no adapter call found")
    return reasons


# ---------- the rules check ----------

def check_function(original: FunctionSlice, new_source: str, allowed_extra_names: set[str],
                   module_globals: set[str], *, adapter_types: Mapping[str, str] | None = None,
                   adapters: Mapping[str, str] | None = None) -> list[str]:
    """Violations (empty = ok). ``adapter_types``/``adapters`` turn on rule 7."""
    try:
        tree = ast.parse(new_source)
    except SyntaxError as e:
        return [f"rule 1: doesn't parse: {e.msg} (line {e.lineno})"]
    # top-level imports next to the function are reported as rule 3 (a clearer message for the retry)
    top_imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
    rest = [s for s in tree.body if s not in top_imports]
    if len(rest) != 1 or not isinstance(rest[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["rule 1: the answer must be exactly one top-level function"]
    fn = rest[0]
    orig = ast.parse(original.source).body[0]
    assert isinstance(orig, (ast.FunctionDef, ast.AsyncFunctionDef))

    out: list[str] = [f"rule 3: imports are not allowed (line {s.lineno}: {ast.unparse(s)})" for s in top_imports]
    if fn.decorator_list:
        out.append("rule 1: no decorators in the answer (the original's stay in the file)")

    # rule 2: same shape
    if fn.name != original.name:
        out.append(f"rule 2: the name changed ({original.name} → {fn.name})")
    if ast.dump(fn.args) != ast.dump(orig.args):
        out.append("rule 2: the parameters changed (names, defaults or kinds)")
    if isinstance(fn, ast.AsyncFunctionDef) != original.is_async:
        out.append("rule 2: async-ness changed")

    # rule 3: imports, global/nonlocal, forbidden names
    orig_scope_decl = any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(orig))
    flagged: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(f"rule 3: imports are not allowed (line {node.lineno}: {ast.unparse(node)})")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and not orig_scope_decl:
            out.append(f"rule 3: {type(node).__name__.lower()} is not allowed (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            flagged.add(node.id)
        elif isinstance(node, ast.Attribute):
            chain = dotted(node)
            if chain:
                parts = chain.split(".")
                hit = next((".".join(parts[:i]) for i in range(1, len(parts) + 1)
                            if ".".join(parts[:i]) in FORBIDDEN_CALLS), None)
                if hit:
                    flagged.add(hit)
                # rule 4: creating AWS clients
                if chain in CLIENT_FACTORIES:
                    out.append(f"rule 4: creates an AWS client ({chain}); use the module-level clients")
    out.extend(f"rule 3: forbidden name {name!r}" for name in sorted(flagged))

    # rule 5: names and attributes the original didn't reach
    orig_reads = names_read(orig)
    orig_chains = {c for n in ast.walk(orig) if isinstance(n, ast.Attribute) and (c := dotted(n))}
    orig_dunder_attrs = {n.attr for n in ast.walk(orig) if isinstance(n, ast.Attribute) and _dunder(n.attr)}
    locals_ = local_names(fn)
    for name in sorted(names_read(fn) - locals_):
        if name in orig_reads or name in allowed_extra_names or name in flagged:
            continue
        if _dunder(name):
            out.append(f"rule 5: dunder name {name!r}")
        elif name in module_globals:
            out.append(f"rule 5: uses module-level name {name!r} that the original function didn't use")
        elif name in BUILTIN_NAMES:
            if name not in SAFE_BUILTINS:
                out.append(f"rule 5: builtin {name!r} is not allowed")
        else:
            out.append(f"rule 5: unknown name {name!r} (it would raise NameError)")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Attribute):
            continue
        if _dunder(node.attr) and node.attr not in orig_dunder_attrs:
            out.append(f"rule 5: dunder attribute .{node.attr}")
        chain = dotted(node)
        if (chain and chain.startswith("os.") and "os" not in locals_ and chain.split(".")[1] not in OS_ALLOWED
                and chain not in orig_chains and not any(chain.startswith(f"{h}.") or chain == h for h in flagged)):
            out.append(f"rule 5: {chain} is not allowed (only os.environ / os.getenv)")

    # rule 6: secrets
    if "<REDACTED:" in new_source:
        out.append("rule 6: the function holds a secret literal (<REDACTED:…>); a human must fix it")

    # rule 7: adapter calls (L14)
    if adapter_types is not None:
        adapters = adapters if adapters is not None else DEFAULT_ADAPTERS
        env = function_env(fn, adapter_types, adapters)
        out.extend(f"rule 7: {p}" for c in adapter_calls(fn, env, adapters, strict=False) for p in c.problems)

    return list(dict.fromkeys(out))


def _dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def check_module(text: str, rel: str) -> None:
    """``ast.parse`` + ``compile(..., "exec")`` on the stitched file. Raises RuleViolation."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text.removeprefix(BOM), filename=rel)
            compile(tree, rel, "exec", dont_inherit=True)
    except SyntaxError as e:
        raise RuleViolation(f"{rel}: doesn't compile: {e.msg} (line {e.lineno})") from None
    except ValueError as e:                                  # e.g. null bytes
        raise RuleViolation(f"{rel}: doesn't compile: {e}") from None
