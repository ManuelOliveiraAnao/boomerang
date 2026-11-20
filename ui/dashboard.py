"""Dashboard page: run a migration and inspect every stage (owner: D).

Live: the sidebar buttons start `boomerang plan/approve/execute/roundtrip` subprocesses
(ui/runner.py) and the header polls the run folder every second.
Replay: plays a recorded run's events with compressed timing; works offline.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui import data, live, runner, theme
from ui.components import breaks, code, costs, roundtrip, setup
from ui.components import scope as scope_tab

APP_DIR = "sample-app/aws"


def _sidebar(ok: bool, why: str) -> tuple[str, Path | None]:
    ss = st.session_state
    step: runner.Step | None = ss.get("step")
    busy = bool(step and not step.done)
    with st.sidebar:
        st.selectbox("Source app", [APP_DIR], help="AWS app to migrate")
        mode = st.radio("Mode", ["Live", "Replay"], horizontal=True, key="mode")
        chosen: Path | None = None

        if mode == "Live":
            run_id = ss.get("live_run")
            if run_id:
                status = runner.run_status(run_id) or ("starting" if busy else "no run.json yet")
                st.markdown(f'<span class="bm-muted">Run</span> `{run_id}` · **{status}**',
                            unsafe_allow_html=True)
            if st.button("Migrate to Leaseweb", type="primary", width="stretch",
                         disabled=not ok or busy):
                rid = runner.new_run_id()
                ss.live_run, ss.approved = rid, False
                ss.step = runner.start("plan", rid, APP_DIR)
                st.rerun()
            can_rt = bool(run_id) and runner.run_status(run_id) == "passed" and not busy
            if st.button("Round trip back to AWS", width="stretch", disabled=not ok or not can_rt):
                ss.step = runner.start("roundtrip", run_id, APP_DIR)
                st.rerun()
            if not ok:
                st.caption(why + " Showing the demo run meanwhile.")
            elif not run_id:
                st.caption("Migrate runs the scan and stops for your approval in What breaks.")
        else:
            runs = [data.DEMO_RUN_DIR] + data.list_runs()
            chosen = st.selectbox("Recorded run", runs, key="replay_run",
                                  format_func=lambda p: "demo (fixtures)" if p == data.DEMO_RUN_DIR else p.name)
            speed = st.select_slider("Speed", [2, 4, 8, 16, 32], value=8, format_func=lambda v: f"×{v}",
                                     key="replay_speed")
            if st.button("▶ Replay run", type="primary", width="stretch"):
                ss.replay = live.start_replay(data.read_events(chosen), float(speed))
                ss.replay_run_path = str(chosen)
                st.rerun()
            if ss.get("replay") and ss.get("replay_run_path") == str(chosen):
                if st.button("Show full run", width="stretch"):
                    ss.replay = None
                    st.rerun()

        st.divider()
        text, local = data.llm_badge()
        st.markdown(theme.badge(text, "local" if local else "cloud"), unsafe_allow_html=True)
    return mode, chosen


def _source(mode: str, chosen: Path | None, ok: bool) -> data.Source:
    ss = st.session_state
    if mode == "Replay" and chosen is not None:
        return data.run_source(chosen) if chosen != data.DEMO_RUN_DIR else data.fixtures_source()
    run_id = ss.get("live_run")
    if ok and run_id and (data.RUNS_DIR / run_id).is_dir():
        return data.run_source(data.RUNS_DIR / run_id)
    return data.fixtures_source()


def _live_panel(src: data.Source, mode: str) -> None:
    """Pipeline canvas + activity feed. Polls while a step or a replay is running."""
    ss = st.session_state
    step: runner.Step | None = ss.get("step")
    replaying = mode == "Replay" and ss.get("replay") and ss.get("replay_run_path") == str(src.path)
    polling = bool((step and not step.done and mode == "Live") or (replaying and not live.replay_done(ss.replay)))

    @st.fragment(run_every=1.0 if polling else None)
    def panel() -> None:
        events = data.read_events(src.path) if src.path else []
        if replaying:
            events = live.replay_visible(events, ss.replay)
        status = data.stage_status(events)
        if src.path is not None and (src.path / "approval.json").is_file() and not replaying:
            status["approve"] = "done"
        elif ss.get("approved") and src.kind == "fixtures" and not replaying:
            status["approve"] = "done"
        st.markdown(theme.pipeline_html(status), unsafe_allow_html=True)

        cur: runner.Step | None = ss.get("step")
        running = bool(cur and not cur.done and mode == "Live")
        if replaying:
            st.progress(live.replay_progress(ss.replay), text=f"Replay ×{ss.replay['speed']:g}")
        st.markdown(live.feed_html(events, running=running or (replaying and not live.replay_done(ss.replay))),
                    unsafe_allow_html=True)

        # A step finished: chain the next one or report, then refresh the whole page once.
        if cur and cur.done and not ss.get("step_reported") == id(cur):
            ss.step_reported = id(cur)
            nxt = runner.advance(cur, APP_DIR)
            if nxt:
                ss.step = nxt
            else:
                text = runner.EXIT_TEXT.get(cur.code, f"exit {cur.code}")
                (st.toast if cur.code in (0, 4) else st.error)(f"`boomerang {cur.name}`: {text}")
                if cur.code not in (0, 4):
                    ss.step_error = (cur.name, cur.tail())
            st.rerun(scope="app")
        if replaying and live.replay_done(ss.replay) and polling:
            st.rerun(scope="app")

    panel()

    err = ss.get("step_error")
    if err and mode == "Live":
        with st.expander(f"`boomerang {err[0]}` failed: output", expanded=True):
            st.code(err[1] or "(no output)", language="text")


def render() -> None:
    ss = st.session_state
    ss.setdefault("approved", False)
    ss.setdefault("mode", "Replay")
    st.markdown(live.FEED_CSS, unsafe_allow_html=True)

    ok, why = runner.available()
    mode, chosen = _sidebar(ok, why)
    src = _source(mode, chosen, ok)

    st.markdown(
        f'<span class="bm-eyebrow"><b>Migration</b>{src.label}</span>'
        '<div class="bm-apptitle">ShopLite · <span class="bm-aws">AWS</span> → '
        '<span class="bm-lw">Leaseweb</span> → <span class="bm-aws">AWS</span></div>',
        unsafe_allow_html=True,
    )
    summ = src.summary
    if src.is_demo:
        st.warning("Demo data: this run was assembled from real component outputs plus illustrative "
                   "pipeline results (fixtures/run-demo), not recorded from one pipeline run. "
                   "Pick a run under Recorded run, or switch to Live to start one; a recorded "
                   "`runs/golden` appears first automatically.", icon=":material/info:")
    if summ.get("llm") and "cloud" in summ["llm"]:
        st.info(f"This run used the cloud fallback model ({summ['llm']}), not a local model.")

    _live_panel(src, mode)

    # Component rows feed What breaks, Placement and the cost comparison.
    assessment = src.json("assessment") or summ.get("assessment")
    assessment = breaks._as_dict(assessment) if assessment else None
    rows = breaks.group_findings(assessment, breaks._load_pam(src)) if assessment else []

    t1, t2, t3, t4, t5, t6 = st.tabs(["What breaks", "Placement", "Code", "Self-hosted setup",
                                      "Data & round trip", "Costs"])
    with t1:
        breaks.render(src)
    with t2:
        if assessment:
            scope_tab.render(src, rows, assessment)
        else:
            theme.empty_state("No assessment yet", "Placement needs a scan first.")
    with t3:
        code.render(src)
    with t4:
        setup.render(src)
    with t5:
        roundtrip.render(src)
    with t6:
        costs.render(src, rows)
