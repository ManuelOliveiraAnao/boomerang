"""`report.md` for a run (docs/15 §5.3). Owner C, task C4.

Rendered with Jinja2 from ``RunSummary`` (+ ``pam.json`` for the app name and ``cost.json`` if D wrote one).
After rendering, ``redact.find_secrets`` must find nothing; if it does, the text is redacted before it is
written and an error event says so. The footer states what the report does not claim.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2

from boomerang.fsutil import atomic_write_text
from boomerang.models import AppModel, Assessment, CodeRef, RewriteRecord, TestReport
from boomerang.redact import find_secrets, redact

if TYPE_CHECKING:
    from boomerang.pipeline import RunContext

LIGHT_ICONS = {"green": "🟢", "amber": "🟠", "red": "🔴"}
STATUS_ICONS = {"passed": "✅", "failed": "❌", "error": "❌", "running": "⏳", "awaiting_approval": "⏸️",
                "not_run": "·"}
MODULE = "<module>"                      # delock.MODULE (not imported: report must import without the engine)


# ---------- small helpers, also used by the pipeline's events ----------

def light_numbers(assessment: Assessment) -> dict[str, int]:
    counts = Counter(f.light for f in assessment.findings)
    return {light: counts.get(light, 0) for light in LIGHT_ICONS}


def light_counts(assessment: Assessment) -> str:
    """``1 🟢 · 4 🟠 · 3 🔴``"""
    return " · ".join(f"{n} {LIGHT_ICONS[light]}" for light, n in light_numbers(assessment).items())


def score(value: int | None) -> str:
    return "–" if value is None else str(value)


def tests_ratio(report: TestReport | None) -> str:
    return "–" if report is None else f"{report.passed}/{report.total}"


def change_label(r: RewriteRecord) -> str:
    if not r.accepted:
        return "❌ kept the original: needs a human"
    if r.kind == "fix":
        return f"🔧 fix (attempt {r.attempt})"
    if r.kind == "ai":
        return "🤖 rewritten by AI" if r.changed_lines else "🤖 AI: no change needed"
    if r.function == MODULE:
        return "⚙️ config change (clients, imports)"
    return "✔️ 1:1 mapping, checked by code"


def rewrite_counts(rewrites: list[RewriteRecord]) -> str:
    functions = [r for r in rewrites if r.function != MODULE]
    parts = [
        (sum(r.kind == "ai" and r.accepted and r.changed_lines > 0 for r in functions), "rewritten by AI"),
        (sum(r.kind == "ai" and r.accepted and r.changed_lines == 0 for r in functions), "unchanged by AI"),
        (sum(r.kind == "config" for r in functions), "1:1 checked by code"),
        (sum(r.kind == "fix" and r.accepted for r in functions), "fixed after a failing test"),
        (sum(not r.accepted for r in functions), "kept for a human"),
    ]
    return " · ".join(f"{n} {label}" for n, label in parts)


# ---------- filters ----------

def _cell(value: Any, limit: int = 200) -> str:
    """One Markdown table cell: no newlines, escaped pipes, bounded length."""
    text = " ".join(str("" if value is None else value).split())
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _ok(value: bool | None) -> str:
    return {True: "✅", False: "❌", None: "–"}[value]


def _ts(value: float | None) -> str:
    return "–" if value is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _refs(evidence: list[CodeRef]) -> str:
    shown = ", ".join(f"{r.file}:{r.line}" for r in evidence[:3])
    return _cell(shown + (f" (+{len(evidence) - 3})" if len(evidence) > 3 else "")) or "–"


TEMPLATE = """\
# 🪃 Boomerang report: {{ app_name|cell }}

| | |
|---|---|
| Run | `{{ s.run_id }}` |
| Source | `{{ s.source_dir|cell }}` |
| Status | {{ status_icon }} **{{ s.status }}** |
| Started → finished | {{ s.started_at|ts }} → {{ s.finished_at|ts }} |
| AI model | {{ (s.llm or "–")|cell }} |
| Lock-in score | {{ s.lock_in_before|score }} → {{ s.lock_in_after|score }} |
| Original tests | AWS {{ s.baseline|ratio }} → Leaseweb {{ last_tests|ratio }} · unchanged {{ s.tests_unchanged|ok }} |
{% if s.fixtures_used %}

> ⚠️ **Fixtures were used for: {{ s.fixtures_used|join(", ") }}.** Those steps did not really run, so this \
report is not evidence of a migration.
{% endif %}

## 1. What breaks

{% if a %}
**{{ counts }}** · overall {{ a.overall|light }} · {{ a.summary|cell(500) }}

| Component | Feature | Light | What changes | Evidence |
|---|---|---|---|---|
{% for f in a.findings %}
| {{ f.component_id|cell }} | {{ f.feature|cell }} | {{ f.light|light }} | {{ f.what_changes|cell }} | \
{{ f.evidence|refs }} |
{% endfor %}

**🔴 Not migrated: needs a human**

{% for f in reds %}
- {{ f.component_id|cell }} · {{ f.feature|cell }}: {{ f.what_changes|cell(400) }}
{% else %}
- none
{% endfor %}

Red items leave the "after" lock-in score because they were not migrated, not because they were solved.
{% else %}
No assessment: the plan did not finish.
{% endif %}

## 2. Code changes

{% if rewrites %}
{{ rewrite_summary }}

| File | Function | Change | Changed lines | Diff |
|---|---|---|---|---|
{% for r in rewrites %}
| `{{ r.file|cell }}` | `{{ r.function|cell }}` | {{ r|change }} | {{ r.changed_lines }} | \
{% if r.diff_path %}[diff](<{{ r.diff_path }}>){% else %}–{% endif %} |
{% endfor %}
{% for r in rewrites if r.unsupported or not r.accepted %}
{% if loop.first %}

Notes:

{% endif %}
{% if not r.accepted %}
- `{{ r.file }}:{{ r.function }}` kept the original: {{ (r.rejected_reason or "rejected")|cell(400) }}
{% endif %}
{% if r.unsupported %}
- `{{ r.file }}:{{ r.function }}` not supported by the adapter: {{ r.unsupported|join("; ")|cell(400) }}
{% endif %}
{% endfor %}
{% else %}
No code changes yet.
{% endif %}

## 3. Verification on the Leaseweb setup (laptop)

{% for v in s.verifications %}
**Attempt {{ loop.index }}** {{ v.ok|ok }}

| Check | Result | Detail | Time |
|---|---|---|---|
{% for c in v.checks %}
| {{ c.name|cell }} | {{ c.ok|ok }} | {{ c.detail|cell }} | {{ "%.1f"|format(c.duration_s) }} s |
{% endfor %}
{% if v.tests %}

Original tests: {{ v.tests|ratio }} passed{% if v.tests.errors %}, {{ v.tests.errors }} error(s){% endif %}.
{% for f in v.tests.failures[:10] %}
{% if loop.first %}

{% endif %}
- `{{ f.test|cell }}`: {{ f.message|cell(300) }}
{% endfor %}
{% endif %}

{% else %}
Not verified.

{% endfor %}
## 4. The app's original tests

| | AWS (before) | Leaseweb (after) |
|---|---|---|
| Original tests passed | {{ s.baseline|ratio }} | {{ last_tests|ratio }} |

Original tests unchanged: {{ s.tests_unchanged|ok }}. The AI never edits tests: every file under `tests/` \
is hashed (sha256, `tests.sha256`) before the migration and compared after it.
{% if s.baseline and not s.baseline.ok %}

⚠️ The original app fails its own tests on AWS, so the migration can't prove more than that.
{% endif %}

## 5. Data

{% for m in s.data_moves %}
**{{ m.direction }}** {{ m.ok|ok }}

| Component | Kind | Source | Target | Checksum | Notes |
|---|---|---|---|---|---|
{% for c in m.counts %}
| {{ c.component_id|cell }} | {{ c.kind }} | {{ c.source_count }} | {{ c.target_count }} | \
{{ c.checksum_match|ok }} | {{ c.notes|join("; ")|cell(300) }} |
{% endfor %}

{% else %}
No data moved.

{% endfor %}
## 6. Round trip back to AWS

{% if s.roundtrip == "not_run" %}
Not run yet: `boomerang roundtrip {{ s.run_id }}`.
{% else %}
{{ roundtrip_icon }} **{{ s.roundtrip }}**. The rollback target is a fresh (emulated) AWS environment, not a \
merge into the old tables.
{% if s.roundtrip_checks %}

| Check | Result | Detail |
|---|---|---|
{% for c in s.roundtrip_checks %}
| {{ c.name|cell }} | {{ c.ok|ok }} | {{ c.detail|cell }} |
{% endfor %}
{% endif %}

The original AWS code's tests after the copy back: {{ s.roundtrip_tests|ratio }}.

**What got lost**

{% for status, items in lost %}
{% for i in items %}
- {{ status_names[status] }} · {{ i.component_id|cell }} · {{ i.feature|cell }}: {{ i.detail|cell(400) }}
{% endfor %}
{% else %}
- not computed
{% endfor %}
{% endif %}

## 7. Costs

{% if cost %}
| Item | Value |
|---|---|
{% for key, value in cost %}
| {{ key|cell }} | {{ value|cell }} |
{% endfor %}

Every line, with its source and date, is in `cost.json`.
{% else %}
Not computed for this run.
{% endif %}

---

**What this report does not claim.** Tests prove what they cover: a green run means the app's own original \
tests passed on each side, not that every behaviour is identical. Boomerang does not claim zero downtime, full \
compatibility, or that it migrates any app; the 🔴 items above need a human. Costs are estimates from published \
prices and the usage entered; Leaseweb is not always cheaper.
"""


def _environment() -> jinja2.Environment:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True,
                             keep_trailing_newline=True, autoescape=False)
    env.filters.update(cell=_cell, ok=_ok, ts=_ts, refs=_refs, score=score, ratio=tests_ratio,
                       change=change_label, light=lambda light: LIGHT_ICONS.get(light, light))
    return env


def render(ctx: RunContext) -> str:
    s = ctx.summary
    a = s.assessment
    last = s.verifications[-1] if s.verifications else None
    lost_order = ("lost", "changed", "preserved")
    lost = [(st, [i for i in s.lost if i.status == st]) for st in lost_order if any(i.status == st for i in s.lost)]
    return _environment().from_string(TEMPLATE).render(
        s=s, a=a, app_name=_app_name(ctx), status_icon=STATUS_ICONS.get(s.status, "·"),
        roundtrip_icon=STATUS_ICONS.get(s.roundtrip, "·"),
        counts=light_counts(a) if a else "", reds=[f for f in a.findings if f.light == "red"] if a else [],
        rewrites=s.rewrites, rewrite_summary=rewrite_counts(s.rewrites),
        last_tests=last.tests if last else None, lost=lost,
        status_names={"lost": "❌ lost", "changed": "🟠 changed", "preserved": "✅ preserved"},
        cost=_cost(ctx.root / "cost.json"),
    )


def write(ctx: RunContext) -> Path:
    """Render `report.md`; it never contains a secret (redacted and reported as an error if it would)."""
    text = render(ctx)
    found = find_secrets(text)
    if found:
        text = redact(text)
        ctx.emitter.emit("report", f"report.md contained {len(found)} secret-like value(s): redacted before writing",
                         "error", step="report", count=len(found))
    path = ctx.root / "report.md"
    atomic_write_text(path, text)
    ctx.emitter.emit("report", f"report written: {path.name}", "success", step="report", path=path.name)
    return path


def _app_name(ctx: RunContext) -> str:
    pam = ctx.root / "pam.json"
    try:
        return AppModel.model_validate_json(pam.read_text(encoding="utf-8")).app_name
    except (OSError, ValueError):
        return Path(ctx.summary.source_dir).name


def _cost(path: Path) -> list[tuple[str, Any]]:
    """Top-level scalar values of D's cost.json (its full schema is D's; the report shows the headline)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [(k, v) for k, v in data.items() if isinstance(v, str | int | float | bool)][:12]
