"""Tab 1: What breaks (owner: D, task D3).

Traffic lights per component (Snyk-style list), counters, lock-in before/after,
a boxed list of what will NOT be migrated, and the Approve plan gate
(Terraform-style plan → approve).
"""
from __future__ import annotations

import html
import time
from typing import Any

import streamlit as st

from ui.data import Source
from ui.theme import LIGHT_COLORS, LIGHT_EMOJI, LIGHT_LABEL, empty_state

SEVERITY = {"green": 0, "amber": 1, "red": 2}

# What each kind is on AWS and what it becomes on Leaseweb.
KIND_INFO = {
    "object_store": ("S3", "Object Storage (S3-compatible)"),
    "kv_table": ("DynamoDB", "Postgres via kv adapter"),
    "queue": ("SQS", "RabbitMQ via queue adapter"),
    "function": ("Lambda", "Not migrated"),
    "trigger": ("Event trigger", "Not migrated"),
    "identity": ("IAM", "Scoped service credentials (review)"),
    "monitoring": ("CloudWatch", "Not migrated"),
    "other": ("AWS", "Not migrated"),
}


def _as_dict(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _load_pam(src: Source) -> dict:
    for name in ("pam", "pam.scanned", "pam.aws"):
        pam = src.json(name)
        if pam:
            return _as_dict(pam)
    return {"components": []}


def group_findings(assessment: dict, pam: dict) -> list[dict]:
    """One row per component: worst light, features, distinct changes, deduped evidence."""
    kinds = {c["id"]: c for c in pam.get("components", [])}
    rows: dict[str, dict] = {}
    for f in assessment.get("findings", []):
        cid = f["component_id"]
        row = rows.setdefault(cid, {
            "id": cid,
            "kind": kinds.get(cid, {}).get("kind", "other"),
            "resource": kinds.get(cid, {}).get("resource_name", cid),
            "light": f["light"],
            "features": [],
            "changes": [],
            "evidence": {},
        })
        if SEVERITY[f["light"]] > SEVERITY[row["light"]]:
            row["light"] = f["light"]
        row["features"].append((f["feature"], f["light"]))
        if f["what_changes"] not in row["changes"]:
            row["changes"].append(f["what_changes"])
        for ref in f.get("evidence", []):
            row["evidence"][(ref["file"], ref["line"])] = ref
    out = list(rows.values())
    out.sort(key=lambda r: (SEVERITY[r["light"]], r["id"]))
    return out


def _pill(light: str) -> str:
    c = LIGHT_COLORS[light]
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;background:{c}14;'
            f'color:{c};border:1px solid {c}40;padding:2px 10px;border-radius:999px;'
            f'font-size:0.75rem;font-weight:500;white-space:nowrap">'
            f'<span style="width:6px;height:6px;border-radius:50%;background:{c};'
            f'box-shadow:0 0 6px {c}"></span>{LIGHT_LABEL[light]}</span>')


def _chip(text: str, light: str) -> str:
    c = LIGHT_COLORS[light]
    return (f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'border:1px solid rgba(255,255,255,0.08);background:#141516;color:#D0D6E0;'
            f'padding:1px 8px;border-radius:6px;font-size:0.74rem;margin:0 4px 4px 0">'
            f'<span style="width:5px;height:5px;border-radius:50%;background:{c}"></span>'
            f'{html.escape(text)}</span>')


def _component_card(row: dict) -> None:
    aws, target = KIND_INFO.get(row["kind"], KIND_INFO["other"])
    with st.container(border=True):
        left, right = st.columns([5, 2])
        with left:
            st.markdown(
                f'<b style="font-size:1.05rem">{html.escape(row["id"])}</b> '
                f'<span class="bm-muted">· <span class="bm-aws">{aws}</span> → '
                f'{html.escape(target)}</span>',
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(f'<div style="text-align:right">{_pill(row["light"])}</div>',
                        unsafe_allow_html=True)
        st.markdown("".join(_chip(f, l) for f, l in row["features"]), unsafe_allow_html=True)
        for change in row["changes"]:
            st.markdown(f'<div class="bm-muted" style="margin-top:4px">• {html.escape(change)}</div>',
                        unsafe_allow_html=True)
        refs = sorted(row["evidence"].values(), key=lambda r: (r["file"], r["line"]))
        if refs:
            with st.expander(f"Evidence · {len(refs)} code reference{'s' if len(refs) != 1 else ''}"):
                for ref in refs:
                    sym = f" · `{ref['symbol']}()`" if ref.get("symbol") else ""
                    st.markdown(f"`{ref['file']}:{ref['line']}`{sym} · {ref['operation']}")
                    st.code(ref.get("snippet", ""), language="python")
        else:
            st.caption("No code reference found (Terraform or config only).")


def render(src: Source) -> None:
    assessment = src.json("assessment") or src.summary.get("assessment")
    if assessment is None:
        empty_state("No assessment yet", "Run a scan, or add fixtures/assessment.json.")
        return
    assessment = _as_dict(assessment)
    rows = group_findings(assessment, _load_pam(src))
    migrated = [r for r in rows if r["light"] != "red"]
    reds = [r for r in rows if r["light"] == "red"]
    count = {k: sum(r["light"] == k for r in rows) for k in SEVERITY}

    # ---- counters ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(":green[●] Config only", count["green"])
    c2.metric(":orange[●] Via adapter", count["amber"])
    c3.metric(":red[●] Needs a human", count["red"])
    summ = src.summary
    lock = ({"before": summ["lock_in_before"], "after": summ.get("lock_in_after")}
            if summ.get("lock_in_before") else src.json("lockin"))
    if lock and lock.get("before") and lock.get("after") is None:
        # planned, not migrated yet: there is no "after" score, so nothing is removed yet
        c4.metric("Lock-in score", f"{lock['before']}", help="Weighted lock-in score before the migration; "
                  "the after score appears once the run is executed.")
    elif lock and lock.get("before"):
        removed = round(100 * (1 - lock["after"] / lock["before"]))
        c4.metric("Lock-in removed", f"{removed}%",
                  help=f"Weighted lock-in score {lock['before']} → {lock['after']} "
                       "(what remains is the red items left on AWS).")
        st.progress(removed / 100)
    else:
        c4.metric("Lock-in removed", "–")
    fc = {k: sum(f["light"] == k for f in assessment.get("findings", [])) for k in SEVERITY}
    st.caption(f"Counters are per AWS component. Per feature: {fc['green']} config only · "
               f"{fc['amber']} via adapter · {fc['red']} need a human "
               f"({len(assessment.get('findings', []))} findings).")

    # ---- what moves ----
    worst = max((r["light"] for r in migrated), key=SEVERITY.get, default="green")
    st.markdown(f"#### Will be migrated &nbsp; {_pill(worst)}", unsafe_allow_html=True)
    for row in migrated:
        _component_card(row)

    # ---- what doesn't ----
    if reds:
        st.markdown("#### Will NOT be migrated")
        with st.container(border=True):
            st.error("Needs a human. Boomerang flags these and does not fake them; "
                     "they stay on AWS until someone handles them.")
            for row in reds:
                _component_card(row)

    # ---- scope + approval gate (docs/16: choose what moves, then approve) ----
    st.divider()
    _gate(src, rows, reds, assessment)


def _approve_label(chosen: list[str], ids: list[str]) -> str:
    if not chosen:
        return "Select at least one service"
    if set(chosen) >= set(ids):
        return f"Approve and move all {len(ids)}"
    return f"Approve and move {len(chosen)} of {len(ids)}"


def _gate(src: Source, rows: list[dict], reds: list[dict], assessment: dict) -> None:
    from ui import runner
    from ui import scope as sc
    from ui.components import scope as scope_ui

    ss = st.session_state
    ids = [r["id"] for r in sc.movable(rows)]
    approval = src.json("approval")
    live_run = ss.get("live_run")
    is_live = src.kind == "run" and src.path is not None and src.path.name == live_run

    if src.kind == "fixtures":
        # Demo data: the whole flow works as a preview in this browser session.
        if ss.get("approved"):
            chosen = scope_ui.choose(src, rows, reds, assessment, editable=False,
                                     initial=sc.approved_scope(src, ids))
            st.success(f"Plan approved at {ss.get('approved_at', '')}: {len(chosen)} of {len(ids)} services move; "
                       f"{len(reds)} stay with a human. See Placement to move more, or bring one back.")
            if st.button("Revoke approval"):
                ss.approved = False
                sc.reset_preview(src)
                st.rerun()
        else:
            chosen = scope_ui.choose(src, rows, reds, assessment, editable=True)
            st.caption("Nothing is written until you approve. AWS stays untouched until every check passes.")
            if st.button(_approve_label(chosen, ids), type="primary", disabled=not chosen):
                sc.approve_preview(src, chosen)
                st.rerun()
    elif approval:
        chosen = scope_ui.choose(src, rows, reds, assessment, editable=False, initial=sc.approved_scope(src, ids))
        when = time.strftime("%H:%M:%S", time.localtime(approval.get("ts", 0)))
        st.success(f"Plan approved by {approval.get('approved_by', '?')} at {when} "
                   f"(plan hash {str(approval.get('plan_sha256', ''))[:12]}). Self-hosted: "
                   f"{', '.join(chosen) or 'nothing'}; stays on AWS: "
                   f"{', '.join(i for i in ids if i not in chosen) or 'nothing'}; {len(reds)} stay with a human.")
    elif is_live:
        step = ss.get("step")
        busy = bool(step and not step.done)
        waiting = src.summary.get("status") == "awaiting_approval"
        chosen = scope_ui.choose(src, rows, reds, assessment, editable=waiting and not busy)
        partial = bool(chosen) and set(chosen) != set(ids)
        allowed = not partial or sc.supported()
        st.caption("Nothing is written until you approve. AWS stays untouched until every check passes.")
        if st.button(_approve_label(chosen, ids), type="primary",
                     disabled=busy or not waiting or not chosen or not allowed):
            ss.step = runner.start("approve", live_run, then=["execute"], components=chosen if partial else None)
            st.rerun()
        if partial and not allowed:
            st.caption("Moving only some services needs the engine's scope support (C: boomerang/scope.py). "
                       "Select all of them to run the full migration now.")
        if not waiting and not busy:
            st.caption("The plan isn't waiting for approval (see the activity log above).")
    else:
        # A recorded run opened in Replay: approving here would only fake it in this
        # browser session, so the gate is read-only. Real approval is a CLI call.
        scope_ui.choose(src, rows, reds, assessment, editable=False)
        run_id = src.path.name if src.path is not None else "<run_id>"
        status = src.summary.get("status")
        if status == "awaiting_approval":
            st.info(f"This recorded run is waiting for approval. To go ahead, run `boomerang approve {run_id}` "
                    f"(add `--only a,b` to move some services) then `boomerang execute {run_id}`, or start a new "
                    "run in Live mode.")
        else:
            st.info(f"Recorded run without an approval record (status: {status or 'unknown'}).")
