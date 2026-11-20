"""Partial migration (docs/16): scope at approval, scoped execute, hybrid round trip, move-over / move-back.

The fake world of tests/test_pipeline.py (no Docker, no model); the de-lock is the real one on mini_app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from boomerang import pipeline
from boomerang.cli import app as cli_app
from boomerang.events import read_events
from boomerang.models import Approval, DataCount, DataMove, LostItem
from boomerang.pipeline import PipelineError
from tests.test_pipeline import World, llm, root_of, source, world  # noqa: F401 - fixtures
from tests.test_roundtrip import Trip, trip  # noqa: F401 - fixtures

KIND = {"orders": "kv_table", "receipts": "object_store", "order_events": "queue"}
COUNTS = {"aws->leaseweb": {"orders": 25, "receipts": 25, "order_events": 3},
          "leaseweb->aws": {"orders": 26, "receipts": 26, "order_events": 1}}


class Moves:
    """datamover.move / render / lost.build that remember the scope they were called with."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.moves: list[tuple[str, list[str] | None]] = []
        self.renders: list[list[str] | None] = []
        self.lost_scopes: list[Any] = []

    def move(self, pam, direction, src, dst, emit=None, sides=None, *, components=None) -> DataMove:
        self.world.calls.append(f"move:{direction}")
        self.moves.append((direction, components))
        ids = components if components is not None else list(KIND)
        n = COUNTS[direction]
        return DataMove(direction=direction, counts=[
            DataCount(component_id=c, kind=KIND[c], source_count=n[c], target_count=n[c],  # type: ignore[arg-type]
                      checksum_match=KIND[c] != "queue" or None) for c in ids])

    def render(self, pam, secrets, scope=None):
        self.renders.append(scope)
        return self.world.generated

    def lost(self, pam, assessment, data_moves, scope=None) -> list[LostItem]:
        self.lost_scopes.append(scope)
        return [LostItem(component_id="order_events", feature="basic", status="preserved", detail="Stays on AWS")]


@pytest.fixture
def moves(world: World, monkeypatch: pytest.MonkeyPatch) -> Moves:
    m = Moves(world)
    monkeypatch.setattr("boomerang.datamover.move", m.move)
    monkeypatch.setattr("boomerang.generators.leaseweb.render", m.render)
    monkeypatch.setattr("boomerang.lost.build", m.lost)
    return m


def app_text(run_id: str, rel: str) -> str:
    return (root_of(run_id) / "leaseweb" / "app" / rel).read_text(encoding="utf-8")


def migrate_hybrid(source: Path, run_id: str = "p1") -> None:
    pipeline.plan(source, run_id=run_id)
    pipeline.approve(run_id, scope=["orders", "receipts"])
    assert pipeline.execute(run_id).status == "passed"


# ---------- approve(scope) ----------

def test_approve_records_the_scope_and_the_hybrid_warning(world, source):
    pipeline.plan(source, run_id="p1")
    approval = pipeline.approve("p1", scope=["receipts", "orders"])
    assert approval.scope == ["orders", "receipts"]                       # PAM order
    saved = Approval.model_validate_json((root_of("p1") / "approval.json").read_text(encoding="utf-8"))
    assert saved.scope == ["orders", "receipts"]
    assert pipeline.load_run("p1").scope is not None
    texts = [e.message for e in read_events(root_of("p1") / "events.jsonl")]
    assert any("order_events stay on AWS" in t for t in texts)
    assert any("🔴 hybrid: Create scoped AWS credentials for kept components: order_events." in t for t in texts)


def test_default_and_full_scope_mean_the_full_migration(world, source):
    pipeline.plan(source, run_id="p1")
    assert pipeline.approve("p1").scope is None
    assert pipeline.approve("p1", scope=["order_events", "orders", "receipts"]).scope is None
    assert pipeline.load_run("p1").scope is None


@pytest.mark.parametrize("scope, why", [
    (["receipt_thumbnailer"], "non-movable"),       # 🔴 Lambda: never in a scope
    (["nope"], "unknown"),
    ([], "empty scope"),
])
def test_bad_scopes_are_refused(world, source, scope, why):
    pipeline.plan(source, run_id="p1")
    with pytest.raises(PipelineError, match=why):
        pipeline.approve("p1", scope=scope)


# ---------- execute with a hybrid scope ----------

def test_hybrid_execute_migrates_only_the_scope(world, source, moves, llm):
    migrate_hybrid(source)
    s = pipeline.load_run("p1")
    assert s.scope is not None and s.scope.selfhosted == ["orders", "receipts"]
    # de-lock: db and storage move, the queue keeps boto3
    db, queue, storage = app_text("p1", "app/db.py"), app_text("p1", "app/queue.py"), app_text("p1", "app/storage.py")
    assert "kv.resource()" in db and "storage.s3_client()" in storage
    assert 'boto3.client("sqs")' in queue and "sovereign_sdk" not in queue
    assert not any(r.file == "app/queue.py" for r in s.rewrites)
    # the files, the data and the findings follow the scope
    assert moves.renders == [["orders", "receipts"]]
    assert moves.moves == [("aws->leaseweb", ["orders", "receipts"])]
    assert [c.component_id for c in s.data_moves[0].counts] == ["orders", "receipts"]
    hybrid = [f for f in s.assessment.findings if f.component_id == "hybrid"]  # type: ignore[union-attr]
    assert len(hybrid) == 1 and hybrid[0].light == "red"
    final = json.loads((root_of("p1") / "assessment.final.json").read_text(encoding="utf-8"))
    assert any(f["component_id"] == "hybrid" for f in final["findings"])
    assert (s.lock_in_before, s.lock_in_after) == (80, 12)    # both scored with [] (the fake returns 999 otherwise)


def test_full_scope_calls_the_other_roles_exactly_as_before(world, source, moves, llm):
    pipeline.migrate(source, yes=True, run_id="p1")
    assert moves.renders == [None] and moves.moves == [("aws->leaseweb", None)]
    s = pipeline.load_run("p1")
    assert s.scope is None and not any(f.component_id == "hybrid" for f in s.assessment.findings)  # type: ignore[union-attr]
    assert "queue.client()" in app_text("p1", "app/queue.py")


# ---------- the hybrid round trip ----------

def test_hybrid_round_trip_keeps_aws_and_copies_back_only_the_scope(trip, source, moves, llm):
    migrate_hybrid(source)
    trip.world.calls.clear()
    summary = pipeline.roundtrip("p1")
    assert summary.roundtrip == "passed"
    assert "lab.reset" not in trip.world.calls                      # the kept queue's data lives on AWS
    assert moves.moves[-1] == ("leaseweb->aws", ["orders", "receipts"])
    assert moves.lost_scopes == [["orders", "receipts"]]
    names = [c.name for c in summary.roundtrip_checks]
    assert "orders: AWS before + 1" in names and not any("order_events" in n for n in names)
    texts = [e.message for e in read_events(root_of("p1") / "events.jsonl")]
    assert any("AWS kept as is (order_events never left)" in t for t in texts)


# ---------- scope steps ----------

def test_move_over_then_move_back(world, source, moves, llm):
    migrate_hybrid(source)
    pipeline._set_active_stack("p1")
    moves.moves.clear()

    over = pipeline.move_over("p1", ["order_events"])
    assert over.status == "passed" and over.scope is None            # everything self-hosted now
    assert moves.moves == [("leaseweb->aws", ["orders", "receipts"]),  # current self-hosted data → AWS first
                           ("aws->leaseweb", None)]                     # then the new scope moves over
    assert "queue.client()" in app_text("p1", "app/queue.py")
    assert [(st.action, st.components, st.verification_ok) for st in over.steps] == [
        ("move_over", ["order_events"], True)]
    assert over.baseline is not None                                  # the baseline survives a step

    moves.moves.clear()
    back = pipeline.move_back("p1", ["orders"])
    assert back.status == "passed" and back.scope is not None and back.scope.selfhosted == ["order_events", "receipts"]
    assert moves.moves == [("leaseweb->aws", ["orders", "order_events", "receipts"]),
                           ("aws->leaseweb", ["order_events", "receipts"])]
    db = app_text("p1", "app/db.py")
    original = (root_of("p1") / "original" / "app" / "db.py").read_text(encoding="utf-8")
    assert db == original                                             # orders back on DynamoDB: the AWS code
    assert "queue.client()" in app_text("p1", "app/queue.py") and "storage.s3_client()" in app_text(
        "p1", "app/storage.py")
    assert [st.action for st in back.steps] == ["move_over", "move_back"]
    approval = Approval.model_validate_json((root_of("p1") / "approval.json").read_text(encoding="utf-8"))
    assert approval.scope == ["order_events", "receipts"]
    assert (root_of("p1") / "data" / "step-2-leaseweb-to-aws.json").is_file()


@pytest.mark.parametrize("action, components, why", [
    ("move_over", ["orders"], "already self-hosted"),
    ("move_back", ["order_events"], "nothing to move back"),
    ("move_back", ["orders", "receipts"], "boomerang roundtrip"),
])
def test_scope_steps_refuse_what_makes_no_sense(world, source, moves, llm, action, components, why):
    migrate_hybrid(source)
    pipeline._set_active_stack("p1")
    with pytest.raises(PipelineError, match=why):
        getattr(pipeline, action)("p1", components)


def test_scope_steps_need_a_passed_run_with_its_stack_up(world, source, moves, llm):
    migrate_hybrid(source)
    pipeline._set_active_stack("other")
    with pytest.raises(PipelineError, match="re-run `boomerang execute p1`"):
        pipeline.move_over("p1", ["order_events"])


# ---------- CLI ----------

def test_cli_only_move_over_and_move_back(world, source, moves, llm):
    runner = CliRunner()
    planned = runner.invoke(cli_app, ["plan", str(source), "--run-id", "p1"])
    assert planned.exit_code in (0, 4), planned.output                # the UI accepts both
    ok = runner.invoke(cli_app, ["approve", "p1", "--only", "orders, receipts"])
    assert ok.exit_code == 0 and "self-hosted: orders, receipts" in ok.output
    assert runner.invoke(cli_app, ["execute", "p1"]).exit_code == 0
    pipeline._set_active_stack("p1")
    over = runner.invoke(cli_app, ["move-over", "p1", "order_events"])
    assert over.exit_code == 0 and "self-hosted now: every movable component" in " ".join(over.output.split())
    back = runner.invoke(cli_app, ["move-back", "p1", "orders"])
    assert back.exit_code == 0 and "order_events, receipts" in back.output
    assert runner.invoke(cli_app, ["move-back", "p1", "receipts,order_events"]).exit_code == 3
    assert runner.invoke(cli_app, ["approve", "p1", "--only", "receipt_thumbnailer"]).exit_code == 3
