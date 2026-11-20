"""Fixer: failing original tests → one AI fix per function (docs/03 §3.9). Owner C, task C5.

Spec: docs/15 §6. Called by the pipeline's verify loop (L11) with the failing ``TestReport``.

1. **Frames** from each failure's traceback: pytest's ``app/db.py:42: in get_order`` / ``app/db.py:42: KeyError``
   and the classic ``File "…/app/db.py", line 42, in get_order``; Windows paths, made relative to ``app_dir``.
2. **Target**: the innermost frame in the app that is not under ``tests/`` or ``sovereign_sdk/``.
   - A traceback ending inside ``sovereign_sdk/`` still targets the app frame that called it (an
     ``UnsupportedFeature`` or a wrong argument is the app's misuse) with a warning that it may be an
     adapter bug (owners B/A). No app frame calling into it → error, skipped.
   - No app frame at all (a wrong value: ``TestClient`` re-raises server errors, so an assertion means the
     app answered wrongly) → heuristic: migrated functions whose name shares a word with the test name,
     AI-rewritten ones first, at most 2.
3. **One AI call per function** (failures merged), at most ``MAX_FUNCTIONS`` per attempt. The prompt holds the
   original AWS function (from ``original/``), the migrated one, the failures through ``llm.stable_text`` and,
   from attempt 2, what the previous attempt tried and how it ended, so the cache (temperature 0) can't hand
   back the same wrong fix.
4. **Never trust the model**: ``file``/``function`` must be the target; then ``rules.check_function`` (one retry
   with the violations listed), stitch, imports, ``check_module``, ``rules.check_path`` (never ``tests/`` or
   ``sovereign_sdk/``), diff ``diffs/<file>__<function>.fix<n>.diff``, ``RewriteRecord(kind="fix")``.

``LLMUnavailable`` and ``CacheMiss`` stop the stage, as in the de-lock: a replay miss must fail loudly.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from boomerang import delock, llm, rules, slicer
from boomerang.events import Emitter
from boomerang.fsutil import atomic_write_bytes
from boomerang.models import AppModel, FixResult, RewriteRecord, TestFailure, TestReport
from boomerang.redact import redact
from boomerang.rules import RuleViolation
from boomerang.slicer import FunctionNode, FunctionSlice

MAX_FUNCTIONS = 3                    # AI calls per attempt; the rest waits for the next round
MAX_HEURISTIC = 2
MAX_FAILURES_SHOWN = 3               # per function; more are listed by name only
FAILURES_CHARS = 3000                # the whole TEST_FAILURES block
MESSAGE_CHARS = 400
MARKERS = ("ORIGINAL_FUNCTION", "MIGRATED_FUNCTION", "MODULE_CONTEXT", "TEST_FAILURES", "PREVIOUS_ANALYSIS",
           "PREVIOUS_ANSWER")
STOP_WORDS = {"test", "tests", "the", "and", "for", "with", "from", "to", "of", "by", "a", "an", "in", "on",
              "is", "it", "when", "then", "should", "can", "not", "no"}

# pytest: "app/db.py:42: in get_order" (short), "app/db.py:42: KeyError" / "app/db.py:42: " (long)
_PYTEST = re.compile(r"^(?P<path>(?:[A-Za-z]:)?[\w.\-/\\~+@ ]*?\.py):(?P<line>\d+):(?:[ \t]+in[ \t]+(?P<func>[\w.<>]+))?")
# classic / --tb=native: '  File "C:\\x\\app\\db.py", line 42, in get_order'
_CLASSIC = re.compile(r'^\s*File "(?P<path>[^"\n]+)", line (?P<line>\d+)(?:, in (?P<func>[\w.<>]+))?')
_PYTEST_NOISE = re.compile(r"^(?:E|>)\s")
_THIRD_PARTY = {"site-packages", "dist-packages"}


# ---------- public API ----------

def frames(traceback: str) -> list[tuple[str, int, str]]:
    """(file, line, function), outermost → innermost; pytest and classic formats, Windows paths.

    ``function`` is ``""`` when the traceback line doesn't say (pytest's long format); the fixer maps
    file + line to the function with the slicer anyway.
    """
    out: list[tuple[str, int, str]] = []
    for raw in traceback.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _CLASSIC.match(raw)
        if m is None and not _PYTEST_NOISE.match(raw):
            m = _PYTEST.match(raw)
        if m is None:
            continue
        path = m.group("path").strip()
        if not path or path.startswith("<"):
            continue
        out.append((path, int(m.group("line")), m.group("func") or ""))
    return out


def fix(app_dir: Path, original_dir: Path, pam: AppModel, report: TestReport, attempt: int,
        rewrites: list[RewriteRecord], diffs_dir: Path, emit: Emitter) -> list[RewriteRecord]:
    """``[]`` = nothing fixed. Never writes under tests/ or sovereign_sdk/."""
    app_dir, original_dir, diffs_dir = Path(app_dir).resolve(), Path(original_dir), Path(diffs_dir)
    failures = sorted(report.failures, key=lambda f: (f.test, f.message))
    if not failures:
        _emit(emit, f"attempt {attempt}: the test report lists no failures to work from "
                    f"({report.failed} failed, {report.errors} errors)", "warning", attempt=attempt)
        return []

    targets = _targets(app_dir, failures, rewrites, emit)
    if not targets:
        _emit(emit, f"attempt {attempt}: no app function to fix in {len(failures)} failure(s)", "warning",
              attempt=attempt)
        return []
    if len(targets) > MAX_FUNCTIONS:
        later = [f"{t.file}:{t.qualname}" for t in targets[MAX_FUNCTIONS:]]
        _emit(emit, f"attempt {attempt}: {len(targets)} functions fail; fixing the first {MAX_FUNCTIONS} now, "
                    f"the rest next round ({', '.join(later)})", "warning", attempt=attempt, later=later)
        targets = targets[:MAX_FUNCTIONS]

    records: list[RewriteRecord] = []
    for target in targets:
        record = _fix_one(app_dir, original_dir, target, attempt, rewrites, diffs_dir, emit)
        if record is not None:
            records.append(record)
    fixed = sum(r.accepted for r in records)
    _emit(emit, f"attempt {attempt}: {fixed} of {len(records)} function(s) fixed", "success" if fixed else "warning",
          attempt=attempt, fixed=fixed, tried=len(records))
    return records


# ---------- targets ----------

@dataclass
class _Target:
    file: str
    qualname: str
    how: str                                       # "traceback" | "heuristic"
    failures: list[TestFailure] = field(default_factory=list)


def _targets(app_dir: Path, failures: list[TestFailure], rewrites: list[RewriteRecord],
             emit: Emitter | None) -> list[_Target]:
    found: dict[tuple[str, str], _Target] = {}

    def add(rel: str, qualname: str, how: str, failure: TestFailure) -> None:
        t = found.setdefault((rel, qualname), _Target(rel, qualname, how))
        if failure not in t.failures:
            t.failures.append(failure)

    for failure in failures:
        located = _locate(failure.traceback, app_dir)
        app_frames = [i for i, (_, _, kind) in enumerate(located) if kind == "app"]
        in_adapter = any(kind == "adapter" for _, _, kind in located)
        if not app_frames:
            if in_adapter:
                _emit(emit, f"{failure.test}: fails inside sovereign_sdk/ with no app code calling it: an adapter "
                            "problem (owners B/A), not something the fixer rewrites", "error", test=failure.test)
                continue
            picks = _heuristic(app_dir, failure.test, rewrites)
            if not picks:
                _emit(emit, f"{failure.test}: no app frame in the traceback and no migrated function matches "
                            "the test name", "warning", test=failure.test)
            for sl in picks:
                _emit(emit, f"{failure.test}: heuristic target {sl.file}:{sl.qualname} (no app frame in the "
                            "traceback; the name matches)", "info", test=failure.test, file=sl.file,
                      function=sl.qualname, how="heuristic")
                add(sl.file, sl.qualname, "heuristic", failure)
            continue

        idx = app_frames[-1]
        rel, line, _ = located[idx]
        adapter_below = any(kind == "adapter" for _, _, kind in located[idx + 1:])
        sl = slicer.function_at(app_dir, rel, line)
        if sl is None:
            _emit(emit, f"{failure.test}: fails in module-level code at {rel}:{line}; the fixer only rewrites "
                        "functions", "error", test=failure.test, file=rel, line=line)
            continue
        if adapter_below:
            _emit(emit, f"{failure.test}: the failure is inside the adapter, called from {rel}:{sl.qualname}; "
                        "fixing the call. If the call looks right, it's an adapter bug (owners B/A)", "warning",
                  test=failure.test, file=rel, function=sl.qualname)
        add(rel, sl.qualname, "traceback", failure)
    return list(found.values())


def _locate(traceback: str, app_dir: Path) -> list[tuple[str, int, str]]:
    """Frames in the app as (rel, line, kind) with kind "app" | "test" | "adapter", outermost → innermost.

    A ``sovereign_sdk/`` frame counts as the adapter even when the path isn't in this app folder (the tests
    may have imported another copy); other frames outside the app (stdlib, site-packages) are dropped.
    """
    out: list[tuple[str, int, str]] = []
    for path, line, _ in frames(traceback):
        rel = _app_relative(path, app_dir)
        if rel is not None:
            out.append((rel, line, _kind(rel)))
        elif _is_adapter_path(path):
            out.append((path.replace("\\", "/"), line, "adapter"))
    return out


def _is_adapter_path(path: str) -> bool:
    parts = [p.lower() for p in PurePosixPath(path.replace("\\", "/")).parts]
    return "sovereign_sdk" in parts[:-1] and not _THIRD_PARTY.intersection(parts)


def _app_relative(path: str, app_dir: Path) -> str | None:
    """A traceback path → POSIX path relative to ``app_dir``, if the file is in the app (else None)."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if _THIRD_PARTY.intersection(p.lower() for p in parts):
        return None
    rel = slicer.normalize_path(path, app_dir)
    if rel is not None and _inside(app_dir, rel):
        return rel
    # another cwd or a container path (/app/app/db.py): the longest suffix that exists in the app
    clean = [p for p in parts if p not in ("/", "\\") and not re.fullmatch(r"[A-Za-z]:[\\/]?", p)]
    for i in range(1, len(clean)):
        candidate = "/".join(clean[i:])
        if _inside(app_dir, candidate):
            return candidate
    return None


def _inside(app_dir: Path, rel: str) -> bool:
    if rel.startswith("../") or rel == "..":
        return False
    target = (app_dir / rel).resolve()
    return target.is_file() and target.is_relative_to(app_dir)


def _kind(rel: str) -> str:
    parts = [p.lower() for p in PurePosixPath(rel).parts]
    if "sovereign_sdk" in parts[:-1]:
        return "adapter"
    if rules.PROTECTED_DIRS.intersection(parts[:-1]) or rules._is_test_file(parts[-1]):
        return "test"
    return "app"


def _tokens(name: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w and w not in STOP_WORDS}


def _heuristic(app_dir: Path, test: str, rewrites: list[RewriteRecord]) -> list[FunctionSlice]:
    """Migrated functions sharing a word with the test name; AI-rewritten and fixed ones first, max 2."""
    name = re.sub(r"\[.*\]$", "", test.split("::", 1)[-1])
    wanted = _tokens(name)
    scored: list[tuple[int, int, int, RewriteRecord]] = []
    seen: set[tuple[str, str]] = set()
    for i, r in enumerate(rewrites):
        key = (r.file, r.function)
        if r.function == delock.MODULE or not r.accepted or key in seen:
            continue
        seen.add(key)
        shared = len(wanted & _tokens(r.function.rsplit(".", 1)[-1]))
        if shared:
            tier = 0 if (r.kind == "fix" or (r.kind == "ai" and r.changed_lines > 0)) else 1
            scored.append((tier, -shared, i, r))
    out: list[FunctionSlice] = []
    for _, _, _, r in sorted(scored, key=lambda item: item[:3]):
        found = _find(app_dir, r.file, r.function)
        if found is not None and _kind(r.file) == "app":
            out.append(found[1])
        if len(out) == MAX_HEURISTIC:
            break
    return out


def _find(base: Path, rel: str, qualname: str) -> tuple[FunctionNode, FunctionSlice] | None:
    """The function ``qualname`` in ``base/rel`` as it is on disk now (line numbers move as fixes land)."""
    try:
        info = slicer.module_info(base, rel)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    hit = next(((n, q) for n, q in info.functions if q == qualname), None)
    return (hit[0], slicer.make_slice(info, *hit)) if hit else None


# ---------- one function ----------

def _fix_one(app_dir: Path, original_dir: Path, target: _Target, attempt: int, rewrites: list[RewriteRecord],
             diffs_dir: Path, emit: Emitter | None) -> RewriteRecord | None:
    rel, qualname = target.file, target.qualname
    where = f"{rel}:{qualname}"
    base = {"file": rel, "function": qualname, "attempt": attempt}
    try:
        rules.check_path(app_dir, rel)
    except RuleViolation as e:
        _emit(emit, f"{where}: not touched ({e})", "error", **base)
        return None
    found = _find(app_dir, rel, qualname)
    ctx = delock.file_context(app_dir, rel, emit)
    if found is None or ctx is None:
        _emit(emit, f"{where}: can't find the function any more; skipped", "error", **base)
        return None
    node, current = found
    decorators = {ast.unparse(d) for d in node.decorator_list}
    in_original = _find(original_dir, rel, qualname)
    original = in_original[1] if in_original else None
    if "<REDACTED:" in redact(current.source):
        return _rejected(rel, qualname, attempt, "the function holds a secret literal; a human must move it to "
                         "the environment first", "", emit)

    tmpl = delock.prompt_environment().get_template("fix.md")
    system = str(tmpl.module.system(adapter_interface=delock.adapter_interface()))
    previous = _previous(rewrites, rel, qualname, attempt)
    fields = {
        "file": rel, "qualname": qualname,
        "original": _data(original.source) if original else "(not found in the original app)",
        "migrated": _data(current.source),
        "context": _data(current.context) if current.context else "(none)",
        "failures": _data(_failures_text(target.failures, app_dir)),
        "previous": previous,
    }
    allowed = set(ctx.allowed) | ({n for n in original.module_names if n in ctx.globals} if original else set())

    _emit(emit, f"{where}: asking the AI ({len(target.failures)} failing test(s), via {target.how}"
                f"{'; previous attempt ' + str(previous['attempt']) if previous else ''})", "info", **base,
          how=target.how, tests=[f.test for f in target.failures])
    label = f"fix:{attempt}:{rel}:{qualname}"
    try:
        answer = llm.structured(system=system, user=str(tmpl.module.user(**fields, retry=None)), schema=FixResult,
                                label=label, emit=emit, stage="fix")
        candidate, violations = _judge(current, decorators, ctx, allowed, answer, emit)
        if violations:
            _emit(emit, f"{where}: rejected by the rules check; retrying once", "warning", **base,
                  violations=violations)
            retry = {"violations": violations, "source": _data(candidate)}
            answer = llm.structured(system=system, user=str(tmpl.module.user(**fields, retry=retry)),
                                    schema=FixResult, label=f"fix-retry:{attempt}:{rel}:{qualname}", emit=emit,
                                    stage="fix")
            candidate, violations = _judge(current, decorators, ctx, allowed, answer, emit)
    except (llm.LLMUnavailable, llm.CacheMiss):
        raise                                            # stop the stage, loudly (docs/15 §4.4)
    except llm.LLMError as e:
        return _rejected(rel, qualname, attempt, f"the model gave no usable answer: {e}", "", emit)

    if violations:
        return _rejected(rel, qualname, attempt, "; ".join(violations), answer.root_cause_analysis, emit)
    if slicer.normalize_source(candidate) == slicer.normalize_source(current.source):
        return _rejected(rel, qualname, attempt, "the model returned the function unchanged",
                         answer.root_cause_analysis, emit, level="warning")

    # stitch, imports, compile check, write (never tests/ or sovereign_sdk/)
    info = slicer.module_info(app_dir, rel)
    try:
        stitched = slicer.replace_function(info.text, current, candidate, info.newline)
        final, notes = delock.fix_imports(stitched, ctx.adapters)
        rules.check_module(final, rel)
        path = rules.check_path(app_dir, rel)
    except (RuleViolation, SyntaxError, ValueError) as e:
        return _rejected(rel, qualname, attempt, f"the stitched file failed the compile check: {e}",
                         answer.root_cause_analysis, emit)
    atomic_write_bytes(path, final.encode("utf-8"))
    diff_path, lines = delock.write_diff(info.text, final, rel, f"{qualname}.fix{attempt}", diffs_dir,
                                         diffs_dir.parent)
    for note in notes:
        _emit(emit, f"{rel}: {note}", "info", file=rel)
    _emit(emit, f"{where}: fixed by AI 🔧 ({lines} line(s)): {_short(answer.root_cause_analysis, 160)}", "success",
          **base, changed_lines=lines, diff=diff_path)
    return RewriteRecord(file=rel, function=qualname, kind="fix", attempt=attempt, accepted=True,
                         changed_lines=lines, analysis=answer.root_cause_analysis, diff_path=diff_path)


def _judge(current: FunctionSlice, decorators: set[str], ctx: delock.FileContext, allowed: set[str],
           answer: FixResult, emit: Emitter | None) -> tuple[str, list[str]]:
    """The model's file/function must be the target; then the same cleanup and rules as the de-lock."""
    violations: list[str] = []
    if slicer.normalize_path(answer.file or "") != current.file:
        violations.append(f"the answer is for file {answer.file!r}, but the target is {current.file!r}")
    if answer.function not in (current.name, current.qualname):
        violations.append(f"the answer is for function {answer.function!r}, but the target is {current.qualname!r}")
    candidate = rules.clean_source(answer.new_source)
    candidate, notes = rules.strip_known(candidate, ctx.imports, decorators)
    if notes:
        _emit(emit, f"{current.file}:{current.qualname}: cleanup before the rules check", "info",
              file=current.file, function=current.qualname, cleanup=notes)
    violations += rules.check_function(current, candidate, allowed, ctx.globals, adapter_types=ctx.env,
                                       adapters=ctx.adapters)
    return candidate, list(dict.fromkeys(violations))


def _rejected(rel: str, qualname: str, attempt: int, reason: str, analysis: str, emit: Emitter | None,
              level: str = "error") -> RewriteRecord:
    _emit(emit, f"{rel}:{qualname}: fix not applied: {_short(reason, 300)}", level, file=rel, function=qualname,
          attempt=attempt)
    return RewriteRecord(file=rel, function=qualname, kind="fix", attempt=attempt, accepted=False,
                         analysis=analysis, rejected_reason=reason)


# ---------- prompt pieces ----------

def _previous(rewrites: list[RewriteRecord], rel: str, qualname: str, attempt: int) -> dict[str, Any] | None:
    """The latest earlier fix of this function: what it tried and how it ended."""
    earlier = [r for r in rewrites if r.kind == "fix" and r.file == rel and r.function == qualname
               and r.attempt < attempt]
    if not earlier:
        return None
    last = max(earlier, key=lambda r: r.attempt)
    if last.accepted:
        outcome = "was applied, but the tests still fail"
    else:
        outcome = f"was rejected ({_short(last.rejected_reason or 'no reason given', 300)})"
    return {"attempt": last.attempt, "outcome": outcome,
            "analysis": _data(last.analysis.strip() or "(none given)")}


def _failures_text(failures: list[TestFailure], app_dir: Path) -> str:
    """Run-independent (``llm.stable_text``) and bounded: ≤ ``FAILURES_CHARS`` whatever the tracebacks."""
    shown = failures[:MAX_FAILURES_SHOWN]
    per_tb = max(400, (FAILURES_CHARS - 200 * len(shown)) // max(1, len(shown)) - MESSAGE_CHARS)
    blocks: list[str] = []
    for f in shown:
        message = _short(llm.stable_text(f.message, app_dir).strip(), MESSAGE_CHARS)
        tb = llm.stable_text(f.traceback, app_dir).strip()
        tb = tb if len(tb) <= per_tb else "…" + tb[-(per_tb - 1):]
        blocks.append(f"test: {llm.stable_text(f.test, app_dir)}\nmessage: {message}\ntraceback (end):\n{tb}")
    rest = [llm.stable_text(f.test, app_dir) for f in failures[MAX_FAILURES_SHOWN:]]
    if rest:
        blocks.append("also failing: " + ", ".join(rest))
    text = "\n\n".join(blocks)
    return text if len(text) <= FAILURES_CHARS else text[: FAILURES_CHARS - 1] + "…"


def _data(text: str) -> str:
    return delock.delimit(text, MARKERS)


# ---------- small helpers ----------

def _emit(emit: Emitter | None, message: str, level: str = "info", **data: Any) -> None:
    if emit is not None:
        emit.emit("fix", message, level, **data)  # type: ignore[arg-type]


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
