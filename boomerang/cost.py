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
            *, eur: float | None = None, note: str = "", kind: str = "monthly") -> None:
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
        })


def compute(prices: dict, usage: dict, ops: dict, *, today: dt.date | None = None) -> dict:
    """Monthly AWS vs Leaseweb costs, one-off migration costs and break-even.

    Returns a dict with `lines`, `totals` (None when prices are missing), `missing`,
    `one_off`, `break_even_months`, `cheaper` and the assumptions used.
    """
    today = today or dt.date.today()
    b = _Book(prices)
    u, o = usage, ops
    orders = float(u.get("orders_per_month", 0))
    hours = float(u.get("hours_per_month", 730))
    rate = float(o.get("hourly_rate_eur", 0))

    # ------------------------------------------------ AWS (monthly)
    reads = orders * float(u.get("reads_per_order", 0))
    writes = orders * float(u.get("writes_per_order", 0))
    b.add("aws", "DynamoDB reads", "aws.dynamodb.read_million", reads / 1e6, "million RRU",
          note="1 read request unit per read (on-demand)")
    b.add("aws", "DynamoDB writes", "aws.dynamodb.write_million", writes / 1e6, "million WRU",
          note="1 write request unit per write (on-demand)")
    db_gb = float(u.get("stored_gb_db", 0))
    db_free = b.free("aws.dynamodb.gb_month")
    b.add("aws", "DynamoDB storage", "aws.dynamodb.gb_month", max(0.0, db_gb - db_free), "GB-month",
          note=f"{db_gb:g} GB stored, first {db_free:g} GB free")

    sqs_req = orders * float(u.get("messages_per_order", 0)) * 3
    sqs_free = b.free("aws.sqs.million")
    b.add("aws", "SQS requests", "aws.sqs.million", max(0.0, sqs_req / 1e6 - sqs_free), "million requests",
          note=f"send + receive + delete = 3 requests per message; first {sqs_free:g}M free")

    obj_gb = float(u.get("stored_gb_objects", 0))
    b.add("aws", "S3 storage", "aws.s3.gb_month", obj_gb, "GB-month")
    puts = orders * float(u.get("receipts_per_order", 1))
    b.add("aws", "S3 PUT requests", "aws.s3.put_thousand", puts / 1000, "thousand requests")
    b.add("aws", "S3 GET requests", "aws.s3.get_thousand", reads / 1000, "thousand requests")

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
          note=f"{egress:g} GB out, first {aws_free:g} GB free")

    aws_ops = float(o.get("aws_ops_hours", 0))
    b.add("aws", "Ops time (managed services)", None, aws_ops, "hours", eur=aws_ops * rate,
          note=f"{aws_ops:g} h × €{rate:g}/h (assumption, ops.yaml)")

    # ------------------------------------------------ Leaseweb (monthly)
    inst = u.get("lw_instance", "lw.instance.m3_large")
    b.add("leaseweb", "Server (api, worker, Postgres, RabbitMQ)", inst, 1, "month",
          note=b.by_id.get(inst, {}).get("item", ""))
    disk = float(u.get("lw_disk_gb", 0))
    b.add("leaseweb", "Network SSD (database + queue data)", "lw.block_storage.gb_month", disk, "GB-month")

    backup_gb = float(o.get("backup_storage_gb", 0))
    if u.get("lw_objects_on", "object_storage") == "instance_disk":
        b.add("leaseweb", "Objects on server disk (SeaweedFS)", "lw.block_storage.gb_month", obj_gb, "GB-month")
        b.add("leaseweb", "Backups (Object Storage)", "lw.object_storage.gb_month", backup_gb, "GB-month")
    else:
        b.add("leaseweb", "Object Storage (objects + backups)", "lw.object_storage.gb_month",
              obj_gb + backup_gb, "GB-month", note=f"{obj_gb:g} GB objects + {backup_gb:g} GB backups")
        b.add("leaseweb", "Object Storage PUT requests", "lw.object_storage.put_thousand", puts / 1000,
              "thousand requests")
        b.add("leaseweb", "Object Storage GET requests", "lw.object_storage.get_thousand", reads / 1000,
              "thousand requests")

    lw_free = b.free("lw.egress.gb")
    b.add("leaseweb", "Internet egress", "lw.egress.gb", max(0.0, egress - lw_free), "GB",
          note=f"{egress:g} GB out, first {lw_free:g} GB free (pooled)")

    for task, h in (o.get("hours_per_month") or {}).items():
        h = float(h)
        b.add("leaseweb", f"Ops: {task.replace('_', ' ')}", None, h, "hours", eur=h * rate,
              note=f"{h:g} h × €{rate:g}/h (assumption, ops.yaml)")
    mon = float(o.get("monitoring_tool_month_eur", 0))
    if mon:
        b.add("leaseweb", "Monitoring tool", None, 1, "month", eur=mon, note="ops.yaml")

    # ------------------------------------------------ one-off migration egress
    switch_free = _to_date(o.get("eu_data_act_switching_free_from", "2027-01-12"))
    mig_gb = float(u.get("migration_data_gb", 0))
    if today >= switch_free:
        b.add("aws", "Migration egress (one-off)", None, mig_gb, "GB", eur=0.0, kind="one_off",
              note=f"€0: EU Data Act bans switching charges incl. egress from {switch_free.isoformat()}")
    else:
        b.add("aws", "Migration egress (one-off)", "aws.egress.gb", mig_gb, "GB", kind="one_off",
              note=f"Charged before {switch_free.isoformat()}; €0 from then under the EU Data Act")

    # ------------------------------------------------ totals
    def total(side: str, kind: str = "monthly", category: str | None = None) -> float | None:
        sel = [ln for ln in b.lines if ln["side"] == side and ln["kind"] == kind
               and (category is None or ln["category"] == category)]
        if any(ln["cost_eur"] is None for ln in sel):
            return None
        return round(sum(ln["cost_eur"] for ln in sel), 2)

    aws_m, lw_m = total("aws"), total("leaseweb")
    one_off = total("aws", "one_off")
    if b.missing or aws_m is None or lw_m is None:
        totals = None
        break_even = cheaper = None
    else:
        totals = {"aws": aws_m, "leaseweb": lw_m, "difference": round(aws_m - lw_m, 2),
                  "aws_infra": total("aws", category="infra"),
                  "leaseweb_infra": total("leaseweb", category="infra")}
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
