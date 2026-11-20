"""Tab 5: Costs (owner: D, task D4).

AWS vs Leaseweb from published prices, editable usage, self-hosting ops time,
one-off migration egress and break-even. Every priced line links to its source.
"""
from __future__ import annotations

import copy
import datetime as dt

import pandas as pd
import streamlit as st

from boomerang import cost
from ui.data import Source
from ui.theme import AWS_ORANGE, LEASEWEB_RED, LIGHT_COLORS

INSTANCES = {
    "lw.instance.m3_large": "M3.LARGE · 2 vCPU · 7 GiB",
    "lw.instance.c3_xlarge": "C3.XLARGE · 4 vCPU · 7 GiB",
    "lw.instance.m3_xlarge": "M3.XLARGE · 4 vCPU · 15 GiB",
}


@st.cache_data
def _defaults() -> tuple[dict, dict, dict]:
    return cost.load_pricing()


def _eur(x: float | None) -> str:
    return "–" if x is None else f"€{x:,.2f}"


def _inputs(usage: dict, ops: dict) -> tuple[dict, dict, dt.date]:
    u, o = copy.deepcopy(usage), copy.deepcopy(ops)
    with st.expander("Your usage and assumptions", expanded=False):
        st.caption("Defaults are ShopLite assumptions from pricing/usage.default.yaml and pricing/ops.yaml. "
                   "Change them to your own numbers.")
        scale = st.select_slider("Traffic and data scale", options=[1, 5, 10, 50, 100], value=1,
                                 format_func=lambda v: f"×{v}", key="cost_scale",
                                 help="Multiplies orders, stored data and egress.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**App usage / month**")
            u["orders_per_month"] = st.number_input("Orders", 0, value=int(usage["orders_per_month"]),
                                                    step=10_000, key="cost_orders") * scale
            u["reads_per_order"] = st.number_input("Reads per order", 0, value=int(usage["reads_per_order"]),
                                                   key="cost_reads")
            u["writes_per_order"] = st.number_input("Writes per order", 0, value=int(usage["writes_per_order"]),
                                                    key="cost_writes")
            u["messages_per_order"] = st.number_input("Queue messages per order", 0,
                                                      value=int(usage["messages_per_order"]), key="cost_msgs")
        with c2:
            st.markdown("**Data**")
            u["stored_gb_db"] = st.number_input("Database GB", 0.0, value=float(usage["stored_gb_db"]),
                                                key="cost_db") * scale
            u["stored_gb_objects"] = st.number_input("Object storage GB", 0.0,
                                                     value=float(usage["stored_gb_objects"]), key="cost_obj") * scale
            u["egress_gb_month"] = st.number_input("Internet egress GB / month", 0.0,
                                                   value=float(usage["egress_gb_month"]), key="cost_egress") * scale
            u["migration_data_gb"] = st.number_input("Data to move once (GB)", 0.0,
                                                     value=float(usage["migration_data_gb"]), key="cost_mig") * scale
        with c3:
            st.markdown("**Leaseweb setup**")
            u["lw_instance"] = st.selectbox("Server", list(INSTANCES), format_func=INSTANCES.get,
                                            index=list(INSTANCES).index(usage.get("lw_instance", "lw.instance.m3_large")),
                                            key="cost_inst")
            u["lw_objects_on"] = st.radio("Objects stored on", ["object_storage", "instance_disk"],
                                          format_func={"object_storage": "Leaseweb Object Storage",
                                                       "instance_disk": "Server disk (SeaweedFS)"}.get,
                                          key="cost_objon")
            migration_date = st.date_input("Migration date", value=dt.date.today(), key="cost_date",
                                           help="Egress for switching is €0 from 12 Jan 2027 (EU Data Act).")
        st.markdown("**Ops time**")
        d1, d2, d3, d4, d5 = st.columns(5)
        o["hourly_rate_eur"] = d1.number_input("€ per hour", 0, value=int(ops["hourly_rate_eur"]), key="cost_rate")
        hpm = dict(ops.get("hours_per_month") or {})
        for col, key, label in ((d2, "patching_and_upgrades", "Patching h"),
                                (d3, "backups_and_restore_tests", "Backups h"),
                                (d4, "monitoring_and_on_call", "On-call h")):
            hpm[key] = col.number_input(label, 0.0, value=float(hpm.get(key, 0)), key=f"cost_h_{key}")
        o["hours_per_month"] = hpm
        o["aws_ops_hours"] = d5.number_input("AWS ops h", 0.0, value=float(ops.get("aws_ops_hours", 0)),
                                             key="cost_awsops")
    return u, o, migration_date


def _table(lines: list[dict]) -> None:
    rows = [{
        "Item": ln["item"],
        "Quantity": f'{ln["quantity"]:,.2f} {ln["unit"]}',
        "Unit price": ("–" if ln["original_price"] is None and ln["price_id"]
                       else f'{ln["original_price"]} {ln["currency"]}' if ln["price_id"] else "assumption"),
        "€ / month": ln["cost_eur"],
        "Source": ln["source"] or None,
        "Retrieved": ln["retrieved"] or "",
        "Note": ln["note"],
    } for ln in lines]
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={
            "€ / month": st.column_config.NumberColumn(format="€%.2f"),
            "Source": st.column_config.LinkColumn(display_text="source ↗"),
        },
    )


def _total_card(title: str, color: str, total: float | None, infra: float | None, sub: str = "") -> str:
    return (f'<div style="background:#0F1011;border:1px solid rgba(255,255,255,0.08);border-radius:14px;'
            f'padding:18px 20px;border-top:2px solid {color}">'
            f'<div style="color:#8A8F98;font-size:0.8rem">{title} · per month</div>'
            f'<div style="font-size:2rem;font-weight:560;letter-spacing:-0.03em;color:#F7F8F8">{_eur(total)}</div>'
            f'<div style="color:#8A8F98;font-size:0.8rem">infrastructure {_eur(infra)} · {sub}</div></div>')


def render(src: Source) -> None:
    prices, usage, ops = _defaults()
    u, o, migration_date = _inputs(usage, ops)
    r = cost.compute(prices, u, o, today=migration_date)

    if r["missing"]:
        st.error("No total shown: these prices are missing from pricing/prices.yaml → "
                 + ", ".join(r["missing"]))

    t = r["totals"] or {}
    aws_lines = [ln for ln in r["lines"] if ln["side"] == "aws" and ln["kind"] == "monthly"]
    lw_lines = [ln for ln in r["lines"] if ln["side"] == "leaseweb"]
    aws_ops = sum(ln["cost_eur"] or 0 for ln in aws_lines if ln["category"] == "ops")
    lw_ops = sum(ln["cost_eur"] or 0 for ln in lw_lines if ln["category"] == "ops")

    c1, c2 = st.columns(2)
    c1.markdown(_total_card("AWS · eu-central-1", AWS_ORANGE, t.get("aws"), t.get("aws_infra"),
                            f"ops {_eur(aws_ops)}"), unsafe_allow_html=True)
    c2.markdown(_total_card("Leaseweb", LEASEWEB_RED, t.get("leaseweb"), t.get("leaseweb_infra"),
                            f"ops {_eur(lw_ops)}"), unsafe_allow_html=True)
    st.write("")

    # ---- verdict ----
    if r["totals"]:
        diff = t["difference"]
        infra_diff = t["aws_infra"] - t["leaseweb_infra"]
        if r["cheaper"] == "leaseweb":
            st.success(f"For these numbers Leaseweb is **{_eur(diff)} / month cheaper**, ops time included.")
        elif r["cheaper"] == "aws":
            infra_note = (f" On infrastructure alone Leaseweb is {_eur(infra_diff)} cheaper; the self-hosting "
                          f"hours make the difference." if infra_diff > 0 else "")
            st.warning(f"For these numbers **AWS is {_eur(-diff)} / month cheaper**, ops time included."
                       f"{infra_note} Try a larger scale or your own hourly rate.")
        else:
            st.info("Both sides cost the same for these numbers.")

    m1, m2, m3 = st.columns(3)
    m1.metric("One-off migration egress", _eur(r["one_off"]),
              help=next(ln["note"] for ln in r["lines"] if ln["kind"] == "one_off"))
    m2.metric("Break-even", f'{r["break_even_months"]} months' if r["break_even_months"] is not None
              else ("immediate" if r["cheaper"] == "leaseweb" else "–"),
              help="One-off costs ÷ monthly saving. Shown only when Leaseweb saves money.")
    m3.metric("Prices as of", str(prices["prices"][0].get("retrieved", "")),
              help="Every line below links to the page it was read from.")

    st.markdown("#### AWS · monthly lines")
    _table(aws_lines)
    st.markdown("#### Leaseweb · monthly lines")
    _table(lw_lines)
    st.markdown("#### One-off")
    _table([ln for ln in r["lines"] if ln["kind"] == "one_off"])

    fx = r["fx"] or {}
    st.caption(
        f"USD → EUR at {fx.get('usd_to_eur')} (ECB reference rate {fx.get('reference_date')}). "
        f"AWS region: {r['region'].get('aws', '–')}. Leaseweb: {r['region'].get('leaseweb', '–')}. "
        "List prices excl. VAT; first paid tiers used throughout. Ops hours are assumptions: use your own. "
        "EU Data Act: switching charges, including egress, are banned from 12 Jan 2027; ongoing egress is not."
    )
