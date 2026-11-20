"""Costs tab, top: compare placements (owner: D, docs/16 §3.4).

All on AWS, the chosen placement and all self-hosted side by side; what each service costs on
each side against the shared base; and how the three totals move as traffic grows.
"""
from __future__ import annotations

import datetime as dt
import html

import altair as alt
import pandas as pd
import streamlit as st

from boomerang import cost
from ui import scope as sc
from ui.components import scope as scope_ui
from ui.data import Source
from ui.theme import ACCENT, AWS_ORANGE, BG_1, BORDER, BORDER_STRONG, FAINT, LIGHT_COLORS, MUTED, TEXT, TEXT_2

SH = scope_ui.SH
GREEN, AMBER = LIGHT_COLORS["green"], LIGHT_COLORS["amber"]
SCALES = [1, 2, 5, 10, 20, 50, 100]

CSS = f"""
<style>
  .cc-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 14px 0 26px; }}
  .cc-card {{ position: relative; background: {BG_1}; border: 1px solid {BORDER}; border-radius: 14px; padding: 18px 20px 16px; }}
  .cc-card.sel {{ border-color: {ACCENT}80; background: linear-gradient(180deg, {ACCENT}12, {BG_1} 55%);
    box-shadow: 0 0 0 3px {ACCENT}12; }}
  .cc-card.cc-empty {{ border-style: dashed; background: transparent; }}
  .cc-empty-t {{ color: {FAINT}; font-size: 0.84rem; line-height: 1.55; margin-top: 16px; max-width: 280px; }}
  .cc-card .top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
  .cc-card .lbl {{ display: inline-flex; align-items: center; gap: 8px; color: {TEXT_2}; font-size: 0.84rem; font-weight: 500; }}
  .cc-card .lbl i {{ width: 8px; height: 8px; border-radius: 2px; }}
  .cc-tag {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.66rem; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; color: {MUTED}; }}
  .cc-tag.low {{ color: {GREEN}; border-color: {GREEN}55; background: {GREEN}12; }}
  .cc-num {{ font-size: 2.1rem; font-weight: 560; letter-spacing: -0.035em; color: {TEXT}; margin: 12px 0 0; line-height: 1.1; }}
  .cc-num small {{ font-size: 0.82rem; font-weight: 400; letter-spacing: 0; color: {FAINT}; margin-left: 6px; }}
  .cc-bar {{ display: flex; height: 4px; border-radius: 4px; overflow: hidden; background: rgba(255,255,255,0.06); margin: 14px 0 8px; }}
  .cc-bar span {{ display: block; height: 100%; }}
  .cc-leg {{ display: flex; gap: 14px; color: {FAINT}; font-size: 0.74rem; }}
  .cc-leg b {{ color: {TEXT_2}; font-weight: 500; }}
  .cc-rows {{ border-top: 1px solid {BORDER}; margin-top: 14px; padding-top: 10px; display: grid; gap: 6px; }}
  .cc-row {{ display: flex; justify-content: space-between; font-size: 0.8rem; color: {MUTED}; }}
  .cc-row b {{ font-weight: 500; color: {TEXT_2}; }}
  .cc-row b.up {{ color: {AMBER}; }}
  .cc-row b.down {{ color: {GREEN}; }}

  .cc-tbl {{ border: 1px solid {BORDER}; border-radius: 14px; background: {BG_1}; overflow: hidden; margin: 12px 0 10px; }}
  .cc-tr {{ display: grid; grid-template-columns: minmax(150px, 1.4fr) 1fr 1fr 1fr minmax(120px, 1.3fr);
    align-items: center; gap: 12px; padding: 11px 18px; border-top: 1px solid {BORDER}; font-size: 0.84rem; color: {TEXT_2}; }}
  .cc-tr:first-child {{ border-top: 0; }}
  .cc-tr.h {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.68rem; letter-spacing: 0.06em;
    text-transform: uppercase; color: {FAINT}; padding-top: 10px; padding-bottom: 10px; }}
  .cc-tr.base {{ background: rgba(255,255,255,0.015); }}
  .cc-tr .svc b {{ color: {TEXT}; font-weight: 500; }}
  .cc-tr .svc span {{ display: block; color: {FAINT}; font-size: 0.74rem; margin-top: 1px; }}
  .cc-tr .n {{ font-variant-numeric: tabular-nums; }}
  .cc-tr .up {{ color: {AMBER}; }}
  .cc-tr .down {{ color: {GREEN}; }}
  .cc-mini {{ display: grid; gap: 4px; }}
  .cc-mini span {{ display: block; height: 3px; border-radius: 3px; min-width: 2px; }}
  .cc-insight {{ color: {MUTED}; font-size: 0.86rem; line-height: 1.6; max-width: 820px; }}
  .cc-insight b {{ color: {TEXT}; font-weight: 560; }}

  @media (max-width: 900px) {{
    .cc-grid {{ grid-template-columns: 1fr; }}
    .cc-tr {{ grid-template-columns: 1.3fr 1fr 1fr; }}
    .cc-tr > :nth-child(4), .cc-tr > :nth-child(5) {{ display: none; }}
  }}
</style>
"""

KIND_NAME = {"kv_table": ("Database", "DynamoDB → Postgres"), "queue": ("Queue", "SQS → RabbitMQ"),
             "object_store": ("Object storage", "S3 → S3-compatible")}


def _eur(x: float | None, d: int = 2) -> str:
    return "–" if x is None else f"€{x:,.{d}f}"


def _delta(x: float | None) -> tuple[str, str]:
    if x is None:
        return "–", ""
    if abs(x) < 0.005:
        return "same", ""
    return (f"+{_eur(x)}", "up") if x > 0 else (f"−{_eur(-x)}", "down")


def _card(title: str, color: str, s: dict, base: dict, *, selected: bool, lowest: bool, tag: str = "") -> str:
    tot, infra, ops = s["total"], s["infra"], s["ops"]
    share = 0 if not tot else max(0.0, min(1.0, (infra or 0) / tot))
    d_txt, d_cls = _delta(None if tot is None or base["total"] is None else tot - base["total"])
    tags = (f'<span class="cc-tag low">Lowest</span>' if lowest else "") + (f'<span class="cc-tag">{tag}</span>' if tag else "")
    gb = s["migration_gb"]
    vs = d_txt if s["mode"] != "aws" else "baseline"
    move = _eur(s["one_off"]) + (f" · {gb:g} GB" if gb else "")
    rows = [f'<div class="cc-row"><span>vs all on AWS</span><b class="{d_cls}">{vs}</b></div>',
            f'<div class="cc-row"><span>One-off data move</span><b>{move}</b></div>']
    if s["mode"] == "hybrid":
        traffic = next((ln for ln in s["lines"] if ln["item"].startswith("Ongoing AWS")), None)
        if traffic:
            rows.append(f'<div class="cc-row"><span>Traffic back to AWS</span><b>{_eur(traffic["cost_eur"])}</b></div>')
    return (f'<div class="cc-card{" sel" if selected else ""}"><div class="top"><span class="lbl"><i style="background:{color}"></i>'
            f'{title}</span><span style="display:flex;gap:6px">{tags}</span></div>'
            f'<div class="cc-num">{_eur(tot)}<small>/ month</small></div>'
            f'<div class="cc-bar"><span style="width:{share * 100:.1f}%;background:{color}"></span>'
            f'<span style="width:{(1 - share) * 100:.1f}%;background:{color}55"></span></div>'
            f'<div class="cc-leg"><span>infra <b>{_eur(infra)}</b></span><span>ops time <b>{_eur(ops)}</b></span></div>'
            f'<div class="cc-rows">{"".join(rows)}</div></div>')


def _breakdown(per: dict, present: set[str]) -> str:
    comps = [k for k in cost.MOVABLE if k in present]

    def row(name: str, sub: str, a: float | None, s: float | None, cls: str = "") -> str:
        d_txt, d_cls = _delta(None if a is None or s is None else s - a)
        top = max(a or 0, s or 0) or 1          # each row compares its own two sides
        wa = 0 if a is None else 100 * a / top
        ws = 0 if s is None else 100 * s / top
        return (f'<div class="cc-tr {cls}"><span class="svc"><b>{name}</b><span>{sub}</span></span>'
                f'<span class="n">{_eur(a)}</span><span class="n">{_eur(s)}</span><span class="n {d_cls}">{d_txt}</span>'
                f'<span class="cc-mini"><span style="width:{wa:.1f}%;background:{AWS_ORANGE}"></span>'
                f'<span style="width:{ws:.1f}%;background:{SH}"></span></span></div>')

    out = ['<div class="cc-tr h"><span>Service</span><span>On AWS</span><span>Your servers</span>'
           '<span>If moved</span><span></span></div>']
    for k in comps:
        name, sub = KIND_NAME[k]
        out.append(row(name, sub, per["components"][k]["aws"], per["components"][k]["selfhosted"]))
    out.append(row("Shared base", "AWS: Fargate, egress, ops · yours: server, disk, egress, ops",
                   per["shared"]["aws"], per["shared"]["selfhosted"], "base"))
    return '<div class="cc-tbl">' + "".join(out) + "</div>"


def _chart(prices: dict, base_usage: dict, ops: dict, moved: list[str], today: dt.date, scale: float,
           chosen_label: str) -> tuple[alt.Chart | None, str]:
    rows = []
    for k in SCALES:
        u = cost.scale_usage(base_usage, k)
        c = cost.compare(prices, u, ops, moved, today=today)
        for key, name in (("aws", "All on AWS"), ("chosen", chosen_label), ("selfhosted", "All self-hosted")):
            if key == "chosen" and c["chosen"]["mode"] != "hybrid":
                continue
            if c[key]["total"] is not None:
                rows.append({"scale": k, "placement": name, "eur": c[key]["total"]})
    if not rows:
        return None, ""
    df = pd.DataFrame(rows)
    names = ["All on AWS", chosen_label, "All self-hosted"]
    colors = [AWS_ORANGE, ACCENT, SH]
    present = [n for n in names if n in set(df["placement"])]
    pal = alt.Scale(domain=present, range=[colors[names.index(n)] for n in present])
    x = alt.X("scale:Q", scale=alt.Scale(type="log", domain=[1, 100]),
              axis=alt.Axis(values=SCALES, format="d", labelExpr="'×' + datum.value", title=None, grid=False,
                            labelColor=MUTED, domainColor=BORDER_STRONG, tickColor=BORDER_STRONG))
    y = alt.Y("eur:Q", axis=alt.Axis(format=",.0f", labelExpr="'€' + datum.label", title=None,
                                     labelColor=MUTED, gridColor="rgba(255,255,255,0.06)", domain=False, ticks=False))
    dash = alt.Scale(domain=present, range=[[5, 4] if n == chosen_label else [1, 0] for n in present])
    base = alt.Chart(df).encode(x=x, y=y, color=alt.Color("placement:N", scale=pal,
                                legend=alt.Legend(orient="top", title=None, labelColor=TEXT_2, symbolType="stroke")),
                                strokeDash=alt.StrokeDash("placement:N", scale=dash, legend=None))
    lines = base.mark_line(strokeWidth=2, interpolate="monotone")
    pts = base.mark_point(filled=True, size=36).encode(
        tooltip=[alt.Tooltip("placement:N", title="Placement"), alt.Tooltip("scale:Q", title="Scale ×"),
                 alt.Tooltip("eur:Q", title="€ / month", format=",.2f")])
    rule = alt.Chart(pd.DataFrame({"scale": [scale]})).mark_rule(strokeDash=[3, 3], color=FAINT).encode(x="scale:Q")
    chart = (rule + lines + pts).properties(height=280).configure_view(strokeWidth=0).configure(background="transparent")

    # Where does all self-hosted overtake AWS? Checked at every whole multiple, not just the plotted ones.
    def cheaper(k: int) -> bool:
        c = cost.compare(prices, cost.scale_usage(base_usage, k), ops, (), today=today)
        a, s_ = c["aws"]["total"], c["selfhosted"]["total"]
        return a is not None and s_ is not None and s_ < a
    cross = next((k for k in range(1, 101) if cheaper(k)), None)
    note = (f"With your inputs, all self-hosted is cheaper than AWS from about <b>×{cross}</b> the current traffic."
            if cross else "With your inputs, all self-hosted does not get cheaper than AWS up to ×100.")
    return chart, note


def compare_section(src: Source, rows: list[dict], prices: dict, usage: dict, ops: dict,
                    today: dt.date, scale: float, base_usage: dict) -> list[str]:
    """Render the comparison; returns the component ids picked to run on your servers."""
    st.markdown(scope_ui.CSS + CSS, unsafe_allow_html=True)
    mov = sc.movable(rows)
    ids = [r["id"] for r in mov]
    by_id = {r["id"]: r for r in mov}
    if not mov:
        return []

    st.markdown('<div class="sc-kicker">Compare</div><div class="sc-title">What each placement costs</div>'
                '<div class="sc-sub">Pick which services run on your servers. The rest stays on AWS. Prices are '
                'published list prices (Leaseweb as the self-hosted example); ops time is your own estimate, '
                'set under "Your usage and assumptions".</div>', unsafe_allow_html=True)
    current = sorted(sc.placement(src, ids))
    picked = st.pills("Run on your servers", ids, selection_mode="multi", default=current,
                      format_func=lambda i: f"{i} · {sc.SERVICE[by_id[i]['kind']][0]}",
                      key=f"cc_pick_{src.path}_{','.join(current)}")
    picked = list(picked or [])
    moved = sc.kinds(mov, picked)
    c = cost.compare(prices, usage, ops, moved, today=today)
    mode, words = sc.label(set(picked), ids)
    totals = {k: v["total"] for k, v in c.items() if v["total"] is not None}
    lowest = min(totals, key=totals.get) if totals else None
    if mode != "hybrid" and lowest == "chosen":
        lowest = mode

    chosen_title = "Your hybrid"
    yours = "your placement"
    middle = (_card(chosen_title, ACCENT, c["chosen"], c["aws"], selected=True, lowest=lowest == "chosen",
                    tag=f"{len(picked)}/{len(ids)} moved")
              if mode == "hybrid" else
              f'<div class="cc-card cc-empty"><div class="top"><span class="lbl"><i style="background:{ACCENT}"></i>'
              f'Hybrid</span></div><div class="cc-empty-t">Pick some services, not all, to price a hybrid: the moved '
              f'ones on your servers, the rest kept on AWS, plus the traffic between them.</div></div>')
    st.markdown('<div class="cc-grid">'
                + _card("All on AWS", AWS_ORANGE, c["aws"], c["aws"], selected=mode == "aws", lowest=lowest == "aws",
                        tag=yours if mode == "aws" else "today")
                + middle
                + _card("All self-hosted", SH, c["selfhosted"], c["aws"], selected=mode == "selfhosted",
                        lowest=lowest == "selfhosted", tag=yours if mode == "selfhosted" else "")
                + "</div>", unsafe_allow_html=True)
    if c["chosen"]["missing"]:
        st.error("No totals: these prices are missing from pricing/prices.yaml → " + ", ".join(c["chosen"]["missing"]))

    # ---- per service ----
    per = cost.per_component(prices, usage, ops, today=today)
    st.markdown('<div class="sc-kicker" style="margin-top:6px">Per service</div>'
                '<div class="sc-title" style="font-size:1.05rem">Where each service is cheaper</div>',
                unsafe_allow_html=True)
    st.markdown(_breakdown(per, {r["kind"] for r in mov}), unsafe_allow_html=True)
    sa, ss_ = per["shared"]["aws"], per["shared"]["selfhosted"]
    if sa is not None and ss_ is not None:
        st.markdown(
            f'<div class="cc-insight">The services themselves are cheap on both sides. <b>The base decides it</b>: '
            f'running your own server and its ops costs {_eur(ss_)} a month before any service moves, against '
            f'{_eur(sa)} on AWS. A hybrid pays both bases, so it is a stepping stone (lower risk, move and check one '
            f'service at a time), not the cheapest end state.</div>', unsafe_allow_html=True)

    # ---- scale ----
    st.markdown('<div class="sc-kicker" style="margin-top:26px">Scale</div>'
                '<div class="sc-title" style="font-size:1.05rem">Monthly cost as traffic grows</div>',
                unsafe_allow_html=True)
    chart, note = _chart(prices, base_usage, ops, moved, today, scale, chosen_title)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
        st.markdown(f'<div class="cc-insight">{note} The dotted line is the scale set under '
                    f'"Your usage and assumptions" (×{scale:g}).</div>', unsafe_allow_html=True)
    return picked
