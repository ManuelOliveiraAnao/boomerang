"""De-lock: AWS calls → sovereign_sdk, per function (docs/02 D3/D4). Owner C, task C3.

Spec: docs/03 §3.6, docs/15 §4.4. ``app_dir`` is always the **copy** under ``runs/<id>/leaseweb/app/``.

1. **Decide per component** from the assessment: ``object_store`` → config only; ``kv_table``/``queue`` that
   are migrated (not only 🔴) → per function; everything else (functions, IAM, alarms, triggers) is infra and
   not touched. Inside a migrated component, code checks first whether a function is already a 1:1 mapping
   onto the adapter (L14): then no AI call, ``RewriteRecord(kind="config", analysis="1:1 mapping, checked
   by code")``.
2. **Slice** with ``slicer.functions_with_usages``.
3. **AI per function** (sequential with Ollama, up to 3 at once with Anthropic): ``prompts/delock.md``,
   rules check, one retry with the violations listed, then keep the original and record "needs a human".
   ``LLMUnavailable`` and ``CacheMiss`` stop the stage: a silent fallback to the original (1:1-ish) code
   would still pass the tests and hide that the run differs from the recorded one.
4. **Stitch** bottom-up per file.
5. **Client swap** (deterministic): ``boto3.resource("dynamodb")`` → ``kv.resource()``, ``boto3.client("sqs")``
   → ``queue.client()``, ``boto3.client("s3")`` → ``storage.s3_client()``. Anything else stays (residual).
6. **Imports**: ``from sovereign_sdk import …`` (only what is used) after the leading imports, plus ``Key`` /
   ``ClientError`` when the AI used them; ``import boto3`` is dropped when nothing uses it any more.
7. ``rules.check_module`` on every changed file (a failure keeps the original file: it's a bug here).
8. **Residual check** with ``scanner.static.scan_static`` (L16), warnings only.
9. **Diffs** per function and per module, ``delock.json`` (``list[RewriteRecord]``) and
   ``assessment.final.json`` (approved findings + de-lock findings). ``assessment.json`` is never rewritten:
   its hash is the approval gate.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import jinja2

from boomerang import llm, rules, slicer
from boomerang._stub import NotBuilt
from boomerang.config import get_settings
from boomerang.events import Emitter
from boomerang.fsutil import atomic_write_bytes, atomic_write_text
from boomerang.models import (
    AppModel,
    Assessment,
    CodeRef,
    Component,
    Finding,
    FunctionRewrite,
    Light,
    RewriteRecord,
)
from boomerang.redact import redact
from boomerang.rules import SWAPS, RuleViolation
from boomerang.slicer import BOM, FunctionNode, FunctionSlice, ModuleInfo

ONE_TO_ONE = "1:1 mapping, checked by code"
MODULE = "<module>"                                  # RewriteRecord.function for module-level config changes
ADAPTER_OF_KIND = {"kv_table": "kv", "queue": "queue", "object_store": "storage"}
AI_KINDS = {"kv_table", "queue"}
HELPER_IMPORTS = {"Key": "from boto3.dynamodb.conditions import Key",
                  "ClientError": "from botocore.exceptions import ClientError"}
MARKERS = ("SCAN_DATA", "MODULE_CONTEXT", "FUNCTION_SOURCE", "PREVIOUS_ANSWER")
SKIP_DIRS = {"tests", "test", "sovereign_sdk", "__pycache__", ".venv", "venv", ".git", "node_modules",
             ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ANTHROPIC_PARALLEL = 3
_LIGHT_ORDER: dict[str, int] = {"green": 0, "amber": 1, "red": 2}


# ---------- public API ----------

def delock(app_dir: Path, pam: AppModel, assessment: Assessment, emit: Emitter) -> list[FunctionRewrite]:
    """Binding (docs/03 §3.6). ``app_dir`` is always the copy under runs/<id>/leaseweb/app/.

    Diffs and ``delock.json`` go to ``runs/<id>/`` (the folder above ``leaseweb/``), or next to ``app_dir``
    for any other layout.
    """
    app_dir = Path(app_dir)
    root = app_dir.parent.parent if app_dir.parent.name == "leaseweb" else app_dir.parent
    return _run(app_dir, pam, assessment, emit, root / "diffs", root).rewrites


def delock_detailed(app_dir: Path, pam: AppModel, assessment: Assessment, emit: Emitter,
                    diffs_dir: Path, *, out_dir: Path | None = None) -> list[RewriteRecord]:
    """What the pipeline calls; ``delock()`` wraps it. ``delock.json`` and ``assessment.final.json`` are
    written to ``out_dir`` (default: the parent of ``diffs_dir``, i.e. ``runs/<id>/``)."""
    diffs_dir = Path(diffs_dir)
    return _run(Path(app_dir), pam, assessment, emit, diffs_dir, Path(out_dir) if out_dir else diffs_dir.parent).records


def final_assessment(assessment: Assessment, extra: list[Finding]) -> Assessment:
    """The approved assessment plus the de-lock findings. ``overall`` only rises for migrated (non-red) lights."""
    if not extra:
        return assessment.model_copy(deep=True)
    lights: list[Light] = [assessment.overall, *[f.light for f in extra if f.light != "red"]]
    overall = max(lights, key=lambda light: _LIGHT_ORDER[light])
    reds = sum(f.light == "red" for f in extra)
    note = f" De-lock added {len(extra)} finding(s)" + (f", {reds} needing a human." if reds else ".")
    return Assessment(findings=[*assessment.findings, *extra], overall=overall, summary=assessment.summary + note)


# ---------- internals: data ----------

@dataclass
class _Result:
    records: list[RewriteRecord]
    rewrites: list[FunctionRewrite]
    findings: list[Finding]


@dataclass
class _FileCtx:
    info: ModuleInfo
    adapters: dict[str, str]          # local name → adapter module, e.g. {"kv": "kv", "sovereign_queue": "queue"}
    env: dict[str, str]               # module-level adapter objects: {"dynamodb": "kv.KV", "table": "kv.Table"}
    globals: set[str]
    imports: set[str]
    allowed: set[str]                 # rule 5 extras: adapter objects, adapter modules, Key, ClientError


@dataclass
class _Job:
    ctx: _FileCtx
    node: FunctionNode
    slice: FunctionSlice
    comps: list[Component]
    usage_lines: list[int]


@dataclass
class _Outcome:
    job: _Job
    record: RewriteRecord
    new_source: str | None = None     # accepted and changed → stitched
    rewrite: FunctionRewrite | None = None
    findings: list[Finding] = field(default_factory=list)


# ---------- the run ----------

def _run(app_dir: Path, pam: AppModel, assessment: Assessment, emit: Emitter | None,
         diffs_dir: Path, out_dir: Path) -> _Result:
    app_dir = app_dir.resolve()
    migrated, in_play = _decide(pam, assessment, emit)

    # 2. slice: functions that use a migrated kv/queue component
    sliced = slicer.functions_with_usages(app_dir, pam, emit)
    ctxs: dict[str, _FileCtx] = {}
    jobs: list[_Job] = []
    for rel in sorted(set(sliced) | set(_app_files(app_dir))):
        try:
            rules.check_path(app_dir, rel)
        except RuleViolation as e:
            _emit(emit, f"{rel}: skipped ({e})", "warning", file=rel)
            continue
        ctx = _file_ctx(app_dir, rel, in_play, emit)
        if ctx is None:
            continue
        ctxs[rel] = ctx
        selected = [(sl, [c for c in comps if c.id in migrated and c.kind in AI_KINDS])
                    for sl, comps in sliced.get(rel, [])]
        for sl, comps in _merge_nested([(sl, comps) for sl, comps in selected if comps], emit):
            node = next(n for n, q in ctx.info.functions if n.lineno == sl.start and q == sl.qualname)
            lines = [u.line for c in comps for u in c.usages
                     if slicer.normalize_path(u.file, app_dir) == rel and sl.start <= u.line <= sl.end]
            jobs.append(_Job(ctx, node, sl, comps, lines))

    # 3. per function: code first (L14), then the AI
    settings = get_settings()
    workers = ANTHROPIC_PARALLEL if settings.provider == "anthropic" and len(jobs) > 1 else 1
    if workers == 1:
        outcomes = [_process(job, emit) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(lambda job: _process(job, emit), jobs))

    # 4–7, 9: stitch, swap, imports, compile check, diffs — file by file
    records: list[RewriteRecord] = []
    by_file: dict[str, list[_Outcome]] = {}
    for o in outcomes:
        by_file.setdefault(o.job.slice.file, []).append(o)
    changed_files = 0
    for rel, ctx in ctxs.items():
        file_outcomes = by_file.get(rel, [])
        module_record, wrote = _apply_file(app_dir, ctx, file_outcomes, diffs_dir, out_dir, emit)
        records.extend(o.record for o in file_outcomes)
        if module_record is not None:
            records.append(module_record)
        changed_files += wrote

    # 8. residual lock-in (static scan, L16)
    _residual_check(app_dir, emit)

    findings = [f for o in outcomes for f in o.findings]
    rewrites = [o.rewrite for o in outcomes if o.rewrite is not None]
    _write_outputs(out_dir, records, assessment, findings)
    _summary(records, changed_files, emit)
    return _Result(records, rewrites, findings)


def _decide(pam: AppModel, assessment: Assessment, emit: Emitter | None) -> tuple[set[str], dict[str, str]]:
    """Component ids to migrate, and the adapter modules in play (``{"kv": "kv", …}``)."""
    migrated: set[str] = set()
    modules: set[str] = set()
    for comp in pam.components:
        lights = [f.light for f in assessment.findings if f.component_id == comp.id]
        adapter = ADAPTER_OF_KIND.get(comp.kind)
        if adapter is None or not comp.provider.startswith("aws."):
            _emit(emit, f"{comp.id} ({comp.kind}): not migrated, needs a human 🔴", "info",
                  component_id=comp.id, decision="not_migrated")
            continue
        if lights and all(light == "red" for light in lights):
            _emit(emit, f"{comp.id} ({comp.kind}): only 🔴 findings, not migrated", "warning",
                  component_id=comp.id, decision="not_migrated")
            continue
        migrated.add(comp.id)
        modules.add(adapter)
        if comp.kind in AI_KINDS:
            _emit(emit, f"{comp.id} ({comp.kind}): per function, code first (1:1 check), AI where code can't decide",
                  "info", component_id=comp.id, decision="per_function")
        else:
            _emit(emit, f"{comp.id} ({comp.kind}): 🟢 config change (endpoint + keys)", "info",
                  component_id=comp.id, decision="config")
    return migrated, {m: m for m in sorted(modules)}


def _app_files(app_dir: Path) -> list[str]:
    """Every app ``.py`` file that mentions boto3 (candidates for the client swap)."""
    out: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(app_dir)
        if SKIP_DIRS.intersection(p.lower() for p in rel.parts[:-1]) or rules._is_test_file(rel.name.lower()):
            continue
        try:
            if b"boto3" in path.read_bytes():
                out.append(rel.as_posix())
        except OSError:
            continue
    return out


def _file_ctx(app_dir: Path, rel: str, in_play: dict[str, str], emit: Emitter | None) -> _FileCtx | None:
    try:
        info = slicer.module_info(app_dir, rel)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        _emit(emit, f"{rel}: can't parse ({type(e).__name__}); left unchanged", "warning", file=rel)
        return None
    bound = set(info.bound)
    adapters: dict[str, str] = {}
    for module in in_play:
        existing = _existing_alias(info.tree, module)
        name = existing or (module if module not in bound else f"sovereign_{module}")
        adapters[name] = module
    env = rules.module_adapter_types(info.tree, adapters)
    allowed = set(env) | {name for name in adapters if name not in bound or _existing_alias(info.tree, adapters[name])}
    allowed |= {name for name, line in HELPER_IMPORTS.items()
                if name not in bound or any(ast.unparse(s) == line for s in info.bound[name])}
    return _FileCtx(info=info, adapters=adapters, env=env, globals=bound, imports=rules.module_imports(info.tree),
                    allowed=allowed)


def _existing_alias(tree: ast.Module, module: str) -> str | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "sovereign_sdk" and stmt.level == 0:
            for alias in stmt.names:
                if alias.name == module:
                    return alias.asname or alias.name
    return None


def _merge_nested(items: list[tuple[FunctionSlice, list[Component]]], emit: Emitter | None
                  ) -> list[tuple[FunctionSlice, list[Component]]]:
    """A selected function nested in another selected one: the outer one takes it (no overlapping stitches)."""
    out: list[tuple[FunctionSlice, list[Component]]] = []
    for sl, comps in sorted(items, key=lambda item: (item[0].start, -item[0].end)):
        outer = next((o for o in out if o[0].start <= sl.start and sl.end <= o[0].end), None)
        if outer is None:
            out.append((sl, list(comps)))
            continue
        outer[1].extend(c for c in comps if all(c.id != k.id for k in outer[1]))
        _emit(emit, f"{sl.file}:{sl.qualname} is nested in {outer[0].qualname}; migrated with it", "info",
              file=sl.file, function=sl.qualname)
    return out


# ---------- one function ----------

def _process(job: _Job, emit: Emitter | None) -> _Outcome:
    sl, ctx = job.slice, job.ctx
    where = f"{sl.file}:{sl.qualname}"
    base = {"file": sl.file, "function": sl.qualname}

    reasons = rules.one_to_one(job.node, ctx.env, ctx.adapters, job.usage_lines)
    if not reasons:
        _emit(emit, f"{where}: {ONE_TO_ONE} (no AI call)", "info", **base, kind="config")
        return _Outcome(job, RewriteRecord(file=sl.file, function=sl.qualname, kind="config", accepted=True,
                                           analysis=ONE_TO_ONE),
                        rewrite=FunctionRewrite(analysis=ONE_TO_ONE, new_source=sl.source))
    _emit(emit, f"{where}: needs the AI ({reasons[0]}{' …' if len(reasons) > 1 else ''})", "info",
          **base, reasons=reasons)

    if "<REDACTED:" in redact(sl.source):
        reason = "the function holds a secret literal; a human must move it to the environment first"
        _emit(emit, f"{where}: kept the original: {reason}", "error", **base)
        return _rejected(job, reason, analysis="", unsupported=[])

    tmpl = _template()
    system = str(tmpl.module.system(adapter_interface=_adapter_interface()))
    components = _components_json(job)
    context = _context_after_swap(job)
    user = str(tmpl.module.user(file=sl.file, qualname=sl.qualname, components=components, context=context,
                                source=_data(sl.source), previous=None))

    try:
        answer = llm.structured(system=system, user=user, schema=FunctionRewrite,
                                label=f"delock:{sl.file}:{sl.qualname}", emit=emit, stage="delock")
        candidate, violations = _judge(job, answer.new_source, emit)
        if violations:
            _emit(emit, f"{where}: rejected by the rules check; retrying once", "warning", **base,
                  violations=violations)
            retry_user = str(tmpl.module.user(
                file=sl.file, qualname=sl.qualname, components=components, context=context,
                source=_data(sl.source), previous={"violations": violations, "source": _data(candidate)}))
            answer = llm.structured(system=system, user=retry_user, schema=FunctionRewrite,
                                    label=f"delock-retry:{sl.file}:{sl.qualname}", emit=emit, stage="delock")
            candidate, violations = _judge(job, answer.new_source, emit)
    except (llm.LLMUnavailable, llm.CacheMiss):
        raise                                            # stop the stage, loudly (docs/15 §4.4)
    except llm.LLMError as e:
        reason = f"the model gave no usable answer: {e}"
        _emit(emit, f"{where}: kept the original: {reason}", "error", **base)
        return _rejected(job, reason, analysis="", unsupported=[])

    if violations:
        reason = "; ".join(violations)
        _emit(emit, f"{where}: kept the original, needs a human (rules check failed twice)", "error",
              **base, violations=violations)
        return _rejected(job, reason, analysis=answer.analysis, unsupported=answer.unsupported)

    changed = slicer.normalize_source(candidate) != slicer.normalize_source(sl.source)
    if changed:
        _emit(emit, f"{where}: rewritten by AI ✅", "success", **base, kind="ai")
    else:
        _emit(emit, f"{where}: AI: no change needed", "info", **base, kind="ai")
    findings = [_finding(job, item[:80], "amber", item) for item in answer.unsupported]
    for item in answer.unsupported:
        _emit(emit, f"{where}: unsupported → 🟠 {item}", "warning", **base, unsupported=item)
    record = RewriteRecord(file=sl.file, function=sl.qualname, kind="ai", accepted=True,
                           analysis=answer.analysis, unsupported=list(answer.unsupported))
    rewrite = FunctionRewrite(analysis=answer.analysis, new_source=candidate, unsupported=list(answer.unsupported))
    return _Outcome(job, record, new_source=candidate if changed else None, rewrite=rewrite, findings=findings)


def _judge(job: _Job, raw: str, emit: Emitter | None) -> tuple[str, list[str]]:
    """Deterministic cleanup, then the rules check. Returns (candidate source, violations)."""
    ctx, sl = job.ctx, job.slice
    candidate = rules.clean_source(raw)
    decorators = {ast.unparse(d) for d in job.node.decorator_list}
    candidate, notes = rules.strip_known(candidate, ctx.imports, decorators)
    if notes:
        _emit(emit, f"{sl.file}:{sl.qualname}: cleanup before the rules check", "info",
              file=sl.file, function=sl.qualname, cleanup=notes)
    violations = rules.check_function(sl, candidate, ctx.allowed, ctx.globals,
                                      adapter_types=ctx.env, adapters=ctx.adapters)
    return candidate, violations


def _rejected(job: _Job, reason: str, *, analysis: str, unsupported: list[str]) -> _Outcome:
    sl = job.slice
    findings = [_finding(job, "needs a human", "red",
                         f"{sl.qualname} in {sl.file} was not migrated: {_short(reason, 300)}")]
    findings += [_finding(job, item[:80], "red", item) for item in unsupported]
    record = RewriteRecord(file=sl.file, function=sl.qualname, kind="ai", accepted=False, analysis=analysis,
                           unsupported=list(unsupported), rejected_reason=reason)
    return _Outcome(job, record, findings=findings)


def _finding(job: _Job, feature: str, light: Light, what: str) -> Finding:
    sl = job.slice
    first = sl.source.split("\n", 1)[0].strip()
    return Finding(analysis="from de-lock", component_id=job.comps[0].id, feature=feature, light=light,
                   what_changes=what,
                   evidence=[CodeRef(file=sl.file, line=sl.start, symbol=sl.qualname, operation="delock",
                                     snippet=first)])


# ---------- prompt pieces ----------

@lru_cache(maxsize=4)
def _environment(prompts_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(prompts_dir), undefined=jinja2.StrictUndefined,
                              autoescape=False, keep_trailing_newline=False)


def _template() -> jinja2.Template:
    return _environment(get_settings().prompts_dir).get_template("delock.md")


def _adapter_interface() -> str:
    path = get_settings().prompts_dir / "adapter_interface.md"
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").strip()


def _data(text: str) -> str:
    """Code goes between ``<<<NAME`` / ``NAME>>>`` markers (S8): a marker inside the data can't close it."""
    for name in MARKERS:
        text = text.replace(f"<<<{name}", f"<<< {name}").replace(f"{name}>>>", f"{name} >>>")
    return text


def _components_json(job: _Job) -> str:
    """The function's components from the PAM, usages filtered to this function, no empty fields."""
    sl, rel = job.slice, job.slice.file
    out: list[dict[str, Any]] = []
    for comp in job.comps:
        data = comp.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        data["usages"] = [u.model_dump(mode="json", exclude_none=True) for u in comp.usages
                          if slicer.normalize_path(u.file) == rel and sl.start <= u.line <= sl.end]
        out.append({k: v for k, v in data.items() if v not in ([], {}, "")})
    return _data(json.dumps(out, indent=2, ensure_ascii=False))


def _context_after_swap(job: _Job) -> str:
    """The module-level lines the function reads, as they will read after the client swap."""
    ctx, sl = job.ctx, job.slice
    context = sl.context
    if not context:
        return "(none)"
    try:
        swapped, swaps, _ = swap_clients(context, ctx.adapters)
        tree = ast.parse(swapped)
    except SyntaxError:
        return _data(context)
    # imports the swapped lines no longer need (typically `import boto3`) would only mislead the model
    reads = slicer.names_read(tree) | set(sl.module_names)
    drop = {n for stmt in tree.body if isinstance(stmt, (ast.Import, ast.ImportFrom))
            and not (slicer.bound_by(stmt) & reads)
            for n in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1)}
    kept = [ln for i, ln in enumerate(swapped.split("\n"), 1) if i not in drop]
    used = sorted({s["module"] for s in swaps})
    header = [_adapter_import_line(ctx.adapters, used)] if used else []
    return _data("\n".join([*header, *kept]))


# ---------- 5. client swap ----------

def swap_clients(text: str, adapters: dict[str, str]) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Replace boto3 client factories by the adapters in play. Returns (text, swaps, residual descriptions).

    Edits are made by AST position (UTF-8 aware), bottom-up, so formatting elsewhere is untouched.
    """
    tree = ast.parse(text.removeprefix(BOM))
    lines = slicer.split_lines(text)
    bom = 1 if text.startswith(BOM) else 0
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    name_of = {module: name for name, module in adapters.items()}

    def offset(lineno: int, col: int) -> int:
        line = lines[lineno - 1][bom:] if lineno == 1 else lines[lineno - 1]
        extra = bom if lineno == 1 else 0
        return starts[lineno - 1] + extra + len(line.encode("utf-8")[:col].decode("utf-8", errors="ignore"))

    edits: list[tuple[int, int, str]] = []
    swaps: list[dict[str, Any]] = []
    residual: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = rules.dotted(node.func)
        svc = rules.boto3_service(node)
        if svc in SWAPS and SWAPS[svc][0] in name_of:
            module, factory, _ = SWAPS[svc]
            replacement = f"{name_of[module]}.{factory}()"
            dropped = [k.arg for k in node.keywords if k.arg and k.arg != "service_name"]
            dropped += ["positional arguments"] if len(node.args) > 1 else []
            edits.append((offset(node.lineno, node.col_offset),
                          offset(node.end_lineno or node.lineno, node.end_col_offset or 0), replacement))
            swaps.append({"line": node.lineno, "from": ast.unparse(node), "to": replacement, "module": module,
                          "dropped": dropped})
        elif chain in rules.CLIENT_FACTORIES:
            residual.append(f"line {node.lineno}: {_short(ast.unparse(node), 80)}")
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    swaps.sort(key=lambda s: s["line"])
    return text, swaps, residual


def _adapter_import_line(adapters: dict[str, str], modules: list[str]) -> str:
    name_of = {module: name for name, module in adapters.items()}
    plain = [m for m in modules if name_of[m] == m]
    parts = [f"from sovereign_sdk import {', '.join(plain)}"] if plain else []
    parts += [f"from sovereign_sdk import {m} as {name_of[m]}" for m in modules if name_of[m] != m]
    return "\n".join(parts)


# ---------- 6. imports ----------

def fix_imports(text: str, adapters: dict[str, str]) -> tuple[str, list[str]]:
    """Add the adapter/helper imports the module now needs; drop ``import boto3`` if nothing uses it.

    Returns (text, notes). Idempotent: an import that is already there is never added twice.
    """
    newline = slicer.newline_style(text)
    notes: list[str] = []
    tree = ast.parse(text.removeprefix(BOM))
    reads = slicer.names_read(tree)
    bound = slicer.module_bound_names(tree)

    # drop a bare top-level `import boto3` that nothing reads any more
    if "boto3" not in reads:
        for stmt in tree.body:
            if (isinstance(stmt, ast.Import) and len(stmt.names) == 1 and stmt.names[0].name == "boto3"
                    and stmt.names[0].asname is None):
                lines = slicer.split_lines(text)
                text = "".join(lines[: stmt.lineno - 1] + lines[(stmt.end_lineno or stmt.lineno):])
                notes.append("removed `import boto3` (no longer used)")
                tree = ast.parse(text.removeprefix(BOM))
                bound = slicer.module_bound_names(tree)
                break

    needed_modules = sorted(m for name, m in adapters.items() if name in reads and name not in bound)
    new_lines: list[str] = []
    if needed_modules:
        new_lines.extend(_adapter_import_line(adapters, needed_modules).split("\n"))
    for name, line in HELPER_IMPORTS.items():
        if name in reads and name not in bound:
            new_lines.append(line)
    if not new_lines:
        return text, notes

    lines = slicer.split_lines(text)
    after = _import_anchor(tree, lines)
    block = "".join(f"{ln}{newline}" for ln in new_lines)
    if after == 0:
        text = block + "".join(lines) if not text.startswith(BOM) else BOM + block + text[1:]
    else:
        if lines[after - 1] and not lines[after - 1].endswith(("\n", "\r")):
            lines[after - 1] += newline                     # last line had no newline at EOF
        text = "".join(lines[:after]) + block + "".join(lines[after:])
    notes.extend(f"added `{ln}`" for ln in new_lines)
    return text, notes


def _import_anchor(tree: ast.Module, lines: list[str]) -> int:
    """Line after which new imports go: the end of the leading import block (after the docstring), or after
    the leading comments (shebang, coding cookie) when the module has neither."""
    anchor = 0
    while anchor < len(lines) and lines[anchor].lstrip(BOM).startswith("#"):
        anchor += 1
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        anchor = body.pop(0).end_lineno or 0
    for stmt in body:
        if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
            break
        anchor = stmt.end_lineno or stmt.lineno
    return anchor


# ---------- 4–7, 9: one file ----------

def _apply_file(app_dir: Path, ctx: _FileCtx, outcomes: list[_Outcome], diffs_dir: Path, out_dir: Path,
                emit: Emitter | None) -> tuple[RewriteRecord | None, bool]:
    info, rel = ctx.info, ctx.info.rel
    original = info.text
    changed = sorted((o for o in outcomes if o.new_source is not None), key=lambda o: o.job.slice.start,
                     reverse=True)

    # 4. stitch bottom-up
    stitched = original
    for o in changed:
        stitched = slicer.replace_function(stitched, o.job.slice, o.new_source or "", info.newline)

    # 5. client swap + 6. imports
    try:
        swapped, swaps, residual = swap_clients(stitched, ctx.adapters)
        final, import_notes = fix_imports(swapped, ctx.adapters)
    except SyntaxError as e:                                  # stitching produced garbage: a bug here
        _revert(rel, outcomes, f"stitching broke the file: {e.msg} (line {e.lineno})", emit)
        return None, False
    for item in residual:
        _emit(emit, f"{rel}: {item} not replaced (no adapter) → residual lock-in 🔴", "warning", file=rel)

    if final == original:
        return None, False

    # 7. compile check on the stitched file
    try:
        rules.check_module(final, rel)
    except RuleViolation as e:
        _revert(rel, outcomes, str(e), emit)
        return None, False

    path = rules.check_path(app_dir, rel)
    atomic_write_bytes(path, final.encode("utf-8"))

    # 9. per-function diffs against the original file (exact line numbers, one function at a time)
    for o in changed:
        single = slicer.replace_function(original, o.job.slice, o.new_source or "", info.newline)
        o.record.diff_path, o.record.changed_lines = _write_diff(original, single, rel, o.job.slice.qualname,
                                                                 diffs_dir, out_dir)

    for s in swaps:
        light = "🟢 config change" if s["module"] == "storage" else "🟠"
        extra = f" (dropped {', '.join(s['dropped'])}: the adapters read env)" if s["dropped"] else ""
        _emit(emit, f"{rel}:{s['line']}: {s['from']} → {s['to']} {light}{extra}", "success", file=rel, **s)
    for note in import_notes:
        _emit(emit, f"{rel}: {note}", "info", file=rel)

    module_record = None
    if swapped != stitched or final != swapped:
        diff_path, lines = _write_diff(stitched, final, rel, "module", diffs_dir, out_dir)
        analysis = "; ".join([f"{s['from']} → {s['to']}" for s in swaps] + import_notes)
        module_record = RewriteRecord(file=rel, function=MODULE, kind="config", accepted=True,
                                      changed_lines=lines, analysis=analysis, diff_path=diff_path)
    return module_record, True


def _revert(rel: str, outcomes: list[_Outcome], reason: str, emit: Emitter | None) -> None:
    _emit(emit, f"{rel}: the stitched file failed the compile check; kept the original file ({reason})", "error",
          file=rel)
    for o in outcomes:
        if o.new_source is not None:
            o.record.accepted = False
            o.record.rejected_reason = f"stitched file failed the compile check: {reason}"
            o.new_source = None
            o.rewrite = None
            o.findings = [f for f in o.findings if f.light != "amber"] + [_finding(
                o.job, "needs a human", "red",
                f"{o.job.slice.qualname} in {rel} was not migrated: {_short(reason, 300)}")]


def _write_diff(before: str, after: str, rel: str, function: str, diffs_dir: Path, out_dir: Path
                ) -> tuple[str, int]:
    a = _diff_lines(before)
    b = _diff_lines(after)
    diff = list(difflib.unified_diff(a, b, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
    added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
    path = Path(diffs_dir) / f"{rel.replace('/', '_')}__{function}.diff"
    atomic_write_text(path, "".join(diff))
    try:
        shown = Path(os.path.relpath(path, out_dir)).as_posix()
    except ValueError:                                         # another drive
        shown = path.as_posix()
    return shown, max(added, removed)


def _diff_lines(text: str) -> list[str]:
    lines = [ln.rstrip("\r\n") + "\n" for ln in slicer.split_lines(text.removeprefix(BOM))]
    return lines


# ---------- 8. residual check ----------

def _residual_check(app_dir: Path, emit: Emitter | None) -> None:
    try:
        from boomerang.scanner.static import scan_static
        residual = scan_static(app_dir)
    except NotBuilt:
        _emit(emit, "residual check skipped: scanner.static.scan_static is not built yet (owner B)", "warning")
        return
    except Exception as e:  # noqa: BLE001 - B's code; a warning-only check must never kill the stage
        _emit(emit, f"residual check failed: {type(e).__name__}: {e}", "warning")
        return
    hits = [(comp, u) for comp in residual.components if comp.provider in ("aws.dynamodb", "aws.sqs")
            for u in comp.usages]
    for comp, u in hits:
        _emit(emit, f"residual lock-in: {u.file}:{u.line} still uses {comp.provider} ({u.operation})", "warning",
              file=u.file, line=u.line, component_id=comp.id)
    if not hits:
        _emit(emit, "residual check: no DynamoDB or SQS calls left in the app", "success")


# ---------- outputs ----------

def _write_outputs(out_dir: Path, records: list[RewriteRecord], assessment: Assessment,
                   findings: list[Finding]) -> None:
    out_dir = Path(out_dir)
    atomic_write_text(out_dir / "delock.json",
                      json.dumps([r.model_dump(mode="json") for r in records], indent=2, ensure_ascii=False) + "\n")
    final = final_assessment(assessment, findings)
    atomic_write_text(out_dir / "assessment.final.json", final.model_dump_json(indent=2) + "\n")


def _summary(records: list[RewriteRecord], changed_files: int, emit: Emitter | None) -> None:
    functions = [r for r in records if r.function != MODULE]
    ai = sum(r.kind == "ai" and r.accepted and r.changed_lines > 0 for r in functions)
    ai_same = sum(r.kind == "ai" and r.accepted and r.changed_lines == 0 for r in functions)
    code = sum(r.kind == "config" for r in functions)
    kept = sum(not r.accepted for r in functions)
    parts = [f"{ai} rewritten by AI", f"{ai_same} unchanged by AI", f"{code} 1:1 (checked by code)",
             f"{kept} kept for a human", f"{changed_files} file(s) changed"]
    _emit(emit, "de-lock done: " + " · ".join(parts), "warning" if kept else "success",
          rewritten=ai, unchanged=ai_same, one_to_one=code, rejected=kept, files=changed_files)


# ---------- shared with the fixer (C5) ----------

FileContext = _FileCtx


def file_context(app_dir: Path, rel: str, emit: Emitter | None = None) -> _FileCtx | None:
    """The rules-check context of an already de-locked file: only the adapters it imports are in play."""
    try:
        tree = slicer.module_info(Path(app_dir), rel).tree
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        _emit(emit, f"{rel}: can't parse ({type(e).__name__})", "warning", file=rel)
        return None
    in_play = {m: m for m in sorted(ADAPTER_OF_KIND.values()) if _existing_alias(tree, m)}
    return _file_ctx(Path(app_dir), rel, in_play, emit)


def delimit(text: str, extra_markers: tuple[str, ...] = ()) -> str:
    """``_data`` for other prompts: neutralises this module's markers and ``extra_markers`` inside data."""
    text = _data(text)
    for name in extra_markers:
        text = text.replace(f"<<<{name}", f"<<< {name}").replace(f"{name}>>>", f"{name} >>>")
    return text


def write_diff(before: str, after: str, rel: str, name: str, diffs_dir: Path, out_dir: Path) -> tuple[str, int]:
    """``diffs/<file>__<name>.diff``; returns (path relative to ``out_dir``, changed lines)."""
    return _write_diff(before, after, rel, name, diffs_dir, out_dir)


def prompt_environment() -> jinja2.Environment:
    return _environment(get_settings().prompts_dir)


def adapter_interface() -> str:
    return _adapter_interface()


# ---------- small helpers ----------

def _emit(emit: Emitter | None, message: str, level: str = "info", **data: Any) -> None:
    if emit is not None:
        emit.emit("delock", message, level, **data)  # type: ignore[arg-type]


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
