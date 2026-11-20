"""Unit tests for boomerang/cost.py (owner: D, task D4)."""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from boomerang import cost

BEFORE = dt.date(2026, 11, 20)
AFTER = dt.date(2027, 1, 12)


def _p(id_, price, currency="EUR", **extra):
    return {"id": id_, "price": price, "currency": currency, "unit": "u",
            "source": f"https://example.test/{id_}", "retrieved": "2026-11-20", **extra}


@pytest.fixture
def prices():
    return {
        "fx": {"usd_to_eur": 0.5},
        "prices": [
            _p("aws.dynamodb.read_million", 1.0, "USD"),
            _p("aws.dynamodb.write_million", 2.0, "USD"),
            _p("aws.dynamodb.gb_month", 1.0, "USD", free_quantity=25),
            _p("aws.sqs.million", 1.0, "USD", free_quantity=1),
            _p("aws.s3.gb_month", 1.0, "USD"),
            _p("aws.s3.put_thousand", 1.0, "USD"),
            _p("aws.s3.get_thousand", 1.0, "USD"),
            _p("aws.fargate.vcpu_hour", 1.0, "USD"),
            _p("aws.fargate.gb_hour", 1.0, "USD"),
            _p("aws.egress.gb", 0.1, "USD", free_quantity=100),
            _p("lw.instance.m3_large", 30.0),
            _p("lw.block_storage.gb_month", 0.1),
            _p("lw.object_storage.gb_month", 0.02),
            _p("lw.object_storage.put_thousand", 0.004),
            _p("lw.object_storage.get_thousand", 0.0003),
            _p("lw.egress.gb", 0.002, free_quantity=1000),
        ],
    }


@pytest.fixture
def usage():
    return {
        "orders_per_month": 1_000_000, "reads_per_order": 1, "writes_per_order": 1,
        "messages_per_order": 1, "receipts_per_order": 1,
        "stored_gb_db": 35, "stored_gb_objects": 10, "egress_gb_month": 300,
        "migration_data_gb": 100, "hours_per_month": 10,
        "aws_compute": [{"name": "api", "vcpu": 1, "memory_gb": 2}],
        "lw_instance": "lw.instance.m3_large", "lw_disk_gb": 20, "lw_objects_on": "object_storage",
    }


@pytest.fixture
def ops():
    return {"hourly_rate_eur": 10, "hours_per_month": {"patching": 2}, "aws_ops_hours": 1,
            "backup_storage_gb": 10, "monitoring_tool_month_eur": 0,
            "eu_data_act_switching_free_from": "2027-01-12"}


def _line(r, side, item):
    return next(ln for ln in r["lines"] if ln["side"] == side and ln["item"].startswith(item))


def test_usd_prices_are_converted_and_free_tiers_applied(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    assert _line(r, "aws", "DynamoDB reads")["cost_eur"] == pytest.approx(0.5)     # 1M × $1 × 0.5
    assert _line(r, "aws", "DynamoDB storage")["quantity"] == 10                    # 35 − 25 free
    assert _line(r, "aws", "SQS requests")["quantity"] == pytest.approx(2)          # 3M − 1M free
    assert _line(r, "aws", "Internet egress")["quantity"] == 200                    # 300 − 100 free
    assert _line(r, "leaseweb", "Internet egress")["quantity"] == 0                 # under 1 TB pool


def test_totals_add_up_and_include_ops(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    aws = sum(ln["cost_eur"] for ln in r["lines"] if ln["side"] == "aws" and ln["kind"] == "monthly")
    lw = sum(ln["cost_eur"] for ln in r["lines"] if ln["side"] == "leaseweb")
    assert r["totals"]["aws"] == pytest.approx(aws, abs=0.01)
    assert r["totals"]["leaseweb"] == pytest.approx(lw, abs=0.01)
    assert _line(r, "aws", "Ops time")["cost_eur"] == 10
    assert _line(r, "leaseweb", "Ops: patching")["cost_eur"] == 20


def test_every_priced_line_has_a_source(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    for ln in r["lines"]:
        if ln["price_id"]:
            assert ln["source"].startswith("https://") and ln["retrieved"]


def test_missing_price_blocks_the_total(prices, usage, ops):
    broken = copy.deepcopy(prices)
    next(p for p in broken["prices"] if p["id"] == "aws.sqs.million")["price"] = None
    r = cost.compute(broken, usage, ops, today=BEFORE)
    assert r["totals"] is None
    assert r["break_even_months"] is None
    assert "aws.sqs.million" in r["missing"]


def test_missing_fx_blocks_usd_lines(prices, usage, ops):
    no_fx = copy.deepcopy(prices)
    no_fx["fx"] = {}
    r = cost.compute(no_fx, usage, ops, today=BEFORE)
    assert r["totals"] is None and "fx.usd_to_eur" in r["missing"]


def test_migration_egress_charged_before_eu_data_act(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    assert r["one_off"] == pytest.approx(100 * 0.1 * 0.5)


def test_migration_egress_free_from_eu_data_act_date(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=AFTER)
    assert r["one_off"] == 0
    assert "EU Data Act" in _line(r, "aws", "Migration egress")["note"]


def test_break_even_only_when_leaseweb_saves_money(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    saving = r["totals"]["aws"] - r["totals"]["leaseweb"]
    if saving > 0:
        assert r["break_even_months"] == pytest.approx(r["one_off"] / saving, abs=0.1)
    pricey = copy.deepcopy(prices)
    next(p for p in pricey["prices"] if p["id"] == "lw.instance.m3_large")["price"] = 100_000
    r2 = cost.compute(pricey, usage, ops, today=BEFORE)
    assert r2["cheaper"] == "aws" and r2["break_even_months"] is None


def test_real_price_files_load_and_compute():
    prices, usage, ops = cost.load_pricing()
    r = cost.compute(prices, usage, ops, today=BEFORE)
    assert r["missing"] == []
    assert r["totals"]["aws"] > 0 and r["totals"]["leaseweb"] > 0


def test_infra_totals_exclude_ops_hours(prices, usage, ops):
    r = cost.compute(prices, usage, ops, today=BEFORE)
    assert r["totals"]["aws"] - r["totals"]["aws_infra"] == pytest.approx(10)
    assert r["totals"]["leaseweb"] - r["totals"]["leaseweb_infra"] == pytest.approx(20)
