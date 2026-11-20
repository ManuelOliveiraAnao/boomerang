"""Shared colours, labels and CSS for the Boomerang UI (owner: D)."""
from __future__ import annotations

import streamlit as st

LIGHT_COLORS = {"green": "#2EA043", "amber": "#D29922", "red": "#F85149"}
LIGHT_EMOJI = {"green": "🟢", "amber": "🟠", "red": "🔴"}
LIGHT_LABEL = {
    "green": "Config only",
    "amber": "Adapter, changes listed",
    "red": "Needs a human",
}
LEASEWEB_RED = "#E3001B"
AWS_ORANGE = "#FF9900"
MUTED = "#8B949E"
BORDER = "#262A31"

# Pipeline stages in display order (subset of models.Stage the user sees).
PIPELINE = [
    ("scan", "Scan"),
    ("assess", "Assess"),
    ("approve", "Approve"),
    ("delock", "De-lock"),
    ("generate", "Generate"),
    ("verify", "Verify"),
    ("data", "Data"),
    ("rollback", "Round trip"),
]

_CSS = f"""
<style>
  .block-container {{ padding-top: 2rem; max-width: 1400px; }}
  h1, h2, h3 {{ letter-spacing: -0.02em; }}
  .bm-muted {{ color: {MUTED}; font-size: 0.9rem; }}
  .bm-badge {{
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    border: 1px solid {BORDER}; font-size: 0.8rem; color: #C9D1D9;
    background: #15181D; white-space: nowrap;
  }}
  .bm-badge.local {{ border-color: #2EA04366; color: #56D364; }}
  .bm-badge.cloud {{ border-color: #D2992266; color: #E3B341; }}
  .bm-strip {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 0.25rem 0 1rem; }}
  .bm-step {{
    flex: 1 1 0; min-width: 90px; padding: 8px 10px; border-radius: 8px;
    border: 1px solid {BORDER}; background: #111418; font-size: 0.82rem;
    color: {MUTED};
  }}
  .bm-step b {{ display: block; color: #C9D1D9; font-weight: 600; }}
  .bm-step.done {{ border-color: #2EA04388; }}
  .bm-step.done b::before {{ content: "✓ "; color: #2EA043; }}
  .bm-step.running {{ border-color: {LEASEWEB_RED}; }}
  .bm-step.running b::before {{ content: "● "; color: {LEASEWEB_RED}; }}
  .bm-step.failed {{ border-color: #F85149; }}
  .bm-step.failed b::before {{ content: "✕ "; color: #F85149; }}
  .bm-step.waiting {{ border-color: #D29922; }}
  .bm-step.waiting b::before {{ content: "◐ "; color: #D29922; }}
  .bm-empty {{
    border: 1px dashed {BORDER}; border-radius: 10px; padding: 28px;
    text-align: center; color: {MUTED};
  }}
  .bm-aws {{ color: {AWS_ORANGE}; font-weight: 600; }}
  .bm-lw {{ color: {LEASEWEB_RED}; font-weight: 600; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "") -> str:
    return f'<span class="bm-badge {kind}">{text}</span>'


def empty_state(title: str, hint: str) -> None:
    st.markdown(
        f'<div class="bm-empty"><b>{title}</b><br><span class="bm-muted">{hint}</span></div>',
        unsafe_allow_html=True,
    )
