"""Tab 2: Code (owner: D, task D5).

One card per function the de-lock touched (C's RewriteRecord, docs/15 L1/L14): what the
AI rewrote, what code checked as a 1:1 mapping, config-only swaps, and fixer rounds, each
with its diff. Labels are honest: "AI" only where the model actually changed code.
"""
from __future__ import annotations

import html

import streamlit as st

from ui.data import Source
from ui.theme import ACCENT, LIGHT_COLORS, MUTED, empty_state, status_icon

KIND = {
    "ai": ("AI rewrite", ACCENT),
    "fix": ("AI fix", LIGHT_COLORS["amber"]),
    "config": ("Config / checked by code", MUTED),
}


def _pill(kind: str) -> str:
    label, c = KIND.get(kind, (kind, MUTED))
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;background:{c}18;color:{c};'
            f'border:1px solid {c}55;padding:2px 10px;border-radius:999px;font-size:0.74rem;font-weight:500">'
            f'<span style="width:6px;height:6px;border-radius:50%;background:{c}"></span>{label}</span>')


def _records(src: Source) -> list[dict]:
    recs = src.summary.get("rewrites") or src.json("delock") or []
    return [r.model_dump() if hasattr(r, "model_dump") else r for r in recs]


def render(src: Source) -> None:
    recs = _records(src)
    if not recs:
        empty_state("No code changes yet", "Approve the plan and run the migration; each function the "
                    "de-lock touches shows up here with its diff.")
        return
    summ = src.summary

    ai = [r for r in recs if r["kind"] == "ai"]
    fixes = [r for r in recs if r["kind"] == "fix"]
    swaps = [r for r in recs if r["kind"] == "config" and r["function"] == "module"]
    checked = [r for r in recs if r["kind"] == "config" and r["function"] != "module"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rewritten by AI", len(ai), help="Functions the local model changed, rule-checked before use.")
    c2.metric("1:1, checked by code", len(checked), help="Adapter calls match exactly; no AI call was made.")
    c3.metric("Client swaps", len(swaps), help="Module-level config changes (S3 endpoint, adapter clients).")
    c4.metric("Fixer rounds", len(fixes), help="Functions the fixer changed after a failing test.")

    unchanged = summ.get("tests_unchanged")
    llm = summ.get("llm") or ""
    tests_txt = ("Original tests unchanged (hashed before and after)" if unchanged
                 else "Original tests changed" if unchanged is False else "Test hashes not checked yet")
    st.markdown(f'<div class="bm-muted" style="display:flex;align-items:center">{status_icon(unchanged, 16)}'
                f'{tests_txt} · Model: {html.escape(llm or "n/a")}</div>', unsafe_allow_html=True)

    choice = st.segmented_control(
        "Show", ["All", "AI", "Checked by code", "Fixes"], default="All", key="code_filter",
        label_visibility="collapsed",
    )
    pick = {"AI": ai, "Checked by code": swaps + checked, "Fixes": fixes}.get(choice or "All", recs)

    for file in sorted({r["file"] for r in pick}):
        st.markdown(f"#### `{file}`")
        for r in [r for r in pick if r["file"] == file]:
            with st.container(border=True):
                left, right = st.columns([5, 2])
                name = "module (imports + clients)" if r["function"] == "module" else f'{r["function"]}()'
                extra = f' · attempt {r["attempt"]}' if r.get("attempt") else ""
                left.markdown(f'<b>{html.escape(name)}</b><span class="bm-muted"> · '
                              f'{r.get("changed_lines", 0)} lines changed{extra}</span>',
                              unsafe_allow_html=True)
                right.markdown(f'<div style="text-align:right">{_pill(r["kind"])}</div>',
                               unsafe_allow_html=True)
                if r.get("analysis"):
                    st.markdown(f'<div class="bm-muted">{html.escape(r["analysis"])}</div>',
                                unsafe_allow_html=True)
                if r.get("unsupported"):
                    st.warning("Not supported by the adapter: " + ", ".join(r["unsupported"]))
                if not r.get("accepted", True):
                    st.error(f'Rejected, original kept: {r.get("rejected_reason") or "rule check failed"}')
                diff = src.text(r["diff_path"]) if r.get("diff_path") else None
                if diff:
                    with st.expander("Diff", expanded=r["kind"] in ("ai", "fix")):
                        st.code(diff, language="diff")
