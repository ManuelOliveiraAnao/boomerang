"""Tab 4: Data & round trip (owner: D, task D6).

AWS → Leaseweb → AWS with tests and counts at each stop (A's data mover, C's round trip),
per-component counts with checksums, the round-trip checks, and what got lost (B's lost.py).
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from ui.data import Source
from ui.theme import AWS_ORANGE, LEASEWEB_RED, LIGHT_COLORS, MUTED, check_row, empty_state


def _tests(t: dict | None) -> str:
    if not t:
        return "tests not run"
    ok = t["total"] > 0 and t["failed"] == 0 and t["errors"] == 0
    return f'tests {t["passed"]}/{t["total"]} ' + ("passed" if ok else "· failing")


def _counts(move: dict | None, side: str) -> str:
    if not move:
        return ""
    key = "source_count" if side == "src" else "target_count"
    return " · ".join(f'{c["component_id"]} {c[key]}' for c in move["counts"])


def _stop(where: str, color: str, lines: list[str], ok: bool | None) -> str:
    dot = {True: LIGHT_COLORS["green"], False: LIGHT_COLORS["red"], None: MUTED}[ok]
    body = "".join(f'<div class="what">{html.escape(x)}</div>' for x in lines if x)
    return (f'<div class="lp-stop"><div class="where" style="color:{color}">'
            f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot};'
            f'box-shadow:0 0 8px {dot};margin-right:8px;vertical-align:middle"></span>{where}</div>{body}</div>')


def _ok_tests(t: dict | None) -> bool | None:
    return None if not t else (t["total"] > 0 and t["failed"] == 0 and t["errors"] == 0)


def _move_table(move: dict) -> None:
    rows = []
    for c in move["counts"]:
        same = c["source_count"] == c["target_count"]
        chk = c.get("checksum_match")
        rows.append({
            "Component": c["component_id"], "Kind": c["kind"],
            "Source": c["source_count"], "Target": c["target_count"],
            "Counts": "Match" if same else "Mismatch",
            "Checksum": "Match" if chk else "–" if chk is None else "Mismatch",
            "Notes": "; ".join(c.get("notes", [])),
        })
    green, red = LIGHT_COLORS["green"], LIGHT_COLORS["red"]
    tone = lambda v: f"color: {green}" if v == "Match" else f"color: {red}" if v == "Mismatch" else ""  # noqa: E731
    df = pd.DataFrame(rows)
    st.dataframe(df.style.map(tone, subset=["Counts", "Checksum"]), hide_index=True, width="stretch")


def render(src: Source) -> None:
    s = src.summary
    moves = {m["direction"]: m for m in s.get("data_moves") or []}
    fwd, back = moves.get("aws->leaseweb"), moves.get("leaseweb->aws")
    if not (fwd or back or s.get("lost")):
        empty_state("No data moved yet", "After verification the data moves to Leaseweb with counts and "
                    "checksums; the round trip copies it back and reruns the original tests.")
        return

    # ---- round-trip strip ----
    verifs = s.get("verifications") or []
    lw_tests = verifs[-1].get("tests") if verifs else None
    fwd_ok = None if not fwd else all(c["source_count"] == c["target_count"] and c.get("checksum_match") is not False
                                      for c in fwd["counts"])
    rt = s.get("roundtrip", "not_run")
    st.markdown(
        '<div class="lp-trip">'
        + _stop("AWS", AWS_ORANGE, ["Original app, untouched", _tests(s.get("baseline")),
                                    _counts(fwd, "src")], _ok_tests(s.get("baseline")))
        + '<div class="lp-arrow">→</div>'
        + _stop("Leaseweb", LEASEWEB_RED, ["Migrated app on the generated stack", _tests(lw_tests),
                                           _counts(fwd, "dst")], fwd_ok if _ok_tests(lw_tests) else _ok_tests(lw_tests))
        + '<div class="lp-arrow">→</div>'
        + _stop("Back to AWS", AWS_ORANGE, ["Original code + data copied back", _tests(s.get("roundtrip_tests")),
                                            _counts(back, "dst")],
                {"passed": True, "failed": False, "error": False}.get(rt))
        + "</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    # ---- counts ----
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### AWS → Leaseweb")
        _move_table(fwd) if fwd else st.caption("Not run yet.")
    with c2:
        st.markdown("#### Leaseweb → AWS")
        _move_table(back) if back else st.caption("Not run yet.")

    checks = s.get("roundtrip_checks") or []
    if checks:
        st.markdown("#### Round-trip checks")
        for c in checks:
            st.markdown(check_row(c["ok"], html.escape(c["name"]), f'· {html.escape(c.get("detail", ""))}'),
                        unsafe_allow_html=True)

    # ---- what got lost ----
    lost = s.get("lost") or []
    if lost:
        st.markdown("#### What got lost")
        st.caption("Every feature, grouped by what happened to it. Lost items stay with a human.")
        groups = [("lost", "Lost", LIGHT_COLORS["red"]), ("changed", "Changed", LIGHT_COLORS["amber"]),
                  ("preserved", "Preserved", LIGHT_COLORS["green"])]
        cols = st.columns(3)
        for col, (key, label, color) in zip(cols, groups):
            items = [x for x in lost if x["status"] == key]
            with col, st.container(border=True):
                st.markdown(f'<b style="color:{color}">● {label}</b> '
                            f'<span class="bm-muted">· {len(items)}</span>', unsafe_allow_html=True)
                for x in items:
                    st.markdown(f'<div style="margin-top:8px"><b>{html.escape(x["component_id"])}</b> '
                                f'<span class="bm-muted">· {html.escape(x["feature"])}</span><br>'
                                f'<span class="bm-muted" style="font-size:0.82rem">{html.escape(x["detail"])}'
                                f'</span></div>', unsafe_allow_html=True)
