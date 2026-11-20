"""Boomerang Doctor: diagnose the self-hosted stack after a migration (docs/18). Owner C (piece 3).

- ``run(run_id)``: collect the stack's signals (A, ``doctor.signals``) → if anything fails, the rules name the
  cause and the fixes (B, ``doctor.rules``) → the AI explains it in plain words → ``runs/<id>/doctor.json``.
- ``fix(run_id, action_id, target, by=…)``: run **one** fix the diagnosis offered (A, ``doctor.actions``), record
  it, and check the stack again.

Rules decide, the AI explains: the AI's answer can only add the explanation and raise the severity
(degraded → down). It never picks, adds or reorders fixes, and never runs anything (docs/13 S2). A human approves
every fix: ``fix()`` refuses any action that isn't in the current diagnosis.

A's and B's modules are imported inside the functions (like the CLI does), so this module loads, and its tests
run, before theirs exist. Until they're merged, a call says which file is missing and who owns it.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from boomerang.config import get_settings
from boomerang.fsutil import atomic_write_text
from boomerang.models import (
    Action,
    AppliedAction,
    AppModel,
    Diagnosis,
    DoctorExplanation,
    Incident,
    RunSummary,
    Signal,
)
from boomerang.pipeline import PipelineError

if TYPE_CHECKING:
    from boomerang.events import Emitter

DOCTOR_FILE = "doctor.json"
MAX_LINES = 40                      # evidence lines per signal sent to the AI (A caps them too)
MARKERS = ("EVIDENCE", "DIAGNOSIS")
OWNERS = {"signals": ("A (Manuel)", "docs/18 §3.1"), "actions": ("A (Manuel)", "docs/18 §3.2"),
          "rules": ("B (Francisco)", "docs/18 §4")}


class DoctorError(PipelineError):
    """A user-facing stop that says what to do (the CLI prints it and exits 3)."""


# ---------- public API ----------

def run(run_id: str, *, emit: Emitter | None = None) -> Incident | None:
    """Diagnose the run's self-hosted stack. ``None`` = healthy (an open incident is then marked resolved)."""
    summary, root = _precondition(run_id)
    emit = emit or _emitter(run_id, root)
    signals = _collect(root / "leaseweb")
    failing = [s for s in signals if not s.ok]
    emit.emit("doctor", f"checked the stack: {len(signals)} signals, {len(failing)} failing", "info",
              signals=len(signals), failing=len(failing))

    if not failing:
        _resolve_open(root, emit, "stack healthy")
        emit.emit("doctor", "stack healthy", "success")
        return None

    diagnosis = _diagnose(signals, _read_pam(root), summary)
    explanation, raise_severity = explain(diagnosis, emit=emit)
    severity = "down" if raise_severity else diagnosis.severity
    incident = Incident(run_id=run_id, at=time.time(), diagnosis=diagnosis, explanation=explanation,
                        severity=severity, llm=get_settings().llm_badge)
    _save(root, incident)
    emit.emit("doctor", f"{diagnosis.rule}: {diagnosis.root_cause}", "error" if severity == "down" else "warning",
              rule=diagnosis.rule, service=diagnosis.service, component_id=diagnosis.component_id,
              severity=severity, impact=diagnosis.impact, actions=[a.id for a in diagnosis.actions])
    return incident


def fix(run_id: str, action_id: str, target: str, *, by: str, emit: Emitter | None = None) -> Incident:
    """Run one fix the current diagnosis offered, record it in ``doctor.json``, then check the stack again."""
    _summary, root = _precondition(run_id)
    emit = emit or _emitter(run_id, root)
    incident = load(run_id)
    if incident is None:
        raise DoctorError(f"no incident for run {run_id}: run `boomerang doctor {run_id}` first")
    if incident.resolved_at is not None:
        raise DoctorError(f"the incident of run {run_id} is already resolved: run `boomerang doctor {run_id}` "
                          "to check the stack again")
    action = next((a for a in incident.diagnosis.actions if a.id == action_id and a.target == target), None)
    if action is None:
        offered = ", ".join(f"{a.id} {a.target}" for a in incident.diagnosis.actions) or "none"
        raise DoctorError(f"`{action_id} {target}` is not one of this incident's fixes ({offered})")

    emit.emit("doctor", f"fix: {action.label} · approved by {by}", "info", action=action.id, target=action.target,
              risk=action.risk, by=by)
    try:
        result = _apply(run_id, root / "leaseweb", action, by=by, emit=emit)
        ok, detail = bool(result.get("ok")), str(result.get("detail", ""))
    except Exception as e:  # recorded, then raised: the incident keeps what was tried
        if isinstance(e, DoctorError):           # a teammate's module isn't merged yet: nothing was tried
            raise
        ok, detail = False, f"{type(e).__name__}: {e}"
        _record(root, incident, action, by, ok, detail)
        emit.emit("doctor", f"fix failed: {action.label}: {detail}", "error", action=action.id, target=action.target)
        raise
    _record(root, incident, action, by, ok, detail)
    emit.emit("doctor", f"{action.label}: {'done' if ok else 'failed'}{f' · {detail}' if detail else ''}",
              "success" if ok else "error", action=action.id, target=action.target, ok=ok)

    failing = [s for s in _collect(root / "leaseweb") if not s.ok]
    if failing:
        names = ", ".join(sorted({f"{s.service} ({s.source})" for s in failing}))
        emit.emit("doctor", f"still failing after the fix: {names}", "warning", failing=len(failing))
    else:
        incident.resolved_at = time.time()
        _save(root, incident)
        emit.emit("doctor", "stack healthy again", "success")
    return incident


def explain(diagnosis: Diagnosis, *, emit: Emitter | None = None) -> tuple[str, bool]:
    """The AI's plain words for a diagnosis and whether it raises the severity. ``("", False)`` when the model
    can't answer: the rules carry the incident without it (docs/18 §5.3)."""
    from boomerang import llm
    from boomerang.delock import prompt_environment

    tmpl = prompt_environment().get_template("doctor.md")
    try:
        answer = llm.structured(system=str(tmpl.module.system()), user=str(tmpl.module.user(**prompt_fields(diagnosis))),
                                schema=DoctorExplanation, label="doctor", emit=emit, stage="doctor")
    except llm.LLMError as e:          # unavailable, cache miss in replay, or two invalid answers
        if emit is not None:
            emit.emit("doctor", f"no AI explanation ({type(e).__name__}); the rules' diagnosis stands", "warning")
        return "", False
    raise_it = answer.raise_severity and diagnosis.severity == "degraded"
    return answer.explanation.strip(), raise_it


def prompt_fields(diagnosis: Diagnosis) -> dict[str, Any]:
    """What ``prompts/doctor.md`` gets. Run-independent (no timestamps, no run id), so the cache replays."""
    from boomerang.llm import stable_text

    evidence = []
    for s in diagnosis.evidence:
        head = f"[{s.source}] {s.service}: {'ok' if s.ok else 'FAILING'}{f' · {s.detail}' if s.detail else ''}"
        lines = [stable_text(line) for line in s.lines[-MAX_LINES:]]
        evidence.append("\n".join([head, *(f"  {line}" for line in lines)]))
    return {
        "rule": diagnosis.rule,
        "root_cause": _data(diagnosis.root_cause),
        "service": diagnosis.service or "none",
        "component_id": diagnosis.component_id or "none",
        "severity": diagnosis.severity,
        "impact": [_data(i) for i in diagnosis.impact],
        "actions": [f"{a.label} (risk {a.risk})" for a in diagnosis.actions],
        "evidence": _data("\n".join(evidence)),
    }


def load(run_id: str) -> Incident | None:
    """The run's ``doctor.json``, or ``None`` if it was never diagnosed with a failure."""
    from boomerang import pipeline

    path = pipeline.run_dir(run_id) / DOCTOR_FILE
    return Incident.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def break_service(run_id: str, service: str) -> None:
    """Demo only: stop one service of the run's lab stack (A's ``actions.break_service`` refuses anything else)."""
    _summary, root = _precondition(run_id)
    _module("actions").break_service(root / "leaseweb", service)


# ---------- A's and B's modules, imported when needed ----------

def _collect(leaseweb_dir: Path) -> list[Signal]:
    return _module("signals").collect(leaseweb_dir)


def _diagnose(signals: list[Signal], pam: AppModel, summary: RunSummary) -> Diagnosis:
    return _module("rules").diagnose(signals, pam, summary.scope)


def _apply(run_id: str, leaseweb_dir: Path, action: Action, *, by: str, emit: Emitter) -> dict[str, Any]:
    return _module("actions").apply(run_id, leaseweb_dir, action, by=by, emit=emit)


def _module(name: str) -> Any:
    try:
        return importlib.import_module(f"boomerang.doctor.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"boomerang.doctor.{name}":
            raise
        owner, doc = OWNERS[name]
        raise DoctorError(f"boomerang/doctor/{name}.py isn't merged yet (owner {owner}, spec {doc})") from None


# ---------- helpers ----------

def _precondition(run_id: str) -> tuple[RunSummary, Path]:
    """The run has a self-hosted stack, and it's the one that is up."""
    from boomerang import pipeline

    root = pipeline.run_dir(run_id)
    summary = pipeline.load_run(run_id)
    if summary.status != "passed" and summary.scope is None:
        raise DoctorError(f"run {run_id} is {summary.status}: the Doctor checks a migrated, running stack; "
                          f"run `boomerang execute {run_id}` first")
    if not (root / "leaseweb").is_dir():
        raise DoctorError(f"run {run_id} has no self-hosted files (runs/{run_id}/leaseweb)")
    up = pipeline.active_stack()
    if up != run_id:
        raise DoctorError(f"the self-hosted stack that is up belongs to {up or 'no run'}, not {run_id}")
    return summary, root


def _read_pam(root: Path) -> AppModel:
    return AppModel.model_validate_json((root / "pam.json").read_text(encoding="utf-8"))


def _record(root: Path, incident: Incident, action: Action, by: str, ok: bool, detail: str) -> None:
    incident.applied.append(AppliedAction(action=action, by=by, at=time.time(), ok=ok, detail=detail))
    _save(root, incident)


def _resolve_open(root: Path, emit: Emitter, why: str) -> None:
    path = root / DOCTOR_FILE
    if not path.is_file():
        return
    incident = Incident.model_validate_json(path.read_text(encoding="utf-8"))
    if incident.resolved_at is None:
        incident.resolved_at = time.time()
        _save(root, incident)
        emit.emit("doctor", f"incident {incident.diagnosis.rule} resolved: {why}", "success",
                  rule=incident.diagnosis.rule)


def _save(root: Path, incident: Incident) -> None:
    atomic_write_text(root / DOCTOR_FILE, incident.model_dump_json(indent=2))


def _emitter(run_id: str, root: Path) -> Emitter:
    from boomerang.events import Emitter, jsonl_sink

    return Emitter(run_id, [jsonl_sink(root / "events.jsonl")])


def _data(text: str) -> str:
    """Stack output goes between ``<<<NAME`` / ``NAME>>>`` markers (S8): a marker inside it can't close them."""
    for name in MARKERS:
        text = text.replace(f"<<<{name}", f"<<< {name}").replace(f"{name}>>>", f"{name} >>>")
    return text
