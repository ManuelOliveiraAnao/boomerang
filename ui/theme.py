"""Shared colours, labels and CSS for the Boomerang UI (owner: D).

Look: Linear-style neutral dark base (near-black, Inter, hairline translucent
borders, muted grey text, white pill primary button) with a Railway-style
dotted canvas for the pipeline, where each stage is a connected node card.
"""
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
ACCENT = "#7170FF"
TEXT = "#F7F8F8"
TEXT_2 = "#D0D6E0"
MUTED = "#8A8F98"
FAINT = "#62666D"
BG = "#08090A"
BG_1 = "#0F1011"
BG_2 = "#141516"
BORDER = "rgba(255,255,255,0.08)"
BORDER_STRONG = "rgba(255,255,255,0.14)"

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

STATUS_COLOR = {
    "done": LIGHT_COLORS["green"],
    "running": ACCENT,
    "waiting": LIGHT_COLORS["amber"],
    "failed": LIGHT_COLORS["red"],
    "": FAINT,
}

_CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  /* Font comes from .streamlit/config.toml (Inter); only tune features here so
     Streamlit's Material icon font is left alone. */
  html, body {{ font-feature-settings: "cv01", "ss03"; }}
  .block-container {{ padding-top: 2.2rem; max-width: 1280px; }}
  h1, h2, h3, h4 {{ letter-spacing: -0.022em; font-weight: 560 !important; color: {TEXT}; }}
  h4 {{ font-size: 1rem !important; color: {TEXT_2}; margin-top: 1.2rem; }}
  p, li {{ color: {TEXT_2}; }}

  /* Sidebar: Linear-style panel */
  section[data-testid="stSidebar"] {{ border-right: 1px solid {BORDER}; }}
  section[data-testid="stSidebar"] hr {{ border-color: {BORDER}; }}

  /* Buttons: white pill primary (Linear), ghost secondary */
  .stButton > button {{
    border-radius: 999px; font-weight: 500; font-size: 0.85rem;
    border: 1px solid {BORDER_STRONG}; background: transparent; color: {TEXT_2};
    transition: background .15s, border-color .15s;
  }}
  .stButton > button:hover {{ border-color: rgba(255,255,255,0.28); color: {TEXT}; background: rgba(255,255,255,0.04); }}
  .stButton > button[kind="primary"] {{
    background: #E5E5E6; color: {BG}; border-color: #E5E5E6;
    box-shadow: 0 1px 2px rgba(0,0,0,.4);
  }}
  .stButton > button[kind="primary"]:hover {{ background: #fff; color: {BG}; }}
  .stButton > button:disabled {{ opacity: .35; }}

  /* Tabs: small, muted, white when selected */
  .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {BORDER}; }}
  .stTabs [data-baseweb="tab"] {{
    font-size: 0.85rem; color: {MUTED}; padding: 6px 12px; border-radius: 8px 8px 0 0;
  }}
  .stTabs [aria-selected="true"] {{ color: {TEXT}; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background: {TEXT}; height: 1px; }}

  /* Bordered containers and metrics: Railway-style rounded cards */
  div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-color: {BORDER} !important; border-radius: 14px !important; background: {BG_1};
  }}
  div[data-testid="stMetric"] {{
    background: {BG_1}; border: 1px solid {BORDER}; border-radius: 14px; padding: 14px 16px;
  }}
  div[data-testid="stMetricLabel"] p {{ color: {MUTED}; font-size: 0.8rem; }}
  div[data-testid="stMetricValue"] {{ font-weight: 560; letter-spacing: -0.02em; }}
  div[data-testid="stExpander"] details {{ border-color: {BORDER}; border-radius: 10px; }}
  div[data-testid="stProgress"] > div > div > div > div {{ background: {ACCENT}; }}

  /* Header */
  .bm-eyebrow {{
    display: inline-flex; align-items: center; gap: 8px; font-size: 0.78rem; color: {MUTED};
    border: 1px solid {BORDER}; border-radius: 999px; padding: 3px 12px 3px 4px; background: {BG_1};
  }}
  .bm-eyebrow b {{ background: {ACCENT}22; color: #A5A4FF; border-radius: 999px; padding: 1px 8px; font-weight: 500; }}
  .bm-title {{ font-size: 3rem; font-weight: 560; letter-spacing: -0.035em; line-height: 1.05; margin: 14px 0 6px;
    background: linear-gradient(180deg, #fff 30%, #9BA0A8); -webkit-background-clip: text; background-clip: text; color: transparent; }}
  .bm-sub {{ color: {MUTED}; font-size: 1rem; max-width: 640px; }}
  .bm-muted {{ color: {MUTED}; font-size: 0.88rem; }}
  .bm-aws {{ color: {AWS_ORANGE}; font-weight: 600; }}
  .bm-lw {{ color: {LEASEWEB_RED}; font-weight: 600; }}

  /* Badges */
  .bm-badge {{
    display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
    border: 1px solid {BORDER}; font-size: 0.78rem; color: {TEXT_2}; background: {BG_1}; white-space: nowrap;
  }}
  .bm-badge::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: {FAINT}; }}
  .bm-badge.local::before {{ background: {LIGHT_COLORS["green"]}; box-shadow: 0 0 8px {LIGHT_COLORS["green"]}; }}
  .bm-badge.cloud::before {{ background: {LIGHT_COLORS["amber"]}; }}

  /* Pipeline: Railway-style dotted canvas with connected nodes */
  .bm-canvas {{
    position: relative; margin: 18px 0 22px; padding: 22px 18px; border-radius: 16px;
    border: 1px solid {BORDER}; background-color: {BG_1};
    background-image: radial-gradient(rgba(255,255,255,0.07) 1px, transparent 1px);
    background-size: 18px 18px;
  }}
  .bm-strip {{ display: flex; align-items: center; gap: 0; overflow-x: auto; }}
  .bm-node {{
    flex: 0 0 auto; min-width: 104px; padding: 9px 12px; border-radius: 10px;
    border: 1px solid {BORDER_STRONG}; background: {BG_2}; box-shadow: 0 4px 14px rgba(0,0,0,.35);
  }}
  .bm-node .n {{ display: flex; align-items: center; gap: 7px; color: {TEXT}; font-size: 0.84rem; font-weight: 500; }}
  .bm-node .s {{ color: {FAINT}; font-size: 0.72rem; margin-top: 2px; padding-left: 15px; }}
  .bm-dot {{ width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }}
  .bm-node.done {{ border-color: {LIGHT_COLORS["green"]}55; }}
  .bm-node.running {{ border-color: {ACCENT}; box-shadow: 0 0 0 3px {ACCENT}22, 0 4px 14px rgba(0,0,0,.35); }}
  .bm-node.waiting {{ border-color: {LIGHT_COLORS["amber"]}88; }}
  .bm-node.failed {{ border-color: {LIGHT_COLORS["red"]}; }}
  .bm-edge {{ flex: 1 0 18px; height: 1px; background: {BORDER_STRONG}; min-width: 18px; }}
  .bm-edge.done {{ background: {LIGHT_COLORS["green"]}88; }}

  .bm-empty {{
    border: 1px dashed {BORDER_STRONG}; border-radius: 14px; padding: 36px; text-align: center; color: {MUTED};
    background-image: radial-gradient(rgba(255,255,255,0.05) 1px, transparent 1px); background-size: 18px 18px;
  }}
  .bm-empty b {{ color: {TEXT_2}; font-weight: 500; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "") -> str:
    return f'<span class="bm-badge {kind}">{text}</span>'


def pipeline_html(status: dict[str, str]) -> str:
    """Railway-style canvas: stage nodes joined by edges, coloured by status."""
    parts: list[str] = []
    for i, (key, label) in enumerate(PIPELINE):
        st_ = status.get(key, "")
        if i:
            prev = status.get(PIPELINE[i - 1][0], "")
            parts.append(f'<div class="bm-edge {"done" if prev == "done" else ""}"></div>')
        color = STATUS_COLOR.get(st_, FAINT)
        glow = f"box-shadow:0 0 8px {color};" if st_ in ("done", "running") else ""
        parts.append(
            f'<div class="bm-node {st_}"><div class="n">'
            f'<span class="bm-dot" style="background:{color};{glow}"></span>{label}</div>'
            f'<div class="s">{st_ or "pending"}</div></div>'
        )
    return f'<div class="bm-canvas"><div class="bm-strip">{"".join(parts)}</div></div>'


def empty_state(title: str, hint: str) -> None:
    st.markdown(
        f'<div class="bm-empty"><b>{title}</b><br><span class="bm-muted">{hint}</span></div>',
        unsafe_allow_html=True,
    )
