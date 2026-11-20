"""Shared colours, labels and CSS for the Boomerang UI (owner: D).

Look: Linear-style neutral dark base (near-black, Inter, hairline translucent
borders, muted grey text, white pill primary button) with a Railway-style
dotted canvas for the pipeline, where each stage is a connected node card.
"""
from __future__ import annotations

from pathlib import Path

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
  .stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] {{
    background: #E5E5E6 !important; color: {BG} !important; border-color: #E5E5E6 !important;
    box-shadow: 0 1px 2px rgba(0,0,0,.4);
  }}
  .stButton > button[kind="primary"] *, button[data-testid="stBaseButton-primary"] * {{ color: {BG} !important; }}
  .stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {{ background: #fff !important; }}
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


ASSETS = Path(__file__).resolve().parent / "assets"
FAVICON = str(ASSETS / "favicon.png")


def logo_svg(height: int = 22, mono: str | None = None) -> str:
    """Inline pixel logo (generated by ui/assets/make_logo.py)."""
    name = {"#FFFFFF": "logo-white.svg", "#000000": "logo-black.svg"}.get(mono or "", "logo.svg")
    svg = (ASSETS / name).read_text(encoding="utf-8")
    return svg.replace("<svg ", f'<svg height="{height}" style="display:block" ', 1)


def wordmark_svg(height: int = 26, dark_text: bool = False) -> str:
    """Inline wordmark: pixel mark + "Boomerang" (Inter SemiBold as outlines)."""
    svg = (ASSETS / ("wordmark-dark.svg" if dark_text else "wordmark.svg")).read_text(encoding="utf-8")
    svg = svg.replace('width="', 'data-w="', 1).replace('height="', 'data-h="', 1)
    return svg.replace("<svg ", f'<svg height="{height}" style="display:block;width:auto" ', 1)


BRAND_HTML = (
    '<div style="margin:4px 0 18px">'
    f'{wordmark_svg(26)}'
    '<div style="font-size:0.75rem;color:#8A8F98;margin:6px 0 0 2px">AWS ⇄ Leaseweb</div></div>'
)

_LANDING_CSS = f"""
<style>
  .bm-apptitle {{ font-size: 1.6rem; font-weight: 560; letter-spacing: -0.025em; color: {TEXT}; margin: 12px 0 0; }}

  .lp-hero {{ text-align: center; padding: 56px 0 8px; position: relative; }}
  .lp-hero::before {{
    content: ""; position: absolute; inset: -40px -10% auto; height: 420px; z-index: -1;
    background: radial-gradient(ellipse at 50% 0%, {ACCENT}33, transparent 60%);
  }}
  .lp-mark {{ display: inline-block; margin-bottom: 18px; padding: 24px; cursor: grab;
    filter: drop-shadow(0 10px 30px {ACCENT}55); }}
  .lp-mark .bm-boom {{ display: inline-block; will-change: transform; }}
  .lp-hero .bm-title {{ font-size: clamp(2.4rem, 5.5vw, 4.2rem); margin: 18px auto 14px; }}
  .lp-hero .bm-sub {{ margin: 0 auto; font-size: 1.12rem; max-width: 680px; line-height: 1.55; }}

  .lp-section {{ padding: 64px 0 8px; }}
  .lp-kicker {{ color: {MUTED}; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.78rem;
    font-weight: 400; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px; }}
  .lp-h2 {{ font-size: clamp(1.7rem, 3vw, 2.4rem); font-weight: 560; letter-spacing: -0.03em; color: {TEXT}; line-height: 1.12; margin: 0 0 10px; }}
  .lp-lead {{ color: {MUTED}; font-size: 1.02rem; max-width: 720px; line-height: 1.6; margin-bottom: 26px; }}

  .lp-grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }}
  .lp-card {{
    background: {BG_1}; border: 1px solid {BORDER}; border-radius: 14px; padding: 20px;
    transition: border-color .2s, transform .2s;
  }}
  .lp-card:hover {{ border-color: {BORDER_STRONG}; transform: translateY(-1px); }}
  .lp-card h3 {{ font-size: 1rem !important; margin: 10px 0 6px !important; color: {TEXT} !important; }}
  .lp-card p {{ color: {MUTED}; font-size: 0.9rem; line-height: 1.55; margin: 0; }}
  .lp-icon {{
    width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center;
    background: {BG_2}; border: 1px solid {BORDER_STRONG}; font-size: 15px;
  }}

  .lp-stats {{ display: grid; gap: 1px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    background: {BORDER}; border: 1px solid {BORDER}; border-radius: 14px; overflow: hidden; margin-top: 40px; }}
  .lp-stat {{ background: {BG}; padding: 18px 20px; }}
  .lp-stat b {{ display: block; color: {TEXT}; font-size: 1.35rem; font-weight: 560; letter-spacing: -0.02em; }}
  .lp-stat span {{ color: {MUTED}; font-size: 0.82rem; }}


  /* Traffic lights: a rules board, one column per light, details pop up on hover / focus. */
  .lt-board {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; align-items: stretch; }}
  .lt-col {{ position: relative; background: {BG_1}; border: 1px solid {BORDER}; border-radius: 14px; padding: 20px 8px 10px;
    transition: opacity .25s ease, border-color .25s ease; }}
  .lt-col::before {{ content: ""; position: absolute; left: 20px; right: 20px; top: -1px; height: 1px;
    background: linear-gradient(90deg, transparent, var(--c), transparent); opacity: .7; }}
  .lt-board:hover .lt-col:not(:hover) {{ opacity: .45; }}
  .lt-col:hover {{ border-color: {BORDER_STRONG}; z-index: 5; }}
  .lt-head {{ display: flex; align-items: center; gap: 14px; padding: 0 12px; }}
  .lt-signal {{ display: inline-flex; flex-direction: column; gap: 4px; padding: 6px 5px; border-radius: 8px;
    background: {BG}; border: 1px solid {BORDER}; }}
  .lt-signal i {{ width: 8px; height: 8px; border-radius: 50%; background: rgba(255,255,255,0.08); }}
  .lt-signal i.lit {{ background: var(--c); box-shadow: 0 0 10px var(--c); }}
  .lt-label {{ color: {TEXT}; font-weight: 560; font-size: 1.02rem; letter-spacing: -0.01em; }}
  .lt-count {{ color: {FAINT}; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.72rem; margin-top: 2px; }}
  .lt-blurb {{ color: {MUTED}; font-size: 0.88rem; line-height: 1.55; margin: 14px 12px 14px; min-height: 2.9em; }}
  .lt-rows {{ border-top: 1px solid {BORDER}; padding-top: 6px; }}
  .lt-row {{ position: relative; display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 8px;
    cursor: default; outline: none; transition: background .2s ease; }}
  .lt-row:hover, .lt-row:focus {{ background: rgba(255,255,255,0.035); }}
  .lt-row:focus-visible {{ box-shadow: inset 0 0 0 1px {BORDER_STRONG}; }}
  .lt-svc {{ flex: 0 0 auto; min-width: 66px; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.7rem;
    color: {MUTED}; }}
  .lt-feat {{ flex: 1; color: {TEXT_2}; font-size: 0.88rem; line-height: 1.4; }}
  .lt-chev {{ color: {FAINT}; font-size: 1rem; transition: transform .2s ease, color .2s ease; }}
  .lt-row:hover .lt-chev, .lt-row:focus .lt-chev {{ color: var(--c); transform: translateX(2px); }}

  .lt-pop {{ position: absolute; left: 4px; width: min(400px, 88vw); bottom: calc(100% + 8px); z-index: 20; text-align: left;
    background: #131416; border: 1px solid {BORDER_STRONG}; border-radius: 12px; padding: 14px 16px 6px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(0,0,0,0.4);
    opacity: 0; visibility: hidden; transform: translate3d(0, 6px, 0) scale(.98); transform-origin: 30% 100%;
    transition: opacity .18s ease, transform .22s cubic-bezier(.215,.61,.355,1), visibility 0s linear .22s;
    pointer-events: none; }}
  .lt-pop::after {{ content: ""; position: absolute; left: 28px; bottom: -6px; width: 10px; height: 10px;
    background: #131416; border-right: 1px solid {BORDER_STRONG}; border-bottom: 1px solid {BORDER_STRONG}; transform: rotate(45deg); }}
  .lt-col:last-child .lt-pop {{ left: auto; right: 4px; transform-origin: 70% 100%; }}
  .lt-col:last-child .lt-pop::after {{ left: auto; right: 28px; }}
  .lt-row:hover .lt-pop, .lt-row:focus .lt-pop {{ opacity: 1; visibility: visible; transform: none;
    transition: opacity .18s ease, transform .22s cubic-bezier(.215,.61,.355,1), visibility 0s; }}
  .lt-pop-h {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
  .lt-pop-l {{ display: inline-flex; align-items: center; gap: 8px; color: var(--c); font-size: 0.74rem; font-weight: 500; }}
  .lt-pop-l::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 8px var(--c); }}
  .lt-pop-s {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.7rem; color: {FAINT}; }}
  .lt-pop-t {{ color: {TEXT}; font-weight: 560; font-size: 0.98rem; margin-bottom: 8px; }}
  .lt-kv {{ display: grid; grid-template-columns: 104px 1fr; gap: 10px; padding: 8px 0; border-top: 1px solid {BORDER}; }}
  .lt-kv span {{ color: {FAINT}; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em;
    font-family: "JetBrains Mono", ui-monospace, monospace; padding-top: 2px; }}
  .lt-kv p {{ margin: 0; color: {TEXT_2}; font-size: 0.84rem; line-height: 1.5; }}
  .lt-kv code {{ font-size: 0.76rem; color: {TEXT}; background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; padding: 1px 6px; }}
  .lt-note {{ margin-top: 16px; color: {FAINT}; font-size: 0.82rem; }}
  .lt-note span {{ color: {TEXT_2}; }}
  @media (max-width: 900px) {{
    .lt-board {{ grid-template-columns: 1fr; }}
    .lt-board:hover .lt-col:not(:hover) {{ opacity: 1; }}
    .lt-blurb {{ min-height: 0; }}
    /* No room to float: the details open inline under the tapped rule. */
    .lt-row {{ flex-wrap: wrap; }}
    .lt-pop {{ position: static; flex-basis: 100%; width: auto; display: none; box-shadow: none; margin-top: 8px; transform: none; }}
    .lt-pop::after {{ display: none; }}
    .lt-row:focus .lt-pop {{ display: block; }}
  }}

  .lp-trip {{ display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; }}
  .lp-stop {{ flex: 1 1 200px; background: {BG_2}; border: 1px solid {BORDER_STRONG}; border-radius: 12px; padding: 16px;
    box-shadow: 0 6px 20px rgba(0,0,0,.35); }}
  .lp-stop .where {{ font-weight: 600; font-size: 1.05rem; }}
  .lp-stop .what {{ color: {MUTED}; font-size: 0.85rem; margin-top: 6px; line-height: 1.5; }}
  .lp-arrow {{ flex: 0 0 56px; display: grid; place-items: center; color: {FAINT}; font-size: 1.2rem; }}

  .lp-list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
  .lp-list li {{ display: flex; gap: 10px; align-items: flex-start; color: {TEXT_2}; font-size: 0.92rem; line-height: 1.5;
    background: {BG_1}; border: 1px solid {BORDER}; border-radius: 12px; padding: 14px 16px; }}
  .lp-list li i {{ font-style: normal; display: inline-flex; margin-top: 1px; }}
  .lp-list li i .bm-st {{ margin-right: 0; }}

  .lp-chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .lp-chip {{ border: 1px solid {BORDER}; background: {BG_1}; color: {TEXT_2}; border-radius: 999px; padding: 4px 12px; font-size: 0.82rem; }}

  /* How it works: pinned heading on the left, a rail with a travelling boomerang on the right. */
  .lp-tl {{ display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); column-gap: 72px; align-items: start; }}
  .tl-side {{ position: sticky; top: 88px; padding-top: 6px; }}
  .tl-side .lp-lead {{ margin-bottom: 40px; }}
  .tl-read {{ display: flex; align-items: baseline; gap: 10px; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-variant-numeric: tabular-nums; }}
  .tl-cur {{ font-size: 4.25rem; line-height: 1; letter-spacing: -0.04em; color: {TEXT}; }}
  .tl-of {{ font-size: 0.95rem; color: {FAINT}; }}
  .tl-name {{ color: {TEXT_2}; font-size: 1rem; margin: 10px 0 18px; min-height: 1.5em; }}
  .tl-name.flip, .tl-cur.flip {{ animation: tlflip .42s cubic-bezier(.215,.61,.355,1); }}
  @keyframes tlflip {{ from {{ opacity: 0; transform: translateY(6px); }} }}
  .tl-bar {{ height: 2px; background: {BORDER}; border-radius: 2px; overflow: hidden; margin-bottom: 22px; }}
  .tl-bar i {{ display: block; height: 100%; width: 0; background: {ACCENT}; }}
  .tl-phases {{ border-top: 1px solid {BORDER}; }}
  .tl-phase {{ display: flex; justify-content: space-between; align-items: center; padding: 11px 0;
    border-bottom: 1px solid {BORDER}; transition: color .3s; }}
  .tl-phase b {{ font-weight: 500; font-size: 0.9rem; color: {FAINT}; display: flex; align-items: center; gap: 10px; transition: color .3s; }}
  .tl-phase b::before {{ content: ""; width: 6px; height: 6px; border-radius: 1px; background: {BORDER_STRONG}; transition: background .3s; }}
  .tl-phase span {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.75rem; color: {FAINT}; }}
  .tl-phase.on b {{ color: {TEXT}; }}
  .tl-phase.on b::before {{ background: {ACCENT}; }}

  .tl-list {{ position: relative; padding: 6px 0 0 64px; }}
  .tl-rail {{ position: absolute; left: 19.5px; top: 13px; bottom: 0; width: 1px; background: {BORDER_STRONG}; }}
  .tl-fill {{ position: absolute; left: -0.5px; top: 0; width: 2px; height: 0;
    background: linear-gradient(180deg, {ACCENT}00, {ACCENT} 40%); display: none; }}
  .tl-mark {{ position: absolute; left: -2.5px; top: 0; width: 44px; height: 44px; display: none; place-items: center;
    border-radius: 50%; background: {BG}; border: 1px solid {BORDER_STRONG}; box-shadow: 0 0 0 6px {BG}, 0 0 24px {ACCENT}40;
    z-index: 2; will-change: transform; }}
  .tl-boom {{ display: block; will-change: transform; }}
  .tl-step {{ position: relative; min-height: 34vh; padding-bottom: 48px; }}
  .tl-step:last-child {{ min-height: 0; padding-bottom: 8px; }}
  .tl-node {{ position: absolute; left: -49px; top: 8px; width: 9px; height: 9px; border-radius: 50%;
    background: {BG}; border: 1px solid {FAINT}; transition: background .35s, border-color .35s, box-shadow .35s; }}
  .tl-meta {{ display: flex; align-items: center; gap: 14px; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 0.74rem; margin-bottom: 10px; }}
  .tl-n {{ color: {FAINT}; }}
  .tl-kind {{ color: {MUTED}; letter-spacing: 0.02em; }}
  .tl-kind.ai {{ color: #A5A4FF; }}
  .tl-title {{ font-size: 1.45rem; font-weight: 560; letter-spacing: -0.02em; color: {TEXT}; line-height: 1.2; margin-bottom: 8px; }}
  .tl-step p {{ color: {MUTED}; font-size: 0.98rem; line-height: 1.6; max-width: 470px; margin: 0 0 16px; }}
  .tl-out {{ display: inline-flex; align-items: center; gap: 10px; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.75rem; }}
  .tl-out span {{ color: {FAINT}; text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.68rem; }}
  .tl-out code {{ color: {TEXT_2}; background: {BG_1}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; font-size: 0.75rem; }}

  /* Scroll states, only when the motion script runs. */
  html.bm-motion .tl-fill {{ display: block; }}
  html.bm-motion .tl-mark {{ display: grid; }}
  html.bm-motion .tl-step {{ opacity: 0; transform: translate3d(0, 28px, 0);
    transition: opacity .7s cubic-bezier(.215,.61,.355,1), transform .9s cubic-bezier(.215,.61,.355,1); }}
  html.bm-motion .tl-step.seen {{ opacity: .3; transform: none; }}
  html.bm-motion .tl-step.on {{ opacity: 1; }}
  html.bm-motion .tl-step.on .tl-node {{ background: {ACCENT}; border-color: {ACCENT}; }}
  html.bm-motion .tl-step.on .tl-n {{ color: #A5A4FF; }}
  html.bm-motion .tl-step.is-cur .tl-node {{ box-shadow: 0 0 0 5px {ACCENT}26; }}

  @media (max-width: 900px) {{
    .lp-tl {{ grid-template-columns: 1fr; }}
    .tl-side {{ position: static; margin-bottom: 28px; }}
    .tl-read, .tl-name, .tl-bar, .tl-phases {{ display: none; }}
    .tl-step {{ min-height: 0; }}
  }}

  /* Problem: four open columns with thin dividers, isometric line art above each title. */
  .lp-problem {{ display: grid; grid-template-columns: repeat(4, 1fr); margin-top: 18px; }}
  .lp-prob {{ padding: 4px 28px 8px; border-left: 1px solid {BORDER}; }}
  .lp-prob:first-child {{ padding-left: 0; border-left: 0; }}
  .lp-prob .art {{ height: 190px; display: flex; align-items: center; justify-content: center; margin-bottom: 26px; }}
  .lp-prob .art svg {{ height: 150px; width: auto; max-width: 100%; overflow: visible; }}
  .lp-prob h3 {{ font-size: 1.05rem !important; font-weight: 560 !important; color: {TEXT} !important; margin: 0 0 10px !important; padding: 0 !important; }}
  .lp-prob p {{ color: {MUTED}; font-size: 0.93rem; line-height: 1.6; margin: 0; max-width: 300px; }}
  :is(.lp-prob, .lp-card) h3 a[aria-label="Link to heading"] {{ display: none; }}
  @media (max-width: 900px) {{
    .lp-problem {{ grid-template-columns: repeat(2, 1fr); row-gap: 36px; }}
    .lp-prob:nth-child(3) {{ padding-left: 0; border-left: 0; }}
  }}
  @media (max-width: 560px) {{
    .lp-problem {{ grid-template-columns: 1fr; }}
    .lp-prob {{ padding-left: 0; border-left: 0; }}
  }}

  .lp-cta {{ text-align: center; padding: 56px 20px; margin-top: 64px; border-radius: 18px; border: 1px solid {BORDER};
    background-color: {BG_1}; background-image: radial-gradient(rgba(255,255,255,0.07) 1px, transparent 1px); background-size: 18px 18px; }}
  .lp-foot {{ color: {FAINT}; font-size: 0.8rem; text-align: center; padding: 28px 0 10px; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_LANDING_CSS, unsafe_allow_html=True)
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(ICON_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- icons
# One small stroke icon set (24px grid, 2px round strokes) instead of emoji, so status marks
# look the same on every OS and match the rest of the UI. Inline SVG: use in HTML strings.
_ICON_PATHS = {
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "x": '<path d="M7 7l10 10M17 7L7 17"/>',
    "minus": '<path d="M7 12h10"/>',
    "alert": '<path d="M12 8v5"/><path d="M12 16.5v.01"/>',
}


def icon(name: str, color: str = "currentColor", size: int = 14, stroke: float = 2.2) -> str:
    """A bare stroke icon (check, x, minus, alert)."""
    return (f'<svg class="bm-ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_ICON_PATHS[name]}</svg>')


def status_icon(ok: bool | None, size: int = 18) -> str:
    """Check in a soft green disc, cross in red, dash in grey when not checked (ok is None)."""
    name, color = {True: ("check", LIGHT_COLORS["green"]), False: ("x", LIGHT_COLORS["red"]),
                   None: ("minus", FAINT)}[ok]
    label = {True: "passed", False: "failed", None: "not checked"}[ok]
    return (f'<span class="bm-st" role="img" aria-label="{label}" style="--c:{color};width:{size}px;height:{size}px">'
            f'{icon(name, color, round(size * 0.62), 2.6)}</span>')


ICON_CSS = f"""
<style>
  .bm-ic {{ display: inline-block; vertical-align: -2px; flex: 0 0 auto; }}
  .bm-st {{ display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; border-radius: 50%;
    vertical-align: -4px; background: color-mix(in srgb, var(--c) 14%, transparent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 38%, transparent); margin-right: 8px; }}
  .bm-st .bm-ic {{ vertical-align: 0; }}
  .bm-check {{ display: flex; align-items: flex-start; gap: 0; margin: 6px 0; color: {TEXT_2}; font-size: 0.9rem;
    line-height: 1.45; }}
  .bm-check .bm-st {{ margin-top: 1px; }}
  .bm-check b {{ color: {TEXT}; font-weight: 500; margin-right: 6px; white-space: nowrap; }}
</style>
"""


def check_row(ok: bool | None, title: str, detail: str = "") -> str:
    """One status line: icon, bold title, muted detail (all escaped by the caller if needed)."""
    return (f'<div class="bm-check">{status_icon(ok)}<b>{title}</b>'
            f'<span class="bm-muted">{detail}</span></div>')


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
