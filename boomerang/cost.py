"""Honest cost model: AWS vs Leaseweb, monthly and one-off (owner: D, task D4).

A pure function over three YAML files (docs/12-costs.md):
- pricing/prices.yaml        published prices, each with source URL and retrieval date
- pricing/usage.default.yaml the app's monthly usage (editable in the UI)
- pricing/ops.yaml           the hours it takes to run it yourself

Rules:
- No number without a source: every line carries the price id, URL and date.
- If any price a line needs is missing (null), no total is shown; the missing ids are listed.
- The AI never produces prices.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

PRICING_DIR = Path(__file__).resolve().parent.parent / "pricing"


# ---------------------------------------------------------------- loading
def load_pricing(directory: Path | str = PRICING_DIR) -> tuple[dict, dict, dict]:
    """Read prices, default usage and ops assumptions from YAML."""
    d = Path(directory)
    read = lambda name: yaml.safe_load((d / name).read_text(encoding="utf-8")) or {}  # noqa: E731
    return read("prices.yaml"), read("usage.default.yaml"), read("ops.yaml")


def _to_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


# ---------------------------------------------------------------- engine
class _Book:
    """Collects cost lines and the price ids that were needed but missing."""

    def __init__(self, prices: dict):
        self.by_id = {p["id"]: p for p in prices.get("prices", [])}
        self.fx = float((prices.get("fx") or {}).get("usd_to_eur") or 0) or None
        self.lines: list[dict] = []
        self.missing: set[str] = set()

    def eur_unit_price(self, price_id: str) -> float | None:
        p = self.by_id.get(price_id)
        if p is None or p.get("price") is None:
            self.missing.add(price_id)
            return None
        price = float(p["price"])
        cur = p.get("currency", "EUR")
        if cur == "EUR":
            return price
        if cur == "USD":
            if self.fx is None:
                self.missing.add("fx.usd_to_eur")
                return None
            return price * self.fx
        self.missing.add(f"{price_id} (currency {cur})")
        return None

    def free(self, price_id: str) -> float:
        return float(self.by_id.get(price_id, {}).get("free_quantity") or 0)

    def add(self, side: str, item: str, price_id: str | None, quantity: float, unit: str,
            *, eur: float | None = None, note: str = "", kind: str = "monthly",
            component: str = "compute") -> None:
        """Add a line. With price_id, cost = quantity × unit price; with eur, a fixed amount."""
        p = self.by_id.get(price_id or "", {})
        if price_id is not None:
            unit_price = self.eur_unit_price(price_id)
            cost = None if unit_price is None else quantity * unit_price
        else:
            unit_price, cost = None, eur
        self.lines.append({
            "side": side, "kind": kind, "item": item, "quantity": quantity, "unit": unit,
            "unit_price_eur": unit_price, "cost_eur": cost, "price_id": price_id,
            "original_price": p.get("price"), "currency": p.get("currency", "EUR"),
            "source": p.get("source", ""), "retrieved": str(p.get("retrieved", "")),
            "note": note,
            "category": "ops" if price_id is None and unit == "hours" else "infra",
            # What the line pays for: a movable kind (kv_table / queue / object_store) or a
            # shared part (compute, disk, egress, ops, migration). Used for partial migrations.
            "component": component,
        })


def _selfhosted_kinds(scope: Any) -> set[str] | None:
    """Component kinds running self-hosted under `scope`, or None when not hybrid (all or empty)."""
    if scope is None:
        return None
    raw = scope.selfhosted if hasattr(scope, "selfhosted") else (
        scope.get("selfhosted") if isinstance(scope, dict) else list(scope)
    )
    if not raw:
        return None
    kind_map = {
        "orders": "kv_table", "kv_table": "kv_table", "dynamodb": "kv_table", "db": "kv_table",
        "receipts": "object_store", "object_store": "object_store", "s3": "object_store", "storage": "object_store",
        "order_events": "queue", "order-events": "queue", "queue": "queue", "sqs": "queue",
    }
    kinds = {kind_map[str(item)] for item in raw if str(item) in kind_map}
    all_kinds = {"kv_table", "object_store", "queue"}
    return kinds if (kinds and kinds != all_kinds) else None


def compute(prices: dict, usage: dict, ops: dict, *, today: dt.date | None = None,
            scope: Any = None) -> dict:
    """Monthly AWS vs Leaseweb (and optional Hybrid) costs, one-off migration costs and break-even.

    Returns a dict with `lines`, `totals` (None when prices are missing), `missing`,
    `one_off`, `break_even_months`, `cheaper` and the assumptions used.
    """
    today = today or dt.date.today()
    b = _Book(prices)
    u, o = usage, ops
    orders = float(u.get("orders_per_month", 0))
    hours = float(u.get("hours_per_month", 730))
    rate = float(o.get("hourly_rate_eur", 0))
    hybrid_kinds = _selfhosted_kinds(scope)

    # ------------------------------------------------ AWS (monthly)
    reads = orders * float(u.get("reads_per_order", 0))
    writes = orders * float(u.get("writes_per_order", 0))
    b.add("aws", "DynamoDB reads", "aws.dynamodb.read_million", reads / 1e6, "million RRU",
          note="1 read request unit per read (on-demand)", component="kv_table")
    b.add("aws", "DynamoDB writes", "aws.dynamodb.write_million", writes / 1e6, "million WRU",
          note="1 write request unit per write (on-demand)", component="kv_table")
    db_gb = float(u.get("stored_gb_db", 0))
    db_free = b.free("aws.dynamodb.gb_month")
    b.add("aws", "DynamoDB storage", "aws.dynamodb.gb_month", max(0.0, db_gb - db_free), "GB-month",
          note=f"{db_gb:g} GB stored, first {db_free:g} GB free", component="kv_table")

    sqs_req = orders * float(u.get("messages_per_order", 0)) * 3
    sqs_free = b.free("aws.sqs.million")
    b.add("aws", "SQS requests", "aws.sqs.million", max(0.0, sqs_req / 1e6 - sqs_free), "million requests",
          note=f"send + receive + delete = 3 requests per message; first {sqs_free:g}M free",
          component="queue")

    obj_gb = float(u.get("stored_gb_objects", 0))
    b.add("aws", "S3 storage", "aws.s3.gb_month", obj_gb, "GB-month", component="object_store")
    puts = orders * float(u.get("receipts_per_order", 1))
    b.add("aws", "S3 PUT requests", "aws.s3.put_thousand", puts / 1000, "thousand requests",
          component="object_store")
    b.add("aws", "S3 GET requests", "aws.s3.get_thousand", reads / 1000, "thousand requests",
          component="object_store")

    tasks = u.get("aws_compute") or []
    vcpu = sum(float(t.get("vcpu", 0)) for t in tasks)
    mem = sum(float(t.get("memory_gb", 0)) for t in tasks)
    names = ", ".join(t.get("name", "?") for t in tasks)
    b.add("aws", "Fargate vCPU", "aws.fargate.vcpu_hour", vcpu * hours, "vCPU-hours",
          note=f"{names}: {vcpu:g} vCPU × {hours:g} h")
    b.add("aws", "Fargate memory", "aws.fargate.gb_hour", mem * hours, "GB-hours",
          note=f"{names}: {mem:g} GB × {hours:g} h")

    egress = float(u.get("egress_gb_month", 0))
    aws_free = b.free("aws.egress.gb")
    b.add("aws", "Internet egress", "aws.egress.gb", max(0.0, egress - aws_free), "GB",
          note=f"{egress:g} GB out, first {aws_free:g} GB free", component="egress")

    aws_ops = float(o.get("aws_ops_hours", 0))
    b.add("aws", "Ops time (managed services)", None, aws_ops, "hours", eur=aws_ops * rate,
          note=f"{aws_ops:g} h × €{rate:g}/h (assumption, ops.yaml)", component="ops")

    # ------------------------------------------------ Leaseweb (monthly)
    inst = u.get("lw_instance", "lw.instance.m3_large")
    b.add("leaseweb", "Server (api, worker, Postgres, RabbitMQ)", inst, 1, "month",
          note=b.by_id.get(inst, {}).get("item", ""))
    disk = float(u.get("lw_disk_gb", 0))
    b.add("leaseweb", "Network SSD (database + queue data)", "lw.block_storage.gb_month", disk, "GB-month",
          component="disk")

    backup_gb = float(o.get("backup_storage_gb", 0))
    if u.get("lw_objects_on", "object_storage") == "instance_disk":
        b.add("leaseweb", "Objects on server disk (SeaweedFS)", "lw.block_storage.gb_month", obj_gb, "GB-month",
              component="object_store")
    else:
        b.add("leaseweb", "Object Storage (objects)", "lw.object_storage.gb_month", obj_gb, "GB-month",
              component="object_store")
        b.add("leaseweb", "Object Storage PUT requests", "lw.object_storage.put_thousand", puts / 1000,
              "thousand requests", component="object_store")
        b.add("leaseweb", "Object Storage GET requests", "lw.object_storage.get_thousand", reads / 1000,
              "thousand requests", component="object_store")
    # Database backups live in object storage either way.
    b.add("leaseweb", "Backups (Object Storage)", "lw.object_storage.gb_month", backup_gb, "GB-month",
          note="database backups", component="kv_table")

    lw_free = b.free("lw.egress.gb")
    b.add("leaseweb", "Internet egress", "lw.egress.gb", max(0.0, egress - lw_free), "GB",
          note=f"{egress:g} GB out, first {lw_free:g} GB free (pooled)", component="egress")

    for task, h in (o.get("hours_per_month") or {}).items():
        h = float(h)
        b.add("leaseweb", f"Ops: {task.replace('_', ' ')}", None, h, "hours", eur=h * rate,
              note=f"{h:g} h × €{rate:g}/h (assumption, ops.yaml)",
              component="kv_table" if task.startswith("backups") else "ops")
    mon = float(o.get("monitoring_tool_month_eur", 0))
    if mon:
        b.add("leaseweb", "Monitoring tool", None, 1, "month", eur=mon, note="ops.yaml", component="ops")

    # ------------------------------------------------ one-off migration egress
    switch_free = _to_date(o.get("eu_data_act_switching_free_from", "2027-01-12"))
    mig_gb = float(u.get("migration_data_gb", 0))
    if today >= switch_free:
        b.add("aws", "Migration egress (one-off)", None, mig_gb, "GB", eur=0.0, kind="one_off",
              note=f"€0: EU Data Act bans switching charges incl. egress from {switch_free.isoformat()}",
              component="migration")
    else:
        b.add("aws", "Migration egress (one-off)", "aws.egress.gb", mig_gb, "GB", kind="one_off",
              note=f"Charged before {switch_free.isoformat()}; €0 from then under the EU Data Act",
              component="migration")

    # ------------------------------------------------ totals
    def total(side: str, kind: str = "monthly", category: str | None = None) -> float | None:
        sel = [ln for ln in b.lines if ln["side"] == side and ln["kind"] == kind
               and (category is None or ln["category"] == category)]
        if any(ln["cost_eur"] is None for ln in sel):
            return None
        return round(sum(ln["cost_eur"] for ln in sel), 2)

    aws_m, lw_m = total("aws"), total("leaseweb")
    one_off = total("aws", "one_off")

    # Hybrid column (docs/16 §3.4): the same lines scenario() prices, so the CLI (`cost --only`) and
    # the UI's placement comparison always agree. Lines kept on AWS are labelled as such.
    hyb_m = hyb_infra = None
    if hybrid_kinds is not None:
        sc = scenario(prices, usage, ops, hybrid_kinds, today=today)
        for ln in sc["lines"]:
            kept = ln["side"] == "aws" and ln["component"] in MOVABLE
            b.lines.append({**ln, "side": "hybrid", "item": ln["item"] + (" (kept on AWS)" if kept else "")})
        b.missing |= set(sc["missing"])
        hyb_m, hyb_infra = sc["total"], sc["infra"]
    if b.missing or aws_m is None or lw_m is None or (hybrid_kinds is not None and hyb_m is None):
        totals = None
        break_even = cheaper = None
    else:
        totals = {"aws": aws_m, "leaseweb": lw_m, "difference": round(aws_m - lw_m, 2),
                  "aws_infra": total("aws", category="infra"),
                  "leaseweb_infra": total("leaseweb", category="infra")}
        if hyb_m is not None:
            totals["hybrid"] = hyb_m
            totals["hybrid_infra"] = hyb_infra
        cheaper = "leaseweb" if lw_m < aws_m else "aws" if aws_m < lw_m else "equal"
        saving = aws_m - lw_m
        if one_off is None or saving <= 0:
            break_even = None
        else:
            break_even = round(one_off / saving, 1)

    return {
        "lines": b.lines,
        "totals": totals,
        "one_off": one_off,
        "break_even_months": break_even,
        "cheaper": cheaper,
        "missing": sorted(b.missing),
        "fx": prices.get("fx", {}),
        "region": prices.get("region", {}),
        "as_of": today.isoformat(),
    }


# ---------------------------------------------------------------- partial migrations
# docs/16 §3.4: a placement runs some movable components self-hosted and keeps the rest on
# AWS. The app (api + worker) runs on the self-hosted server as soon as anything moves.
MOVABLE = ("kv_table", "queue", "object_store")
_DATA_GB = {"kv_table": "stored_gb_db", "object_store": "stored_gb_objects"}   # what a move copies once
_ITEM_KB = {"kv_table": 1.0, "queue": 1.0}                                     # assumed bytes per read / message


def hybrid_traffic_gb(usage: dict, kept: set[str]) -> float:
    """GB / month the self-hosted app pulls from the AWS services it still uses.

    An estimate from usage: every read of a kept table or bucket and every kept queue message
    leaves AWS once. Bucket reads use `receipt_kb`; the rest assume ~1 KB."""
    orders = float(usage.get("orders_per_month", 0))
    reads = orders * float(usage.get("reads_per_order", 0))
    kb = 0.0
    if "kv_table" in kept:
        kb += reads * float(usage.get("item_kb", _ITEM_KB["kv_table"]))
    if "object_store" in kept:
        kb += reads * float(usage.get("receipt_kb", 2))
    if "queue" in kept:
        kb += orders * float(usage.get("messages_per_order", 0)) * float(usage.get("message_kb", _ITEM_KB["queue"]))
    return kb / 1e6


def scenario(prices: dict, usage: dict, ops: dict, moved, *, today: dt.date | None = None,
             traffic_gb: float | None = None) -> dict:
    """Monthly cost of one placement: `moved` = the movable kinds that run self-hosted.

    Nothing moved = all on AWS; everything moved = all self-hosted (the same lines as
    compute()). In between, a hybrid: AWS keeps the services of the kept kinds and their
    share of the managed-service ops time; the self-hosted side runs the app, the moved
    services and their ops; and the traffic the app pulls back from AWS is charged as AWS
    egress (the EU Data Act only frees switching, not ongoing traffic).
    """
    today = today or dt.date.today()
    moved = {k for k in moved if k in MOVABLE}
    kept = set(MOVABLE) - moved
    r = compute(prices, usage, ops, today=today)
    monthly = [ln for ln in r["lines"] if ln["kind"] == "monthly"]
    aws = [ln for ln in monthly if ln["side"] == "aws"]
    lw = [ln for ln in monthly if ln["side"] == "leaseweb"]
    b = _Book(prices)

    if not moved:
        mode, lines = "aws", list(aws)
    elif not kept:
        mode, lines = "selfhosted", list(lw)
    else:
        mode, lines = "hybrid", []
        share = len(kept) / len(MOVABLE)
        for ln in aws:
            if ln["component"] in kept:
                lines.append(ln)
            elif ln["component"] == "ops":
                lines.append({**ln, "quantity": ln["quantity"] * share,
                              "cost_eur": None if ln["cost_eur"] is None else ln["cost_eur"] * share,
                              "item": "Ops time (managed services kept)",
                              "note": f"{share:.0%} of the AWS ops hours: {len(kept)} of {len(MOVABLE)} services stay"})
        needs_disk = bool(moved & {"kv_table", "queue"})
        for ln in lw:
            c = ln["component"]
            if c in ("compute", "egress", "ops") or c in moved or (c == "disk" and needs_disk):
                lines.append(ln)
        gb = hybrid_traffic_gb(usage, kept) if traffic_gb is None else float(traffic_gb)
        free = b.free("aws.egress.gb")
        b.add("aws", "Ongoing AWS → self-hosted egress", "aws.egress.gb", max(0.0, gb - free), "GB",
              note=f"~{gb:,.2f} GB / month the app reads from the AWS services it still uses (not covered "
                   f"by the EU Data Act switching rules); first {free:g} GB free", component="egress")
        lines += b.lines

    # One-off: only the data of what moves leaves AWS.
    switch_free = _to_date(ops.get("eu_data_act_switching_free_from", "2027-01-12"))
    mig_gb = sum(float(usage.get(_DATA_GB[k], 0)) for k in moved if k in _DATA_GB)
    if not moved:
        one_off = 0.0
    elif today >= switch_free:
        one_off = 0.0
    else:
        unit = b.eur_unit_price("aws.egress.gb")
        one_off = None if unit is None else round(mig_gb * unit, 2)

    missing = sorted(set(r["missing"]) | b.missing)

    def _sum(sel):
        return None if missing or any(ln["cost_eur"] is None for ln in sel) else round(sum(ln["cost_eur"] for ln in sel), 2)

    return {
        "mode": mode, "moved": sorted(moved), "kept": sorted(kept), "lines": lines,
        "total": _sum(lines),
        "infra": _sum([ln for ln in lines if ln["category"] == "infra"]),
        "ops": _sum([ln for ln in lines if ln["category"] == "ops"]),
        "aws": _sum([ln for ln in lines if ln["side"] == "aws"]),
        "selfhosted": _sum([ln for ln in lines if ln["side"] == "leaseweb"]),
        "one_off": one_off, "migration_gb": mig_gb, "missing": missing,
    }


def compare(prices: dict, usage: dict, ops: dict, moved, *, today: dt.date | None = None) -> dict:
    """The three placements side by side: all on AWS, the chosen one, all self-hosted."""
    return {
        "aws": scenario(prices, usage, ops, (), today=today),
        "chosen": scenario(prices, usage, ops, moved, today=today),
        "selfhosted": scenario(prices, usage, ops, MOVABLE, today=today),
    }


def per_component(prices: dict, usage: dict, ops: dict, *, today: dt.date | None = None) -> dict:
    """Monthly cost of each movable kind on each side, plus the shared base (app, egress, ops).

    The shared base is what each side costs to exist at all: on AWS, Fargate + egress + ops;
    self-hosted, the server, its disk, egress and ops. Moving a single service brings the
    whole self-hosted base with it, which is why moving one small service rarely pays."""
    r = compute(prices, usage, ops, today=today)
    monthly = [ln for ln in r["lines"] if ln["kind"] == "monthly"]

    def total(side: str, comps) -> float | None:
        sel = [ln for ln in monthly if ln["side"] == side and ln["component"] in comps]
        return None if any(ln["cost_eur"] is None for ln in sel) else round(sum(ln["cost_eur"] for ln in sel), 2)

    rows = {k: {"aws": total("aws", (k,)), "selfhosted": total("leaseweb", (k,))} for k in MOVABLE}
    shared = {"aws": total("aws", ("compute", "egress", "ops")),
              "selfhosted": total("leaseweb", ("compute", "disk", "egress", "ops"))}
    return {"components": rows, "shared": shared, "missing": r["missing"]}


def scale_usage(usage: dict, factor: float) -> dict:
    """Usage with traffic and data multiplied by `factor` (the UI's scale slider)."""
    u = dict(usage)
    for key in ("orders_per_month", "stored_gb_db", "stored_gb_objects", "egress_gb_month", "migration_data_gb"):
        if key in u:
            u[key] = float(u[key]) * factor
    return u


CostReport = dict  # C1's stub names the return type CostReport; it's the dict compute() returns.


def estimate(prices: dict, usage: dict, ops: dict, scope: Any = None, *,
             today: dt.date | None = None) -> CostReport:
    """C1's binding name (docs/03 §3.13): same as compute()."""
    return compute(prices, usage, ops, today=today, scope=scope)
