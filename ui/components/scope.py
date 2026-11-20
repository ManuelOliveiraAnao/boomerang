"""Partial migration UI (owner: D, docs/16 §3.4).

Two pieces:
- `choose()`: "Choose what moves" in the What breaks tab, above the approval.
- `render()`: the Placement tab: two lanes (AWS / your servers), move over / move back per
  component, what a hybrid costs and needs, and the history of moves.
"""
from __future__ import annotations

import html
import time

import streamlit as st

from boomerang import cost
from ui import runner
from ui import scope as sc
from ui.data import Source
from ui.theme import (ACCENT, AWS_ORANGE, BG, BG_1, BORDER, BORDER_STRONG, FAINT, LIGHT_COLORS, MUTED,
                      TEXT, TEXT_2)

SH = "#A5A4FF"   # self-hosted lane: Boomerang's accent, so it never reads as a red light

CSS = f"""
<style>
  .sc-kicker {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.72rem; color: {FAINT};
    letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 6px; }}
  .sc-title {{ font-size: 1.35rem; font-weight: 560; letter-spacing: -0.02em; color: {TEXT}; margin: 0 0 4px; }}
  .sc-sub {{ color: {MUTED}; font-size: 0.9rem; line-height: 1.55; max-width: 720px; }}
  .sc-headrow {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin: 6px 0 18px; }}

  .sc-mode {{ display: inline-flex; align-items: center; gap: 8px; padding: 5px 12px; border-radius: 999px;
    border: 1px solid {BORDER_STRONG}; background: {BG_1}; font-size: 0.8rem; color: {TEXT}; white-space: nowrap; }}
  .sc-mode i {{ width: 7px; height: 7px; border-radius: 50%; }}
  .sc-mode.aws i {{ background: {AWS_ORANGE}; box-shadow: 0 0 8px {AWS_ORANGE}; }}
  .sc-mode.selfhosted i {{ background: {SH}; box-shadow: 0 0 8px {SH}; }}
  .sc-mode.hybrid i {{ background: linear-gradient(90deg, {AWS_ORANGE} 50%, {SH} 50%); }}

  /* stat strip */
  .sc-stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid {BORDER};
    border-radius: 14px; background: {BG_1}; margin-bottom: 18px; }}
  .sc-stat {{ padding: 14px 18px; border-left: 1px solid {BORDER}; }}
  .sc-stat:first-child {{ border-left: 0; }}
  .sc-stat span {{ display: block; color: {FAINT}; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 0.68rem; letter-spacing: 0.06em; text-transform: uppercase; }}
  .sc-stat b {{ display: block; color: {TEXT}; font-size: 1.25rem; font-weight: 560; letter-spacing: -0.02em; margin-top: 6px; }}
  .sc-stat em {{ font-style: normal; color: {MUTED}; font-size: 0.78rem; }}

  /* lanes */
  .sc-lane {{ display: flex; align-items: center; justify-content: space-between; padding: 0 2px 8px;
    border-bottom: 1px solid {BORDER}; margin-bottom: 4px; }}
  .sc-lane b {{ display: inline-flex; align-items: center; gap: 8px; color: {TEXT}; font-size: 0.88rem; font-weight: 560; }}
  .sc-lane b i {{ width: 8px; height: 8px; border-radius: 2px; }}
  .sc-lane span {{ color: {FAINT}; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.72rem; }}

  .sc-card {{ position: relative; background: {BG_1}; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 13px 16px 12px 18px; overflow: hidden; }}
  .sc-card::before {{ content: ""; position: absolute; left: 0; top: 12px; bottom: 12px; width: 2px;
    border-radius: 2px; background: var(--lane); }}
  .sc-card .top {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
  .sc-card .nm {{ color: {TEXT}; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.86rem; }}
  .sc-card .svc {{ color: var(--lane); font-size: 0.78rem; font-weight: 500; }}
  .sc-card .meta {{ display: flex; gap: 14px; margin-top: 8px; color: {MUTED}; font-size: 0.78rem; }}
  .sc-card .meta b {{ color: {TEXT_2}; font-weight: 500; }}
  .sc-ghost {{ border: 1px dashed rgba(255,255,255,0.09); border-radius: 12px; padding: 13px 16px; min-height: 66px;
    display: flex; align-items: center; color: {FAINT}; font-size: 0.78rem; }}
  .sc-lock {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 14px;
    border: 1px solid {BORDER}; border-radius: 10px; background: {BG}; color: {MUTED}; font-size: 0.8rem; }}
  .sc-lock .nm {{ font-family: "JetBrains Mono", ui-monospace, monospace; color: {TEXT_2}; font-size: 0.8rem; }}
  .sc-lock i {{ font-style: normal; color: {LIGHT_COLORS["red"]}; font-size: 0.72rem; }}

  /* selectable cards in What breaks (a Streamlit container keyed sc_pick_*) */
  div[class*="st-key-sc_pick_"] {{ transition: border-color .2s, background .2s, box-shadow .2s; }}
  div[class*="st-key-sc_pick_"]:has(input:checked) {{ border-color: {ACCENT}88 !important;
    background: linear-gradient(180deg, {ACCENT}10, {BG_1} 60%) !important; box-shadow: 0 0 0 3px {ACCENT}14; }}
  .sc-pick .top {{ display: flex; align-items: center; justify-content: space-between; }}
  .sc-pick .nm {{ font-family: "JetBrains Mono", ui-monospace, monospace; color: {TEXT}; font-size: 0.9rem; }}
  .sc-route {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 10px 0 4px; font-size: 0.86rem; }}
  .sc-route .a {{ color: {AWS_ORANGE}; font-weight: 500; }}
  .sc-route .s {{ color: {SH}; font-weight: 500; }}
  .sc-route .arr {{ color: {FAINT}; }}
  .sc-route .via {{ color: {FAINT}; font-size: 0.76rem; }}
  .sc-pick .meta {{ color: {MUTED}; font-size: 0.78rem; }}

  .sc-sum {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px 18px; padding: 12px 16px; margin: 6px 0 12px;
    border: 1px solid {BORDER}; border-radius: 12px; background: {BG_1}; color: {MUTED}; font-size: 0.85rem; }}
  .sc-sum b {{ color: {TEXT}; font-weight: 560; }}
  .sc-sum .sep {{ width: 1px; height: 14px; background: {BORDER_STRONG}; }}

  .sc-note {{ display: grid; grid-template-columns: 18px 1fr; gap: 10px; padding: 11px 14px; margin-bottom: 8px;
    border: 1px solid {BORDER}; border-radius: 10px; background: {BG}; color: {TEXT_2}; font-size: 0.84rem; line-height: 1.5; }}
  .sc-note i {{ font-style: normal; width: 18px; height: 18px; border-radius: 5px; display: grid; place-items: center;
    font-size: 0.7rem; font-weight: 700; color: {BG}; background: var(--c); margin-top: 1px; }}
  .sc-note b {{ color: {TEXT}; font-weight: 560; }}

  .sc-preview {{ display: flex; align-items: center; gap: 10px; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 0.72rem; color: {MUTED}; letter-spacing: 0.02em; }}
  .sc-preview b {{ color: {SH}; font-weight: 500; letter-spacing: 0.08em; }}

  /* history */
  .sc-hist {{ border-top: 1px solid {BORDER}; }}
  .sc-h {{ display: grid; grid-template-columns: 74px 120px 1fr auto; align-items: center; gap: 14px;
    padding: 11px 2px; border-bottom: 1px solid {BORDER}; font-size: 0.84rem; color: {TEXT_2}; }}
  .sc-h .t {{ font-family: "JetBrains Mono", ui-monospace, monospace; color: {FAINT}; font-size: 0.74rem; }}
  .sc-h .act {{ font-size: 0.76rem; font-weight: 500; }}
  .sc-h .act.over {{ color: {SH}; }}
  .sc-h .act.back {{ color: {AWS_ORANGE}; }}
  .sc-h .c {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.8rem; }}
  .sc-h .r {{ font-size: 0.76rem; color: {FAINT}; text-align: right; }}
  .sc-h .r.ok {{ color: {LIGHT_COLORS["green"]}; }}
  .sc-h .r.bad {{ color: {LIGHT_COLORS["red"]}; }}
  .sc-empty {{ color: {FAINT}; font-size: 0.84rem; padding: 14px 2px; border-top: 1px solid {BORDER}; }}

  @media (max-width: 900px) {{
    .sc-stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .sc-stat:nth-child(3) {{ border-left: 0; }}
    .sc-stat:nth-child(n+3) {{ border-top: 1px solid {BORDER}; }}
    .sc-headrow {{ flex-direction: column; align-items: flex-start; }}
    .sc-h {{ grid-template-columns: 60px 1fr; }}
  }}
</style>
"""

DEMO_DATA_GB = {"kv_table": "stored_gb_db", "object_store": "stored_gb_objects"}


@st.cache_data
def _pricing():
    return cost.load_pricing()


def _gb(kind: str) -> float | None:
    _, usage, _ = _pricing()
    key = DEMO_DATA_GB.get(kind)
    return float(usage.get(key, 0)) if key else None


def _eur(x: float | None) -> str:
    return "–" if x is None else f"€{x:,.2f}"


def _route(kind: str) -> str:
    aws, target, via = sc.SERVICE[kind]
    return (f'<div class="sc-route"><span class="a">{aws}</span><span class="arr">→</span>'
            f'<span class="s">{target}</span><span class="via">{via}</span></div>')


def _notes(rows: list[dict], reds: list[dict], chosen: set[str], ids: list[str], assessment: dict) -> None:
    """What a placement implies: credentials, dependencies, consistency."""
    kept = [i for i in ids if i not in chosen]
    out = []
    if chosen and kept:
        out.append((LIGHT_COLORS["red"], "!", f"<b>AWS credentials on your servers.</b> The app keeps calling AWS for "
                    f"<code>{', '.join(kept)}</code>, so it needs credentials scoped to just those services. "
                    "They are yours: Boomerang never generates them or sends them to the AI."))
        out.append((LIGHT_COLORS["amber"], "~", "<b>Two sides, no shared transactions.</b> Each service stays consistent "
                    "on its own; a write that spans both sides is not atomic while the app is hybrid. Traffic back to "
                    "AWS adds latency and is billed as egress (see Costs)."))
    kinds = {r["id"]: r["kind"] for r in rows}
    if any(kinds.get(c) == "object_store" for c in chosen):
        hooks = sorted({f["component_id"] for f in assessment.get("findings", [])
                        if f.get("feature") == "event_notification"})
        if hooks:
            out.append((LIGHT_COLORS["red"], "!", f"<b>Bucket events stop firing.</b> Once the bucket moves, its event "
                        f"notifications to <code>{', '.join(hooks)}</code> no longer run. That function stays on AWS "
                        "and needs a person to rewire it."))
    for c, sym, text in out:
        st.markdown(f'<div class="sc-note" style="--c:{c}"><i>{sym}</i><div>{text}</div></div>',
                    unsafe_allow_html=True)


def _summary_bar(rows: list[dict], chosen: set[str], ids: list[str]) -> str:
    key, words = sc.label(chosen, ids)
    gb = sum(_gb(r["kind"]) or 0 for r in rows if r["id"] in chosen)
    kept = [i for i in ids if i not in chosen]
    parts = [f'<span class="sc-mode {key}"><i></i>{words}</span>',
             f'<span><b>{len(chosen)} of {len(ids)}</b> services move</span>']
    if chosen:
        parts.append(f'<span><b>{gb:g} GB</b> of data to copy</span>')
    if kept:
        parts.append(f'<span>stays on AWS: <b>{html.escape(", ".join(kept))}</b></span>')
    return '<div class="sc-sum">' + '<span class="sep"></span>'.join(parts) + "</div>"


# ---------------------------------------------------------------- What breaks: choose what moves
def choose(src: Source, rows: list[dict], reds: list[dict], assessment: dict, *, editable: bool,
           initial: list[str] | None = None) -> list[str]:
    """Selectable cards for the movable components; returns the chosen ids."""
    st.markdown(CSS, unsafe_allow_html=True)
    mov = sc.movable(rows)
    ids = [r["id"] for r in mov]
    if not mov:
        return []
    st.markdown('<div class="sc-kicker">Scope</div><div class="sc-title">Choose what moves</div>'
                '<div class="sc-sub">Move everything at once, or start with the services that are easiest to '
                'move. Each one moves whole, with its data. Whatever you leave keeps running on AWS, and you '
                'can move it later, or bring a moved one back.</div>', unsafe_allow_html=True)
    st.write("")
    start = set(ids if initial is None else initial)
    chosen: list[str] = []
    cols = st.columns(len(mov))
    for col, r in zip(cols, mov):
        with col, st.container(border=True, key=f"sc_pick_{r['id']}"):
            light = r["light"]
            gb = _gb(r["kind"])
            feats = len(r["features"])
            meta = f"{feats} feature{'s' if feats != 1 else ''}" + (f" · {gb:g} GB" if gb is not None else "")
            st.markdown(
                f'<div class="sc-pick"><div class="top"><span class="nm">{html.escape(r["id"])}</span>'
                f'<span style="color:{LIGHT_COLORS[light]};font-size:0.74rem">● {"config only" if light == "green" else "adapter"}</span></div>'
                f'{_route(r["kind"])}<div class="meta">{meta}</div></div>', unsafe_allow_html=True)
            if st.checkbox("Move to your servers", value=r["id"] in start, disabled=not editable,
                           key=f"sc_sel_{src.path}_{r['id']}"):
                chosen.append(r["id"])
    if reds:
        locked = " · ".join(f'<span class="nm">{html.escape(r["id"])}</span>' for r in reds)
        st.markdown(f'<div class="sc-lock" style="margin-top:4px"><span>Stays on AWS, needs a human: {locked}</span>'
                    f'<i>● not movable</i></div>', unsafe_allow_html=True)
    st.markdown(_summary_bar(mov, set(chosen), ids), unsafe_allow_html=True)
    _notes(rows, reds, set(chosen), ids, assessment)
    return chosen


# ---------------------------------------------------------------- Placement tab
def _lane_card(r: dict, side: str, per: dict) -> str:
    aws, target, via = sc.SERVICE[r["kind"]]
    lane = AWS_ORANGE if side == "aws" else SH
    svc = aws if side == "aws" else f"{target} · {via}"
    gb = _gb(r["kind"])
    eur = per["components"][r["kind"]]["aws" if side == "aws" else "selfhosted"]
    data = f'<span><b>{gb:g} GB</b> data</span>' if gb is not None else '<span><b>messages</b> in flight</span>'
    return (f'<div class="sc-card" style="--lane:{lane}"><div class="top"><span class="nm">{html.escape(r["id"])}</span>'
            f'<span class="svc">{svc}</span></div><div class="meta">{data}'
            f'<span><b>{_eur(eur)}</b> / month here</span></div></div>')


def _history(src: Source) -> None:
    hist = sc.steps(src)
    st.markdown('<div class="sc-kicker" style="margin-top:22px">History</div>'
                '<div class="sc-title" style="font-size:1.05rem">Moves</div>', unsafe_allow_html=True)
    if not hist:
        st.markdown('<div class="sc-empty">No moves yet. The approved scope is the starting point.</div>',
                    unsafe_allow_html=True)
        return
    out = []
    for s in reversed(hist):
        over = s.get("action") == "move_over"
        when = time.strftime("%H:%M:%S", time.localtime(s.get("at", 0)))
        ok = s.get("verification_ok")
        if s.get("preview"):
            res, cls = "preview · not run", ""
        elif ok is True:
            res, cls = "verified ✓", "ok"
        elif ok is False:
            res, cls = "verification failed", "bad"
        else:
            res, cls = "running", ""
        moves = s.get("data_moves") or []
        counts = " · ".join(
            f'{"→" if m.get("direction", "").startswith("aws") else "←"} '
            f'{c.get("component_id") or c.get("component", "")} {c.get("source_count", "?")}/{c.get("target_count", "?")}'
            for m in moves for c in (m.get("counts") or []))
        comps = html.escape(", ".join(s.get("components", [])))
        out.append(f'<div class="sc-h"><span class="t">{when}</span>'
                   f'<span class="act {"over" if over else "back"}">{"Move over →" if over else "← Move back"}</span>'
                   f'<span class="c">{comps}{f" <span class=t>{counts}</span>" if counts else ""}</span>'
                   f'<span class="r {cls}">{res}</span></div>')
    st.markdown('<div class="sc-hist">' + "".join(out) + "</div>", unsafe_allow_html=True)


def render(src: Source, rows: list[dict], assessment: dict) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    mov = sc.movable(rows)
    ids = [r["id"] for r in mov]
    reds = [r for r in rows if r["light"] == "red"]
    if not mov:
        st.info("No movable services found in this run.")
        return
    mode = sc.mode(src)
    here = sc.placement(src, ids)
    key, words = sc.label(here, ids)
    by_kind = {r["id"]: r["kind"] for r in mov}

    prices, usage, ops = _pricing()
    moved_kinds = sc.kinds(mov, here)
    scen = cost.scenario(prices, usage, ops, moved_kinds)
    base = cost.scenario(prices, usage, ops, ())
    per = cost.per_component(prices, usage, ops)

    st.markdown(
        f'<div class="sc-headrow"><div><div class="sc-kicker">Placement</div>'
        f'<div class="sc-title">Where each service runs</div>'
        f'<div class="sc-sub">Move services one at a time and bring any of them back. Each move rewrites only the '
        f'code that touches that service, verifies the app with its original tests, and copies that '
        f'service\'s data with counts checked.</div></div>'
        f'<span class="sc-mode {key}"><i></i>{words}</span></div>', unsafe_allow_html=True)

    gb = sum(_gb(by_kind[i]) or 0 for i in here)
    delta = None if scen["total"] is None or base["total"] is None else scen["total"] - base["total"]
    delta_txt = "" if delta is None or key == "aws" else f'{"+" if delta >= 0 else "−"}{_eur(abs(delta))} vs all on AWS'
    creds = "Yes" if key == "hybrid" else "No"
    st.markdown(
        '<div class="sc-stats">'
        f'<div class="sc-stat"><span>Self-hosted</span><b>{len(here)} / {len(ids)}</b><em>movable services</em></div>'
        f'<div class="sc-stat"><span>Data on your servers</span><b>{gb:g} GB</b><em>copied with checksums</em></div>'
        f'<div class="sc-stat"><span>Monthly, this placement</span><b>{_eur(scen["total"])}</b><em>{delta_txt or "default usage, see Costs"}</em></div>'
        f'<div class="sc-stat"><span>AWS credentials needed</span><b>{creds}</b><em>{"while hybrid" if creds == "Yes" else "nothing calls AWS" if key == "selfhosted" else "app stays on AWS"}</em></div>'
        '</div>', unsafe_allow_html=True)

    if mode == "preview":
        p1, p2 = st.columns([5, 1], vertical_alignment="center")
        p1.markdown('<div class="sc-preview"><b>PREVIEW</b> Demo data: moves are simulated in this session. '
                    'Nothing is rewritten, booted or copied.</div>', unsafe_allow_html=True)
        if p2.button("Reset", key="sc_reset", width="stretch"):
            sc.reset_preview(src)
            st.rerun()
    elif mode == "readonly":
        why = ("this is a recorded run" if src.kind == "run" and src.path is not None
               and src.path.name != st.session_state.get("live_run")
               else "the engine has no scope support yet (C: boomerang/scope.py)")
        st.markdown(f'<div class="sc-preview"><b>READ ONLY</b> Moves are disabled: {why}.</div>',
                    unsafe_allow_html=True)
    st.write("")

    step = st.session_state.get("step")
    busy = bool(step and not step.done)
    # Live moves need a passed run whose self-hosted stack is up, and nothing else running.
    blocked = mode == "readonly" or busy or (mode == "live" and src.summary.get("status") != "passed")
    if mode == "live" and blocked and not busy:
        st.markdown('<div class="sc-preview"><b>WAITING</b> Moves open once the run has passed and its stack '
                    'is up.</div>', unsafe_allow_html=True)
    h1, _, h2 = st.columns([5, 2, 5])
    n_aws = len(ids) - len(here) + len(reds)
    h1.markdown(f'<div class="sc-lane"><b><i style="background:{AWS_ORANGE}"></i>AWS</b><span>{n_aws} services · eu-central-1</span></div>',
                unsafe_allow_html=True)
    h2.markdown(f'<div class="sc-lane"><b><i style="background:{SH}"></i>Your servers</b><span>{len(here)} services · Leaseweb example</span></div>',
                unsafe_allow_html=True)
    for r in mov:
        on_sh = r["id"] in here
        a, m, b = st.columns([5, 2, 5], vertical_alignment="center")
        a.markdown(_lane_card(r, "aws", per) if not on_sh else '<div class="sc-ghost">moved to your servers</div>',
                   unsafe_allow_html=True)
        b.markdown(_lane_card(r, "selfhosted", per) if on_sh else '<div class="sc-ghost">runs on AWS</div>',
                   unsafe_allow_html=True)
        action = "move_back" if on_sh else "move_over"
        label = "← Move back" if on_sh else "Move over →"
        # Moving the last self-hosted service back is the round trip, not a step.
        last = on_sh and len(here) == 1
        if m.button(label, key=f"sc_mv_{r['id']}", width="stretch", disabled=blocked or last,
                    help="Bringing the last one back is the round trip (Data & round trip)." if last else None):
            if mode == "preview":
                sc.preview_move(src, ids, [r["id"]], action)
            else:
                st.session_state.step = runner.start(action.replace("_", "-"), src.path.name, components=[r["id"]])
            st.rerun()
    for r in reds:
        a, _, b = st.columns([5, 2, 5], vertical_alignment="center")
        aws_name = {"function": "Lambda", "identity": "IAM", "monitoring": "CloudWatch"}.get(r["kind"], "AWS")
        a.markdown(f'<div class="sc-lock"><span class="nm">{html.escape(r["id"])}</span><span>{aws_name}</span>'
                   f'<i>● needs a human</i></div>', unsafe_allow_html=True)
        b.markdown('<div class="sc-ghost" style="min-height:0;padding:9px 14px">not movable</div>',
                   unsafe_allow_html=True)

    st.write("")
    _notes(rows, reds, here, ids, assessment)
    _history(src)
