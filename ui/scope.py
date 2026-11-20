"""Partial migration state for the UI (owner: D, docs/16 §3.4).

A *placement* is the set of movable components that run self-hosted; the rest stays on
AWS. This module answers, for any source (demo, recorded run, live run):

- which components can move at all (movable kinds, not red, DLQs travel with their queue)
- where each one runs right now, and the history of moves
- what the UI may do: preview (demo data: simulated, nothing runs), live (C's scope
  support is installed and this is the run started here), or read only.

The contract it reads is docs/16 §2: run.json `scope.selfhosted` and `steps`, and the
approval's scope. Until C ships it (`boomerang/scope.py`), runs simply have no scope and
the UI previews on demo data.
"""
from __future__ import annotations

import importlib.util
import time
from typing import Any

import streamlit as st

from boomerang import cost
from ui.data import Source

MOVABLE_KINDS = cost.MOVABLE

# kind → (AWS service, self-hosted service, how it moves)
SERVICE = {
    "kv_table": ("DynamoDB", "Postgres", "kv adapter"),
    "queue": ("SQS", "RabbitMQ", "queue adapter"),
    "object_store": ("S3", "Object Storage", "S3-compatible"),
}


def supported() -> bool:
    """C's scope support (docs/16 §2) is installed: approve --only, move-over, move-back."""
    try:
        return importlib.util.find_spec("boomerang.scope") is not None
    except (ImportError, ValueError):
        return False


def movable(rows: list[dict]) -> list[dict]:
    """Component rows (breaks.group_findings) that can run self-hosted on their own."""
    return [r for r in rows
            if r["kind"] in MOVABLE_KINDS and r["light"] != "red" and not r["id"].endswith("_dlq")]


def _key(src: Source, what: str) -> str:
    return f"scope::{what}::{src.path}"


def _ids(value: Any) -> list[str] | None:
    """A scope as stored by C: {"selfhosted": [...]}, a plain list, or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("selfhosted")
    return list(value) if isinstance(value, (list, tuple, set)) else None


def approved_scope(src: Source, ids: list[str]) -> list[str] | None:
    """What the approval covers: its scope, or every movable component for a plain approval."""
    approval = src.json("approval")
    if approval:
        return _ids(approval.get("scope")) or list(ids)
    if src.kind == "fixtures" and st.session_state.get("approved"):
        return st.session_state.get(_key(src, "approved")) or list(ids)
    return None


def mode(src: Source) -> str:
    """preview | live | readonly (see module docstring)."""
    if src.kind == "fixtures":
        return "preview"
    live_run = st.session_state.get("live_run")
    if src.kind == "run" and src.path is not None and src.path.name == live_run and supported():
        return "live"
    return "readonly"


def placement(src: Source, ids: list[str]) -> set[str]:
    """Components running self-hosted now."""
    ss = st.session_state
    k = _key(src, "placement")
    if k in ss:
        return set(ss[k]) & set(ids)
    summ = src.summary
    stored = _ids(summ.get("scope"))
    if stored is not None:
        return set(stored) & set(ids)
    executed = summ.get("status") == "passed" or bool(summ.get("verifications"))
    approved = approved_scope(src, ids)
    if executed and approved is not None:
        return set(approved) & set(ids)
    return set()


def steps(src: Source) -> list[dict]:
    """Scope changes, oldest first: run.json `steps` plus this session's previews."""
    recorded = [dict(s, preview=False) for s in (src.summary.get("steps") or [])]
    return recorded + list(st.session_state.get(_key(src, "steps"), []))


def preview_move(src: Source, ids: list[str], comps: list[str], action: str) -> None:
    """Demo data only: change the placement in this session and log a preview step."""
    ss = st.session_state
    now = placement(src, ids)
    now = now | set(comps) if action == "move_over" else now - set(comps)
    ss[_key(src, "placement")] = sorted(now)
    ss.setdefault(_key(src, "steps"), []).append(
        {"at": time.time(), "action": action, "components": list(comps), "verification_ok": None,
         "data_moves": [], "preview": True})


def reset_preview(src: Source) -> None:
    for what in ("placement", "steps"):
        st.session_state.pop(_key(src, what), None)


def approve_preview(src: Source, chosen: list[str]) -> None:
    """Demo data: record the chosen scope as approved and start the placement from it."""
    ss = st.session_state
    ss.approved = True
    ss.approved_at = time.strftime("%H:%M:%S")
    ss[_key(src, "approved")] = list(chosen)
    reset_preview(src)
    ss[_key(src, "placement")] = list(chosen)


def kinds(rows: list[dict], comp_ids) -> list[str]:
    by_id = {r["id"]: r["kind"] for r in rows}
    return sorted({by_id[c] for c in comp_ids if c in by_id})


def label(selfhosted: set[str], ids: list[str]) -> tuple[str, str]:
    """(mode key, words) for a placement."""
    if not selfhosted:
        return "aws", "All on AWS"
    if set(ids) <= selfhosted:
        return "selfhosted", "All self-hosted"
    return "hybrid", "Hybrid"
