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
    st.markdown("### 🪃 Boomerang")
    st.caption("Leave AWS. Prove it works. Come back.")

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
left, right = st.columns([3, 1])
with left:
    st.markdown(
        '<h1 style="margin-bottom:0">Boomerang</h1>'
        '<div class="bm-muted">'
        '<span class="bm-aws">AWS</span> → <span class="bm-lw">Leaseweb</span> → '
        '<span class="bm-aws">AWS</span> · see what breaks, move it, prove it, come back'
        "</div>",
        unsafe_allow_html=True,
    )
with right:
    st.markdown(
        f'<div style="text-align:right;margin-top:1.2rem">{theme.badge(src.label)}</div>',
        unsafe_allow_html=True,
    )

# Pipeline strip (Terraform-style plan → approve → apply)
status = data.stage_status(src.events)
if ss.approved and "approve" in status:
    status["approve"] = "done"
steps = "".join(
    f'<div class="bm-step {status.get(key, "")}"><b>{label}</b>'
    f'{status.get(key, "pending")}</div>'
    for key, label in theme.PIPELINE
)
st.markdown(f'<div class="bm-strip">{steps}</div>', unsafe_allow_html=True)


# ---------- tabs ----------
t1, t2, t3, t4, t5 = st.tabs(
    ["🚦 What breaks", "🧩 Code", "🏗️ Leaseweb setup", "🔁 Data & round trip", "💶 Costs"]
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
