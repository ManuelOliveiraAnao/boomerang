"""Data mover tests. Both sides are fake AWS (moto) with different resource names: the Leaseweb
adapters mimic boto3, so the copy logic is the same. Real Leaseweb-side runs need B's kv adapter."""
from decimal import Decimal
from pathlib import Path

import boto3
import pytest

from boomerang import datamover, lab, provisioner_aws
from boomerang.models import AppModel

ROOT = Path(__file__).resolve().parent.parent
PAM = AppModel.model_validate_json((ROOT / "tests/fixtures/pam_shoplite.json").read_text(encoding="utf-8"))


@pytest.fixture
def two_sides():
    lab.up()
    lab.reset_moto()
    src_env = provisioner_aws.provision(PAM)                 # "aws": orders, receipts, order-events
    dst_env = provisioner_aws.provision(PAM, run_tag="lw")   # stand-in "leaseweb": *-lw
    provisioner_aws.seed_shoplite(src_env, n_orders=25, pending=3)
    src, dst = datamover.aws_side(src_env), datamover.aws_side(dst_env)
    yield src_env, dst_env, src, dst
    lab.reset_moto()


def _by_id(move):
    return {c.component_id: c for c in move.counts}


def test_forward_move_counts_and_checksums(two_sides):
    src_env, dst_env, src, dst = two_sides
    result = datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst))
    c = _by_id(result)
    assert (c["orders"].source_count, c["orders"].target_count) == (25, 25)
    assert (c["receipts"].source_count, c["receipts"].target_count) == (25, 25)
    assert (c["order_events"].source_count, c["order_events"].target_count) == (3, 3)
    assert all(x.checksum_match for x in result.counts)
    assert result.ok
    # functions / identities are never "moved"
    assert set(c) == {"orders", "receipts", "order_events"}
    # target counts confirmed independently
    assert provisioner_aws.counts_shoplite(dst_env) == {"orders": 25, "receipts": 25, "order_events": 3}
    assert provisioner_aws.counts_shoplite(src_env)["order_events"] == 0  # drained


def test_round_trip_brings_back_new_data(two_sides):
    src_env, dst_env, src, dst = two_sides
    datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst))
    # an order created on "Leaseweb" after the migration
    dst.table(dst_env["ORDERS_TABLE"]).put_item(Item={"order_id": "new-on-lw", "customer_id": "cust-99",
                                                      "items": [], "total_cents": 100, "status": "pending",
                                                      "created_at": "2026-11-21T10:00:00+00:00"})
    back = datamover.move(PAM, "leaseweb->aws", src_env, dst_env, sides=(dst, src))
    c = _by_id(back)
    assert (c["orders"].source_count, c["orders"].target_count) == (26, 26)  # 25 upserted + 1 new
    assert c["orders"].checksum_match
    item = src.table("orders").get_item(Key={"order_id": "new-on-lw"})["Item"]
    assert item["total_cents"] == 100


def test_detects_mismatch_when_target_has_extra_rows(two_sides):
    src_env, dst_env, src, dst = two_sides
    dst.table(dst_env["ORDERS_TABLE"]).put_item(Item={"order_id": "stale", "customer_id": "x"})
    c = _by_id(datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst)))
    assert c["orders"].target_count == 26 and c["orders"].source_count == 25


def test_copy_failure_is_reported_not_raised(two_sides):
    src_env, dst_env, src, dst = two_sides
    bad_env = dict(dst_env, RECEIPTS_BUCKET="no-such-bucket")
    bad_dst = datamover.aws_side(bad_env)
    c = _by_id(datamover.move(PAM, "aws->leaseweb", src_env, bad_env, sides=(src, bad_dst)))
    assert c["receipts"].checksum_match is False and "copy failed" in c["receipts"].notes[0]
    assert c["orders"].checksum_match  # other components still moved


def test_normalize_and_to_dynamo():
    assert datamover.normalize({"a": Decimal("3"), "b": Decimal("1.5"), "c": [Decimal("2")]}) == \
        {"a": 3, "b": 1.5, "c": [2]}
    assert datamover.to_dynamo({"x": 1.25, "y": [0.5], "z": 3}) == \
        {"x": Decimal("1.25"), "y": [Decimal("0.5")], "z": 3}
    # same data, different number types -> same checksum
    assert datamover.checksum([{"n": Decimal("7")}], "n") == datamover.checksum([{"n": 7}], "n")
