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


@pytest.mark.integration
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


@pytest.mark.integration
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


@pytest.mark.integration
def test_detects_mismatch_when_target_has_extra_rows(two_sides):
    src_env, dst_env, src, dst = two_sides
    dst.table(dst_env["ORDERS_TABLE"]).put_item(Item={"order_id": "stale", "customer_id": "x"})
    c = _by_id(datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst)))
    assert c["orders"].target_count == 26 and c["orders"].source_count == 25


@pytest.mark.integration
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


# ---------------------------------------------------------------- added
@pytest.mark.integration
def test_content_type_and_metadata_are_copied(two_sides):
    src_env, dst_env, src, dst = two_sides
    s3 = src.s3()
    s3.put_object(Bucket="receipts", Key="extra/meta.txt", Body=b"hi", ContentType="text/plain",
                  Metadata={"origin": "pos-7"})
    datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst))
    obj = dst.s3().get_object(Bucket=dst_env["RECEIPTS_BUCKET"], Key="extra/meta.txt")
    assert obj["ContentType"] == "text/plain" and obj["Metadata"] == {"origin": "pos-7"}


@pytest.mark.integration
def test_empty_source_moves_nothing_and_is_ok(two_sides):
    _, dst_env, _, dst = two_sides
    lab.reset_moto()
    empty_env = provisioner_aws.provision(PAM, run_tag="empty")
    dst_env = provisioner_aws.provision(PAM, run_tag="lw")
    empty = datamover.aws_side(empty_env)
    result = datamover.move(PAM, "aws->leaseweb", empty_env, dst_env, sides=(empty, datamover.aws_side(dst_env)))
    assert all(c.source_count == 0 == c.target_count for c in result.counts) and result.ok


@pytest.mark.integration
def test_moving_twice_is_idempotent_for_tables_and_objects(two_sides):
    src_env, dst_env, src, dst = two_sides
    datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst))
    again = _by_id(datamover.move(PAM, "aws->leaseweb", src_env, dst_env, sides=(src, dst)))
    assert again["orders"].target_count == 25 and again["receipts"].target_count == 25
    assert again["order_events"].source_count == 0  # queue was drained the first time


@pytest.mark.integration
def test_floats_become_decimals_for_dynamodb(two_sides):
    src_env, dst_env, src, dst = two_sides

    class FloatTable:  # a Leaseweb-style source returning JSON numbers
        def scan(self, **kw):
            return {"Items": [{"order_id": "f1", "customer_id": "c", "rate": 0.25, "n": 3}]}
    fake_src = datamover.Side("leaseweb", dst_env, table=lambda n: FloatTable(), s3=dst.s3, queue=dst.queue)
    comp = PAM.components[0]
    dc = datamover.move_table(comp, fake_src, src)
    assert dc.checksum_match
    from decimal import Decimal
    assert src.table("orders").get_item(Key={"order_id": "f1"})["Item"]["rate"] == Decimal("0.25")


# ---------------------------------------------------------------- fakes: pagination and corruption
class PagedTable:
    def __init__(self, items, page=3):
        self.items, self.page, self.puts, self.calls = items, page, [], 0

    def scan(self, ExclusiveStartKey=None, **_):
        self.calls += 1
        start = ExclusiveStartKey["i"] if ExclusiveStartKey else 0
        out = {"Items": self.items[start:start + self.page]}
        if start + self.page < len(self.items):
            out["LastEvaluatedKey"] = {"i": start + self.page}
        return out

    def put_item(self, Item):
        self.items.append(Item)


class PagedS3:
    def __init__(self, objects, page=4, corrupt=False):
        self.objects, self.page, self.corrupt = dict(objects), page, corrupt

    def list_objects_v2(self, Bucket, ContinuationToken=None, **_):
        keys = sorted(self.objects)
        start = int(ContinuationToken or 0)
        out = {"Contents": [{"Key": k} for k in keys[start:start + self.page]],
               "IsTruncated": start + self.page < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + self.page)
        return out

    def get_object(self, Bucket, Key):
        import io
        body = self.objects[Key]
        if self.corrupt:
            body = body + b"!"
        return {"Body": io.BytesIO(body), "ContentType": "application/json"}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body


def _side(table=None, s3=None):
    return datamover.Side("fake", {}, table=lambda n: table, s3=lambda: s3, queue=lambda: None)


def test_table_scan_follows_pagination():
    src = PagedTable([{"order_id": str(i), "v": i} for i in range(10)], page=3)
    dst = PagedTable([], page=3)
    dc = datamover.move_table(PAM.components[0], _side(table=src), _side(table=dst))
    assert (dc.source_count, dc.target_count, dc.checksum_match) == (10, 10, True)
    assert src.calls == 4  # 3 + 3 + 3 + 1


def test_object_listing_follows_pagination():
    src = PagedS3({f"k{i:02d}": f"body{i}".encode() for i in range(10)}, page=4)
    dst = PagedS3({}, page=4)
    dc = datamover.move_objects(PAM.components[1], _side(s3=src), _side(s3=dst))
    assert (dc.source_count, dc.target_count, dc.checksum_match) == (10, 10, True)


def test_object_content_mismatch_is_detected():
    src = PagedS3({"a": b"1", "b": b"2"})
    dst = PagedS3({}, corrupt=True)  # target returns different bytes than were written
    dc = datamover.move_objects(PAM.components[1], _side(s3=src), _side(s3=dst))
    assert dc.source_count == dc.target_count == 2 and dc.checksum_match is False


def test_table_content_mismatch_is_detected():
    class Mangling(PagedTable):
        def put_item(self, Item):
            self.items.append(dict(Item, v=-1))
    src = PagedTable([{"order_id": "1", "v": 1}])
    dc = datamover.move_table(PAM.components[0], _side(table=src), _side(table=Mangling([])))
    assert dc.source_count == dc.target_count == 1 and dc.checksum_match is False


@pytest.mark.integration
@pytest.mark.slow
def test_real_s3_pagination_over_1000_objects(two_sides):
    """S3 returns at most 1000 keys per page; make sure the copy doesn't stop at 1000."""
    src_env, dst_env, src, dst = two_sides
    s3 = src.s3()
    for i in range(1005):
        s3.put_object(Bucket="receipts", Key=f"bulk/{i:05d}", Body=b"x")
    dc = datamover.move_objects(PAM.components[1], src, dst)
    assert dc.source_count == dc.target_count == 1030 and dc.checksum_match


def test_leaseweb_side_without_kv_adapter_is_a_clear_error():
    from sovereign_sdk import kv
    try:
        kv.resource()
    except Exception as e:
        if type(e).__name__ != "NotBuilt":
            pytest.skip("B's real kv adapter is present")
    else:
        pytest.skip("B's real kv adapter is present")
    with pytest.raises(datamover.MissingAdapter, match="B5"):
        datamover.leaseweb_side({"AMQP_URL": "amqp://x"})


def test_direction_picks_the_right_adapters(monkeypatch):
    made = []
    monkeypatch.setattr(datamover, "aws_side", lambda env: made.append(("aws", env["who"])) or "AWS")
    monkeypatch.setattr(datamover, "leaseweb_side", lambda env: made.append(("lw", env["who"])) or "LW")
    empty = PAM.model_copy(update={"components": []})
    datamover.move(empty, "aws->leaseweb", {"who": "src"}, {"who": "dst"})
    assert made == [("aws", "src"), ("lw", "dst")]
    made.clear()
    datamover.move(empty, "leaseweb->aws", {"who": "src"}, {"who": "dst"})
    assert made == [("lw", "src"), ("aws", "dst")]
    with pytest.raises(ValueError):
        datamover.move(empty, "sideways", {}, {})


@pytest.mark.integration
def test_move_emits_data_events(two_sides):
    from boomerang.events import Emitter
    src_env, dst_env, src, dst = two_sides
    events = []
    datamover.move(PAM, "aws->leaseweb", src_env, dst_env, Emitter("t", [events.append]), sides=(src, dst))
    assert events and all(e.stage == "data" for e in events)
    assert any(e.level == "success" and "orders: 25 -> 25" in e.message for e in events)
