"""Dashboard page: run a migration and inspect every stage (owner: D)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui import data, theme
from ui.components import breaks, code, costs, roundtrip, setup


def render() -> None:
    ss = st.session_state
    ss.setdefault("events", [])          # live events from the pipeline (ui_sink)
    ss.setdefault("approved", False)
    ss.setdefault("mode", "Replay")

    # ---------- sidebar ----------
    with st.sidebar:
        st.selectbox("Source app", ["sample-app/aws"], help="AWS app to migrate")

        mode = st.radio("Mode", ["Live", "Replay"], horizontal=True, key="mode")
        runs = data.list_runs()
        run_choice: Path | None = None
        if mode == "Replay":
            if runs:
                run_choice = st.selectbox("Recorded run", runs, format_func=lambda p: p.name)
            else:
                st.caption("No recorded runs yet: showing fixtures.")

        st.divider()
        migrate = st.button("Migrate to Leaseweb", type="primary", width="stretch",
                            disabled=mode != "Live")
        roundtrip_btn = st.button("Round trip back to AWS", width="stretch",
                                  disabled=mode != "Live")
        if migrate or roundtrip_btn:
            # D-later: call boomerang.pipeline with emit_extra=ui_sink (needs C4/C6).
            st.toast("Pipeline not wired yet (waits for C4).", icon="⏳")

        st.divider()
        text, local = data.llm_badge()
        st.markdown(theme.badge(text, "local" if local else "cloud"), unsafe_allow_html=True)

    # ---------- data source ----------
    if mode == "Replay" and run_choice is not None:
        src = data.run_source(run_choice)
    else:
        src = data.fixtures_source()
        if mode == "Live" and ss.events:
            src.events = ss.events

    # ---------- header (compact, app-style) ----------
    st.markdown(
        f'<span class="bm-eyebrow"><b>Migration</b>{src.label}</span>'
        '<div class="bm-apptitle">ShopLite · <span class="bm-aws">AWS</span> → '
        '<span class="bm-lw">Leaseweb</span> → <span class="bm-aws">AWS</span></div>',
        unsafe_allow_html=True,
    )

    summ = src.summary
    if src.is_demo:
        st.warning("Demo data: this run was assembled from A's and B's real outputs plus illustrative "
                   "pipeline results (fixtures/run-demo). It is replaced by a real run once C's "
                   "pipeline lands.", icon="⚠️")
    if summ.get("llm") and "cloud" in summ["llm"]:
        st.info(f"This run used the cloud fallback model ({summ['llm']}), not a local model.")

    status = data.stage_status(src.events)
    if ss.approved:
        status["approve"] = "done"
    st.markdown(theme.pipeline_html(status), unsafe_allow_html=True)

    # ---------- tabs ----------
    t1, t2, t3, t4, t5 = st.tabs(
        ["What breaks", "Code", "Leaseweb setup", "Data & round trip", "Costs"]
    )
    with t1:
        breaks.render(src)
    with t2:
        code.render(src)
    with t3:
        setup.render(src)
    with t4:
        roundtrip.render(src)
    with t5:
        costs.render(src)
