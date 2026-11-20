"""Boomerang UI (owner: D). Run with: streamlit run ui/app.py"""
from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run ui/app.py` only puts ui/ on the path; we need the repo root
# so `ui.*` and (later) `boomerang.*` import the same way everywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from ui import data, theme  # noqa: E402
from ui.components import breaks, code, costs, roundtrip, setup  # noqa: E402

st.set_page_config(
    page_title="Boomerang",
    page_icon="🪃",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject_css()

# ---------- session state ----------
ss = st.session_state
ss.setdefault("events", [])          # live events from the pipeline (ui_sink)
ss.setdefault("approved", False)
ss.setdefault("mode", "Replay")


# ---------- sidebar ----------
with st.sidebar:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;margin:4px 0 18px">'
        '<div style="width:26px;height:26px;border-radius:7px;display:grid;place-items:center;'
        'background:linear-gradient(135deg,#7170FF,#4E4BD8);font-size:14px">🪃</div>'
        '<div><div style="font-weight:600;color:#F7F8F8;letter-spacing:-0.01em">Boomerang</div>'
        '<div style="font-size:0.75rem;color:#8A8F98">AWS ⇄ Leaseweb</div></div></div>',
        unsafe_allow_html=True,
    )

    source_app = st.selectbox("Source app", ["sample-app/aws"], help="AWS app to migrate")

    mode = st.radio("Mode", ["Live", "Replay"], horizontal=True, key="mode")
    runs = data.list_runs()
    run_choice: Path | None = None
    if mode == "Replay":
        if runs:
            run_choice = st.selectbox("Recorded run", runs, format_func=lambda p: p.name)
        else:
            st.caption("No recorded runs yet: showing fixtures.")

    st.divider()
    migrate = st.button(
        "Migrate to Leaseweb", type="primary", width="stretch",
        disabled=mode != "Live",
    )
    roundtrip_btn = st.button(
        "Round trip back to AWS", width="stretch",
        disabled=mode != "Live",
    )
    if migrate or roundtrip_btn:
        # D-later: call boomerang.pipeline with emit_extra=ui_sink (needs C4/C6).
        st.toast("Pipeline not wired yet (waits for C4).", icon="⏳")

    st.divider()
    text, local = data.llm_badge()
    st.markdown(theme.badge(text, "local" if local else "cloud"), unsafe_allow_html=True)


# ---------- pick the data source ----------
if mode == "Replay" and run_choice is not None:
    src = data.run_source(run_choice)
else:
    src = data.fixtures_source()
    if mode == "Live" and ss.events:
        src.events = ss.events


# ---------- header ----------
st.markdown(
    f'<span class="bm-eyebrow"><b>InfraHack 2026</b>{src.label}</span>'
    '<div class="bm-title">Leave AWS. Prove it works.<br>Come back.</div>'
    '<div class="bm-sub">Boomerang shows what breaks, moves your app from '
    '<span class="bm-aws">AWS</span> to <span class="bm-lw">Leaseweb</span> with a local AI '
    "model, proves it with your own tests, and brings you back.</div>",
    unsafe_allow_html=True,
)

# Pipeline canvas (Railway-style nodes; Terraform-style plan → approve → apply)
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
