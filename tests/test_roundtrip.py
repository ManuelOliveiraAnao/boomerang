"""Round trip AWS → Leaseweb → AWS (docs/15 §7). Every other role is faked: no Docker, no moto, no model."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.stub import Stubber
from typer.testing import CliRunner

from boomerang import pipeline
from boomerang.cli import app as cli_app
from boomerang.events import read_events
from boomerang.models import DataCount, DataMove, GeneratedFile, LostItem, TestReport
from boomerang.pipeline import PipelineError
from tests.test_pipeline import (  # noqa: F401 - fixtures
    World,
    llm,
    make_report,
    messages,
    root_of,
    source,
    world,
)

ORDER_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def back_move(orders: int = 26, receipts: int = 26, messages_: int = 1, checksum: bool | None = True) -> DataMove:
    return DataMove(direction="leaseweb->aws", counts=[
        DataCount(component_id="orders", kind="kv_table", source_count=26, target_count=orders,
                  checksum_match=checksum),
        DataCount(component_id="receipts", kind="object_store", source_count=26, target_count=receipts,
                  checksum_match=True),
        DataCount(component_id="order_events", kind="queue", source_count=1, target_count=messages_)])


LOST = [LostItem(component_id="receipt_thumbnailer", feature="event_notification", status="lost",
                 detail="S3 → Lambda trigger has no Leaseweb equivalent"),
        LostItem(component_id="order_events", feature="dlq", status="changed", detail="RabbitMQ delivery-limit"),
        LostItem(component_id="orders", feature="basic", status="preserved", detail="26 rows, checksum ✅")]


@dataclass
class Trip:
    """The round trip's side of the fake world: the api container, DynamoDB lookups, the back move."""

    world: World
    health: int = 200
    post_status: int = 201
    back: DataMove = field(default_factory=back_move)
    aws_tests: TestReport = field(default_factory=lambda: make_report("aws", 99))
    in_dynamodb: bool = True
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    lookups: list[dict[str, Any]] = field(default_factory=list)
    lost_args: list[Any] = field(default_factory=list)

    def http(self, method: str, path: str, **kwargs: Any) -> Any:
        self.world.calls.append(f"http:{method} {path}")
        self.requests.append((method, path, kwargs.get("json")))
        status = self.health if path == "/health" else self.post_status
        body = {"status": "ok"} if path == "/health" else {"order_id": ORDER_ID, "status": "pending",
                                                           "customer_id": kwargs["json"]["customer_id"]}
        return _Resp(status, body)

    def move(self, pam, direction, src, dst, emit) -> DataMove:
        self.world.calls.append(f"move:{direction}")
        return self.world.move if direction == "aws->leaseweb" else self.back

    def run_tests(self, app_dir: Path, env: dict, platform: str, attempt: int, emit: Any) -> TestReport:
        self.world.calls.append(f"run_tests:{platform}:{app_dir.name}:{attempt}")
        return self.world.baseline if attempt == 0 else self.aws_tests

    def has(self, table, pk, value, env) -> tuple[bool, str]:
        self.world.calls.append("dynamodb:get")
        self.lookups.append({"table": table.resource_name, "pk": pk, "value": value})
        return self.in_dynamodb, "DynamoDB table orders"

    def lost(self, pam, assessment, data_moves) -> list[LostItem]:
        self.world.calls.append("lost.build")
        self.lost_args.append((assessment, data_moves))
        return LOST


@dataclass
class _Resp:
    status_code: int
    body: Any

    def json(self) -> Any:
        return self.body


@pytest.fixture
def trip(world: World, monkeypatch: pytest.MonkeyPatch) -> Trip:
    t = Trip(world)
    monkeypatch.setattr(pipeline, "_http", t.http)
    monkeypatch.setattr(pipeline, "_dynamodb_has", t.has)
    monkeypatch.setattr("boomerang.datamover.move", t.move)
    monkeypatch.setattr("boomerang.verifier.run_tests", t.run_tests)
    monkeypatch.setattr("boomerang.lost.build", t.lost)
    return t


@pytest.fixture
def migrated(trip: Trip, source: Path) -> str:
    """A passed migration whose stack is the active one."""
    summary = pipeline.migrate(source, yes=True, run_id="r1")
    assert summary.status == "passed" and pipeline.active_stack() == "r1"
    trip.world.calls.clear()
    return "r1"


# ---------- the happy path ----------

def test_round_trip_passes(trip, migrated):
    summary = pipeline.roundtrip(migrated)
    root = root_of(migrated)

    assert summary.roundtrip == "passed" and summary.status == "passed"
    assert pipeline.load_run(migrated) == summary
    assert trip.world.calls == [
        "http:GET /health",
        "stop:worker",                                     # freeze the consumer before the new order
        "http:POST /orders",
        "lab.reset", "provision",                          # fresh AWS, no seed
        "move:leaseweb->aws",
        "dynamodb:get",                                    # counts and the new order before any test runs
        "run_tests:aws:original:99",
        "lost.build",
        "start:worker",
    ]
    assert trip.requests[1] == ("POST", "/orders", {"customer_id": "rt-r1", "items": [
        {"sku": "BOOMERANG-RT", "qty": 1, "price_cents": pipeline.ROUNDTRIP_PRICE_CENTS}]})
    checks = {c.name: (c.ok, c.detail) for c in summary.roundtrip_checks}
    assert list(checks) == ["new order written on Leaseweb", "orders: AWS before + 1", "receipts: AWS before + 1",
                            "order_events: pending messages copied back", "new order exists on AWS"]
    assert all(ok for ok, _ in checks.values())
    assert checks["orders: AWS before + 1"][1] == "AWS 25 → Leaseweb 26 → AWS 26 (expected 26), checksum ✅"
    assert checks["order_events: pending messages copied back"][1] == "1 pending on Leaseweb → 1 on AWS"
    assert trip.lookups == [{"table": "orders", "pk": "order_id", "value": ORDER_ID}]
    # outputs for D's tab 4 and the report
    assert summary.roundtrip_tests is not None and summary.roundtrip_tests.attempt == 99
    assert summary.data_moves == [trip.world.move, trip.back]
    assert summary.lost == LOST
    assert json.loads((root / "lost.json").read_text(encoding="utf-8"))[0]["status"] == "lost"
    assert DataMove.model_validate_json((root / "data" / "leaseweb-to-aws.json").read_text(encoding="utf-8")) == trip.back
    assert json.loads((root / "data" / "roundtrip-writes.json").read_text(encoding="utf-8")) == [ORDER_ID]
    # lost.build gets the final assessment (with the de-lock findings) and both moves
    assessment, moves = trip.lost_args[0]
    assert moves == [trip.world.move, trip.back]
    assert assessment.model_dump() == json.loads((root / "assessment.final.json").read_text(encoding="utf-8"))
    report = (root / "report.md").read_text(encoding="utf-8")
    assert "## 6. Round trip back to AWS" in report and "✅ **passed**" in report.split("## 6.")[1]
    assert "fresh (emulated) AWS environment" in report and "| new order exists on AWS | ✅ |" in report
    assert "❌ lost · receipt_thumbnailer" in report


def test_round_trip_events_use_valid_stages_and_every_step_starts_and_ends(trip, migrated):
    pipeline.roundtrip(migrated)
    events = read_events(root_of(migrated) / "events.jsonl")               # validates every Stage
    start = next(i for i, e in enumerate(events) if e.message.startswith("round trip r1:"))
    tail = events[start:]
    steps = [(e.data or {}).get("step") for e in tail if (e.data or {}).get("phase") == "start"]
    assert steps == ["freeze worker", "write on Leaseweb", "fresh AWS", "copy back", "checks",
                     "original tests on AWS", "what got lost", "report"]
    assert {e.stage for e in tail} == {"rollback", "data", "report"}


def test_a_second_round_trip_expects_both_orders(trip, migrated):
    pipeline.roundtrip(migrated)
    trip.back = back_move(orders=27, receipts=27)
    trip.back.counts[0].source_count = trip.back.counts[1].source_count = 27
    summary = pipeline.roundtrip(migrated)
    assert summary.roundtrip == "passed"
    detail = {c.name: c.detail for c in summary.roundtrip_checks}["orders: AWS before + 2"]
    assert detail == "AWS 25 → Leaseweb 27 → AWS 27 (expected 27), checksum ✅"
    assert len(summary.data_moves) == 2


# ---------- failures are results, not errors ----------

@pytest.mark.parametrize("change, failed", [
    (lambda t: setattr(t, "back", back_move(orders=25)), "orders: AWS before + 1"),
    (lambda t: setattr(t, "back", back_move(checksum=False)), "orders: AWS before + 1"),
    (lambda t: setattr(t, "back", back_move(messages_=0)), "order_events: pending messages copied back"),
    (lambda t: setattr(t, "in_dynamodb", False), "new order exists on AWS"),
    (lambda t: setattr(t, "aws_tests", make_report("aws", 99, passed=6, failed=1)), "original tests on AWS 6/7"),
])
def test_a_failed_check_fails_the_round_trip_not_the_migration(trip, migrated, change, failed):
    change(trip)
    summary = pipeline.roundtrip(migrated)
    assert summary.roundtrip == "failed" and summary.status == "passed"
    assert any(failed in m for m in messages(migrated, "error"))
    assert "start:worker" in trip.world.calls


def test_an_error_mid_way_marks_the_round_trip_error_and_restarts_the_worker(trip, migrated, monkeypatch):
    def broken_reset() -> None:
        raise RuntimeError("moto is gone")
    monkeypatch.setattr("boomerang.lab.reset", broken_reset)
    with pytest.raises(RuntimeError, match="moto is gone"):
        pipeline.roundtrip(migrated)
    saved = pipeline.load_run(migrated)
    assert saved.roundtrip == "error" and saved.status == "passed"
    assert trip.world.calls[-1] == "start:worker"
    assert any("fresh AWS failed: RuntimeError: moto is gone" in m for m in messages(migrated, "error"))
    assert "❌ **error**" in (root_of(migrated) / "report.md").read_text(encoding="utf-8")


def test_post_that_is_not_201_stops_with_the_reason(trip, migrated):
    trip.post_status = 500
    with pytest.raises(PipelineError, match="expected 201"):
        pipeline.roundtrip(migrated)
    assert pipeline.load_run(migrated).roundtrip == "error"
    assert "lab.reset" not in trip.world.calls                  # AWS untouched when nothing was written


# ---------- preconditions ----------

def _set_status(run_id: str, status: str) -> None:
    s = pipeline.load_run(run_id)
    s.status = status  # type: ignore[assignment]
    (root_of(run_id) / "run.json").write_text(s.model_dump_json(), encoding="utf-8")


@pytest.mark.parametrize("setup, why", [
    (lambda t, r: _set_status(r, "failed"), "is failed: the round trip needs a passed migration"),
    (lambda t, r: pipeline._set_active_stack("other"), "belongs to other, not r1"),
    (lambda t, r: pipeline._set_active_stack(None), "belongs to no run, not r1"),
    (lambda t, r: (root_of(r) / "data" / "aws-to-leaseweb.json").unlink(), "aws-to-leaseweb.json is missing"),
    (lambda t, r: setattr(t, "health", 503), "health is 503, not 200"),
])
def test_preconditions_say_re_run_execute(trip, migrated, setup, why):
    setup(trip, migrated)
    with pytest.raises(PipelineError, match="re-run `boomerang execute r1`") as info:
        pipeline.roundtrip(migrated)
    assert why in str(info.value)
    assert not any(c.startswith(("stop:", "http:POST", "lab.")) for c in trip.world.calls)
    assert pipeline.load_run(migrated).roundtrip == "not_run"
    assert any(why in m for m in messages(migrated, "error"))


def test_unreachable_api_is_a_precondition(trip, migrated, monkeypatch):
    def down(method: str, path: str, **kwargs: Any) -> Any:
        raise ConnectionError("refused")
    monkeypatch.setattr(pipeline, "_http", down)
    with pytest.raises(PipelineError, match="api not reachable at http://127.0.0.1:8080"):
        pipeline.roundtrip(migrated)


# ---------- CP2 cut line: no api container ----------

def test_without_an_api_container_the_order_goes_through_a_testclient_subprocess(trip, source, monkeypatch):
    trip.world.generated = [GeneratedFile(path="docker-compose.yml", content="services:\n  postgres: {}\n"),
                            *[g for g in trip.world.generated if g.path != "docker-compose.yml"]]
    pipeline.migrate(source, yes=True, run_id="r1")
    seen: list[Any] = []

    def post(app_dir: Path, env: dict, entrypoint: str, payload: dict) -> tuple[int, Any]:
        seen.append((app_dir, env, entrypoint, payload))
        return 201, {"order_id": ORDER_ID}
    monkeypatch.setattr(pipeline, "_post_order_subprocess", post)
    summary = pipeline.roundtrip("r1")
    assert summary.roundtrip == "passed"
    assert not any(r[1] == "/health" for r in trip.requests)       # no api: no health to ask
    app_dir, env, entrypoint, payload = seen[0]
    assert app_dir == root_of("r1") / "leaseweb" / "app" and env == {"DATABASE_URL": "postgresql://127.0.0.1:5432/x"}
    assert entrypoint == "app.main:app" and payload["customer_id"] == "rt-r1"
    assert any("CP2 cut line" in m for m in messages("r1", "warning"))


def test_testclient_subprocess_runs_the_app_with_only_allowlisted_env(tmp_path, monkeypatch):
    app = tmp_path / "app_dir"
    (app / "app").mkdir(parents=True)
    (app / "app" / "__init__.py").write_text("", encoding="utf-8")
    (app / "app" / "main.py").write_text(textwrap.dedent("""\
        import os
        from fastapi import FastAPI
        app = FastAPI()

        @app.post("/orders", status_code=201)
        def create(order: dict):
            return {"order_id": "abc", "customer_id": order["customer_id"], "db": os.environ.get("DATABASE_URL"),
                    "leaked": sorted(k for k in os.environ if "ANTHROPIC" in k or k.startswith("BOOMERANG_"))}
    """), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-the-app")
    monkeypatch.setenv("BOOMERANG_LLM_PROVIDER", "anthropic")
    status, body = pipeline._post_order_subprocess(app, {"DATABASE_URL": "postgresql://127.0.0.1/x"},
                                                   "app.main:app", {"customer_id": "rt-1", "items": []})
    assert status == 201
    assert body == {"order_id": "abc", "customer_id": "rt-1", "db": "postgresql://127.0.0.1/x", "leaked": []}


@pytest.mark.parametrize("entrypoint", ["app.main:app; rm -rf /", "../x:app", "app main"])
def test_bad_entrypoints_are_refused(tmp_path, entrypoint):
    with pytest.raises(PipelineError, match="is not 'module:attribute'"):
        pipeline._post_order_subprocess(tmp_path, {}, entrypoint, {})


def test_subprocess_env_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("AWS_PROFILE", "prod")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = pipeline.subprocess_env({"DATABASE_URL": "postgresql://127.0.0.1/x"})
    assert env["PATH"] == "/usr/bin" and env["PYTHONUTF8"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["DATABASE_URL"] == "postgresql://127.0.0.1/x"
    assert "ANTHROPIC_API_KEY" not in env and "AWS_PROFILE" not in env


# ---------- the DynamoDB lookup (plain boto3, L17) ----------

def test_dynamodb_lookup_uses_the_aws_env_and_a_consistent_read(monkeypatch):
    import boto3

    from tests.test_delock import MINI_APP, mini_pam

    client = boto3.client("dynamodb", region_name="us-east-1", endpoint_url="http://127.0.0.1:4566",
                          aws_access_key_id="test", aws_secret_access_key="test")
    seen_env: list[dict] = []
    monkeypatch.setattr(pipeline, "_dynamodb_client", lambda env: (seen_env.append(env), client)[1])
    table = next(c for c in mini_pam(MINI_APP).components if c.id == "orders")
    env = {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "ORDERS_TABLE": "orders-rt"}
    with Stubber(client) as stub:
        stub.add_response("get_item", {"Item": {"order_id": {"S": ORDER_ID}}},
                          {"TableName": "orders-rt", "Key": {"order_id": {"S": ORDER_ID}}, "ConsistentRead": True})
        stub.add_response("get_item", {}, {"TableName": "orders-rt", "Key": {"order_id": {"S": "nope"}},
                                           "ConsistentRead": True})
        assert pipeline._dynamodb_has(table, "order_id", ORDER_ID, env) == (True, "DynamoDB table orders-rt")
        assert pipeline._dynamodb_has(table, "order_id", "nope", env)[0] is False
    assert seen_env == [env, env]


def test_dynamodb_client_reads_the_endpoint_from_the_aws_env():
    client = pipeline._dynamodb_client({"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "AWS_REGION": "eu-west-1"})
    assert client.meta.endpoint_url == "http://127.0.0.1:4566" and client.meta.region_name == "eu-west-1"


def test_has_api_reads_the_compose_file(tmp_path):
    assert pipeline._has_api(tmp_path) is True                                # unknown: try HTTP
    (tmp_path / "docker-compose.yml").write_text("services:\n  api: {}\n  worker: {}\n", encoding="utf-8")
    assert pipeline._has_api(tmp_path) is True
    (tmp_path / "docker-compose.yml").write_text("services:\n  worker: {}\n", encoding="utf-8")
    assert pipeline._has_api(tmp_path) is False


# ---------- fixture mode and the CLI ----------

def test_lost_not_built_yet_in_fixture_mode_is_skipped_and_said(trip, migrated, monkeypatch):
    from boomerang._stub import NotBuilt
    from boomerang.config import get_settings

    def not_built(pam, assessment, data_moves):
        raise NotBuilt("lost.build", owner="B")
    monkeypatch.setattr("boomerang.lost.build", not_built)
    monkeypatch.setenv("BOOMERANG_ALLOW_FIXTURES", "1")
    monkeypatch.setattr(pipeline, "fixtures_dir", lambda: root_of(migrated) / "no-fixtures")
    get_settings.cache_clear()
    summary = pipeline.roundtrip(migrated)
    assert summary.roundtrip == "passed" and summary.lost == []
    assert "what got lost" in summary.fixtures_used
    assert any("what got lost: skipped (lost.build not built yet, owner B)" in m for m in messages(migrated))


def test_round_trip_refuses_a_run_verified_on_a_fixture(trip, migrated):
    s = pipeline.load_run(migrated)
    s.fixtures_used.append("verify")
    (root_of(migrated) / "run.json").write_text(s.model_dump_json(), encoding="utf-8")
    with pytest.raises(PipelineError, match="verified on a fixture"):
        pipeline.roundtrip(migrated)


def test_cli_roundtrip_exit_codes(trip, migrated):
    runner = CliRunner()
    ok = runner.invoke(cli_app, ["roundtrip", migrated])
    assert ok.exit_code == 0, ok.output
    assert "round trip r1: passed" in ok.output
    trip.in_dynamodb = False
    assert runner.invoke(cli_app, ["roundtrip", migrated]).exit_code == 1
    pipeline._set_active_stack("other")
    assert runner.invoke(cli_app, ["roundtrip", migrated]).exit_code == 3
