"""Doctor tab (owner D, docs/18 §6): incident response on the self-hosted stack.

Reads runs/<id>/doctor.json (an `Incident`, docs/18 §2). The rules name the cause and the fixes; the AI only
explains; a person approves each fix. Three modes, like the Placement tab:

- preview:  demo data (fixtures/doctor-demo.json). Fixes are simulated in this session; "move back" also
            updates the Placement preview.
- live:     the run started here and the engine has C's orchestrator (`boomerang doctor`): buttons start
            `boomerang doctor`, `doctor fix` and `doctor break` through ui.runner.
- readonly: anything else (a recorded run, or the orchestrator isn't installed yet).
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

import streamlit as st

from ui import runner
from ui import scope as sc
from ui.components import scope as scope_ui
from ui.data import FIXTURES_DIR, Source, mask_secrets
from ui.theme import (AWS_ORANGE, BG, BG_1, BORDER, BORDER_STRONG, FAINT, LIGHT_COLORS, MUTED, TEXT, TEXT_2,
                      status_icon)

DEMO_INCIDENT = FIXTURES_DIR / "doctor-demo.json"
RED, AMBER, GREEN = LIGHT_COLORS["red"], LIGHT_COLORS["amber"], LIGHT_COLORS["green"]
SEVERITY = {"down": ("Down", RED), "degraded": ("Degraded", AMBER)}
SOURCE_LABEL = {"container": "Container", "probe": "Port", "health": "Health", "logs": "Logs", "queue": "Queue"}
SERVICES = ("postgres", "rabbitmq", "seaweedfs", "api", "worker")

CSS = f"""
<style>
  .dr-hero {{ position: relative; border: 1px solid {BORDER}; border-radius: 16px; background: {BG_1};
    padding: 20px 22px 18px 26px; overflow: hidden; margin-bottom: 18px; }}
  .dr-hero::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--c); }}
  .dr-hero::after {{ content: ""; position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(ellipse at 0% 0%, color-mix(in srgb, var(--c) 10%, transparent), transparent 55%); }}
  .dr-top {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; position: relative; z-index: 1; }}
  .dr-sev {{ display: inline-flex; align-items: center; gap: 7px; padding: 3px 10px; border-radius: 6px; font-size: 0.72rem;
    font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--c);
    background: color-mix(in srgb, var(--c) 12%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 35%, transparent); }}
  .dr-sev i {{ width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 8px var(--c);
    animation: dr-pulse 1.6s ease-in-out infinite; }}
  @keyframes dr-pulse {{ 50% {{ opacity: .35; }} }}
  @media (prefers-reduced-motion: reduce) {{ .dr-sev i {{ animation: none; }} }}
  .dr-mono {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.74rem; color: {FAINT}; }}
  .dr-cause {{ position: relative; z-index: 1; font-size: 1.32rem; font-weight: 560; letter-spacing: -0.02em; color: {TEXT};
    line-height: 1.35; margin: 12px 0 12px; max-width: 860px; }}
  .dr-impact {{ position: relative; z-index: 1; display: flex; flex-wrap: wrap; gap: 8px; }}
  .dr-impact span {{ display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px;
    background: {BG}; border: 1px solid {BORDER_STRONG}; color: {TEXT_2}; font-size: 0.82rem; }}
  .dr-impact span::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--c); }}
  .dr-meta {{ position: relative; z-index: 1; display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; color: {MUTED};
    font-size: 0.8rem; }}
  .dr-meta b {{ color: {TEXT_2}; font-weight: 500; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.78rem; }}

  .dr-h {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.7rem; letter-spacing: 0.08em;
    text-transform: uppercase; color: {FAINT}; margin: 4px 0 10px; display: flex; justify-content: space-between; }}
  .dr-ai {{ border: 1px solid {BORDER}; border-radius: 12px; background: {BG_1}; padding: 14px 16px; margin-bottom: 18px;
    color: {TEXT_2}; font-size: 0.9rem; line-height: 1.6; }}
  .dr-ai .who {{ display: flex; align-items: center; gap: 7px; color: {FAINT}; font-size: 0.74rem; margin-bottom: 8px; }}
  .dr-ai .who i {{ width: 6px; height: 6px; border-radius: 50%; background: {GREEN}; box-shadow: 0 0 6px {GREEN}; }}
  .dr-ai.none {{ color: {FAINT}; font-size: 0.84rem; }}

  .dr-ev {{ display: grid; grid-template-columns: 22px 92px 86px 1fr; align-items: center; gap: 8px; padding: 9px 2px;
    border-top: 1px solid {BORDER}; font-size: 0.84rem; color: {TEXT_2}; }}
  .dr-ev:last-child {{ border-bottom: 1px solid {BORDER}; }}
  .dr-ev .bm-st {{ margin-right: 0; }}
  .dr-ev .src {{ color: {FAINT}; font-size: 0.74rem; font-family: "JetBrains Mono", ui-monospace, monospace;
    text-transform: uppercase; letter-spacing: 0.05em; }}
  .dr-ev .svc {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.8rem; color: {TEXT}; }}

  .dr-fix .row {{ display: flex; align-items: center; gap: 10px; }}
  .dr-fix .n {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.74rem; color: {FAINT}; }}
  .dr-fix .t {{ flex: 1; color: {TEXT}; font-size: 0.92rem; font-weight: 500; }}
  .dr-fix .d {{ color: {MUTED}; font-size: 0.8rem; line-height: 1.5; margin: 6px 0 2px 32px; }}
  .dr-risk {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.66rem; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; color: {MUTED}; }}
  .dr-risk.high {{ color: {AMBER}; border-color: {AMBER}55; background: {AMBER}12; }}

  .dr-ok {{ display: flex; align-items: center; gap: 14px; border: 1px solid {BORDER}; border-radius: 16px; background: {BG_1};
    padding: 18px 22px; margin-bottom: 18px; }}
  .dr-ok .t {{ color: {TEXT}; font-size: 1.1rem; font-weight: 560; }}
  .dr-ok .s {{ color: {MUTED}; font-size: 0.84rem; margin-top: 2px; }}
  .dr-svcs {{ display: flex; gap: 8px; flex-wrap: wrap; margin: -6px 0 18px; }}
  .dr-svcs span {{ display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px; border: 1px solid {BORDER};
    border-radius: 8px; background: {BG_1}; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.76rem;
    color: {TEXT_2}; }}
  .dr-svcs span i {{ width: 6px; height: 6px; border-radius: 50%; }}

  .dr-log {{ display: grid; grid-template-columns: 22px 70px 1fr auto; gap: 10px; align-items: center; padding: 9px 2px;
    border-top: 1px solid {BORDER}; font-size: 0.84rem; color: {TEXT_2}; }}
  .dr-log .bm-st {{ margin-right: 0; }}
  .dr-log .r {{ color: {FAINT}; font-size: 0.76rem; text-align: right; }}
  @media (max-width: 900px) {{
    .dr-ev {{ grid-template-columns: 22px 1fr; }}
    .dr-ev .src {{ display: none; }}
    .dr-log {{ grid-template-columns: 22px 1fr; }}
  }}
</style>
"""


# ---------------------------------------------------------------- state
def supported() -> bool:
    """C's orchestrator (docs/18 §5) is installed: `boomerang doctor` can run."""
    try:
        import boomerang.doctor as d
        return callable(getattr(d, "run", None))
    except ImportError:
        return False


def mode(src: Source) -> str:
    if src.kind == "fixtures":
        return "preview"
    if (src.kind == "run" and src.path is not None and src.path.name == st.session_state.get("live_run")
            and supported()):
        return "live"
    return "readonly"


def _key(src: Source) -> str:
    return f"doctor::{src.path}"


def _load_demo() -> dict[str, Any]:
    return json.loads(Path(DEMO_INCIDENT).read_text(encoding="utf-8"))


def incident(src: Source) -> dict[str, Any] | None:
    """The incident to show: the session's preview state on demo data, else runs/<id>/doctor.json."""
    if src.kind == "fixtures":
        ss = st.session_state
        if _key(src) not in ss:
            ss[_key(src)] = {"incident": _load_demo(), "open": True}
        return ss[_key(src)]
    data = src.json("doctor")
    if data is None:
        return None
    applied = data.get("applied", [])
    return {"incident": data, "open": not (applied and applied[-1].get("ok"))}   # the last fix brought it back


# ---------------------------------------------------------------- preview (demo data)
def _simulate(src: Source, state: dict[str, Any], action: dict[str, Any], rows: list[dict]) -> None:
    """Demo data: what the action would do, recorded as applied. Nothing runs."""
    a_id, target = action["id"], action["target"]
    if a_id in ("restart_service", "start_service"):
        ok, detail, resolved = True, f"{target} healthy after 2 s", True
    elif a_id == "move_back":
        ok, detail, resolved = True, f"{target} runs on AWS again; the rest stays on your servers", True
        ids = [r["id"] for r in sc.movable(rows)]
        if target in sc.placement(src, ids):
            sc.preview_move(src, ids, [target], "move_back")
    else:  # reverify: re-checking doesn't fix a stopped database
        ok, detail, resolved = False, "health still failing: postgres is down", False
    state["incident"].setdefault("applied", []).append(
        {"action": action, "by": "ui", "at": time.time(), "ok": ok, "detail": detail, "preview": True})
    state["open"] = not resolved


# ---------------------------------------------------------------- pieces
def _evidence_html(evidence: list[dict]) -> str:
    rows = []
    for s in evidence:
        rows.append(f'<div class="dr-ev">{status_icon(s.get("ok"), 18)}'
                    f'<span class="src">{SOURCE_LABEL.get(s.get("source"), s.get("source", ""))}</span>'
                    f'<span class="svc">{html.escape(s.get("service", ""))}</span>'
                    f'<span>{html.escape(s.get("detail", ""))}</span></div>')
    return "".join(rows)


def _hero(inc: dict[str, Any], is_open: bool) -> str:
    d = inc["diagnosis"]
    word, color = SEVERITY.get(inc.get("severity") or d.get("severity"), ("Incident", RED))
    when = time.strftime("%H:%M:%S", time.localtime(inc.get("at", time.time())))
    impact = "".join(f"<span>{html.escape(x)}</span>" for x in d.get("impact", []))
    meta = []
    if d.get("service"):
        meta.append(f'service <b>{html.escape(d["service"])}</b>')
    if d.get("component_id"):
        meta.append(f'component <b>{html.escape(d["component_id"])}</b>')
    meta.append(f'rule <b>{html.escape(d.get("rule", ""))}</b>')
    meta.append(f'detected <b>{when}</b>')
    return (f'<div class="dr-hero" style="--c:{color}"><div class="dr-top"><span class="dr-sev"><i></i>{word}</span>'
            f'<span class="dr-mono">incident on your servers</span></div>'
            f'<div class="dr-cause">{html.escape(d.get("root_cause", ""))}</div>'
            f'<div class="dr-impact">{impact}</div>'
            f'<div class="dr-meta">{"<span>" + "</span><span>".join(meta) + "</span>"}</div></div>')


def _healthy(inc: dict[str, Any] | None) -> None:
    last = (inc or {}).get("applied", [])[-1:] if inc else []
    sub = "No incident on the self-hosted stack."
    if inc and last:
        a = last[0]
        sub = (f'Resolved: {html.escape(inc["diagnosis"].get("rule", ""))} · '
               f'{html.escape(a["action"]["label"])} · {html.escape(a.get("detail", ""))}')
    st.markdown(f'<div class="dr-ok">{status_icon(True, 28)}<div><div class="t">All services healthy</div>'
                f'<div class="s">{sub}</div></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="dr-svcs">' + "".join(
        f'<span><i style="background:{GREEN};box-shadow:0 0 6px {GREEN}"></i>{s}</span>' for s in SERVICES)
        + "</div>", unsafe_allow_html=True)


def _history(inc: dict[str, Any] | None) -> None:
    applied = (inc or {}).get("applied", [])
    st.markdown('<div class="dr-h"><span>Applied fixes</span></div>', unsafe_allow_html=True)
    if not applied:
        st.markdown(f'<div class="sc-empty">Nothing applied yet. Every fix needs your approval.</div>',
                    unsafe_allow_html=True)
        return
    out = []
    for a in reversed(applied):
        when = time.strftime("%H:%M:%S", time.localtime(a.get("at", 0)))
        tag = " · preview" if a.get("preview") else ""
        out.append(f'<div class="dr-log">{status_icon(a.get("ok"), 18)}<span class="dr-mono">{when}</span>'
                   f'<span><b style="color:{TEXT};font-weight:500">{html.escape(a["action"]["label"])}</b>'
                   f' <span style="color:{MUTED}">· {html.escape(a.get("detail", ""))}</span></span>'
                   f'<span class="r">by {html.escape(a.get("by", "?"))}{tag}</span></div>')
    st.markdown("".join(out), unsafe_allow_html=True)


def _fixes(src: Source, state: dict[str, Any], rows: list[dict], m: str) -> None:
    d = state["incident"]["diagnosis"]
    st.markdown('<div class="dr-h"><span>Fixes</span><span>you approve each one</span></div>', unsafe_allow_html=True)
    step = st.session_state.get("step")
    busy = bool(step and not step.done)
    for i, a in enumerate(d.get("actions", []), 1):
        risk = a.get("risk", "low")
        why = {"restart_service": "Bring the service back on your servers.",
               "start_service": "Start the stopped service.",
               "move_back": "Run this service on AWS again; everything else stays on your servers.",
               "reverify": "Re-run config, boot, schema, tests and health."}.get(a["id"], "")
        with st.container(border=True, key=f"dr_fix_{i}"):
            st.markdown(f'<div class="dr-fix"><div class="row"><span class="n">{i:02d}</span>'
                        f'<span class="t">{html.escape(a["label"])}</span><span class="dr-risk {risk}">{risk} risk</span>'
                        f'</div><div class="d">{why}</div></div>', unsafe_allow_html=True)
            disabled = m == "readonly" or busy
            if risk == "high":
                with st.popover(f"{a['label']}…", width="stretch", disabled=disabled):
                    st.markdown(f"**{html.escape(a['label'])}?**")
                    st.caption("This moves the service and its data. AWS gets the latest data first; the app "
                               "keeps running on your servers for everything else.")
                    go = st.button("Confirm", key=f"dr_go_{i}", type="primary", width="stretch")
            else:
                go = st.button(a["label"], key=f"dr_go_{i}", width="stretch",
                               type="primary" if i == 1 else "secondary", disabled=disabled)
            if go:
                if m == "preview":
                    _simulate(src, state, a, rows)
                else:
                    st.session_state.step = runner.start("doctor-fix", src.path.name, args=[a["id"], a["target"]])
                st.rerun()


def _live_controls(src: Source, m: str) -> None:
    if m != "live":
        return
    step = st.session_state.get("step")
    busy = bool(step and not step.done)
    c1, c2, c3 = st.columns([1.2, 1.2, 3], vertical_alignment="bottom")
    if c1.button("Diagnose now", width="stretch", disabled=busy):
        st.session_state.step = runner.start("doctor", src.path.name)
        st.rerun()
    svc = c3.selectbox("Demo: stop a service", SERVICES, key="dr_break_svc", label_visibility="collapsed")
    if c2.button("Break it (demo)", width="stretch", disabled=busy):
        st.session_state.step = runner.start("doctor-break", src.path.name, args=[svc])
        st.rerun()


# ---------------------------------------------------------------- tab
def render(src: Source, rows: list[dict]) -> None:
    st.markdown(scope_ui.CSS + CSS, unsafe_allow_html=True)
    m = mode(src)
    state = incident(src)
    inc = state["incident"] if state else None
    is_open = bool(state and state["open"])
    if is_open:
        word, color = SEVERITY.get(inc.get("severity"), ("Incident", RED))
        badge = f'<span class="sc-mode" style="border-color:{color}55"><i style="background:{color};box-shadow:0 0 8px {color}"></i>{word}</span>'
    else:
        badge = f'<span class="sc-mode"><i style="background:{GREEN};box-shadow:0 0 8px {GREEN}"></i>Healthy</span>'
    st.markdown(
        f'<div class="sc-headrow"><div><div class="sc-kicker">Doctor</div>'
        f'<div class="sc-title">Incident response on your servers</div>'
        f'<div class="sc-sub">When something breaks after the move, the Doctor reads the stack, names the cause and '
        f'what users lose, and offers fixes. Rules decide; the AI explains; you approve every fix, including moving '
        f'one service back to AWS.</div></div>{badge}</div>', unsafe_allow_html=True)

    if m == "preview":
        p1, p2 = st.columns([5, 1], vertical_alignment="center")
        p1.markdown('<div class="sc-preview"><b>PREVIEW</b> Demo incident: fixes are simulated in this session. '
                    'Nothing is restarted or moved.</div>', unsafe_allow_html=True)
        if p2.button("Reset", key="dr_reset", width="stretch"):
            st.session_state.pop(_key(src), None)
            st.rerun()
    elif m == "readonly":
        why = ("this is a recorded run" if src.kind == "run" and src.path is not None
               and src.path.name != st.session_state.get("live_run")
               else "the engine has no `boomerang doctor` yet (C, docs/18 §5)")
        st.markdown(f'<div class="sc-preview"><b>READ ONLY</b> Diagnosis and fixes are disabled: {why}.</div>',
                    unsafe_allow_html=True)
    st.write("")
    _live_controls(src, m)

    if not is_open:
        _healthy(inc)
        _history(inc)
        return

    st.markdown(_hero(inc, is_open), unsafe_allow_html=True)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        explanation = (inc.get("explanation") or "").strip()
        if explanation:
            st.markdown(f'<div class="dr-h"><span>What happened</span></div><div class="dr-ai"><div class="who"><i></i>'
                        f'AI explanation · {html.escape(inc.get("llm", ""))}</div>{html.escape(explanation)}</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="dr-h"><span>What happened</span></div><div class="dr-ai none">No AI explanation: '
                        'the model wasn\'t available. The diagnosis above comes from the rules and stands on its own.'
                        '</div>', unsafe_allow_html=True)
        st.markdown('<div class="dr-h"><span>Evidence</span></div>' + _evidence_html(inc["diagnosis"].get("evidence", [])),
                    unsafe_allow_html=True)
        for s in inc["diagnosis"].get("evidence", []):
            if s.get("lines"):
                with st.expander(f"{s['service']} logs · last {len(s['lines'])} lines", icon=":material/terminal:"):
                    st.code(mask_secrets("\n".join(s["lines"])), language="text")
    with right:
        _fixes(src, state, rows, m)
    st.write("")
    _history(inc)
