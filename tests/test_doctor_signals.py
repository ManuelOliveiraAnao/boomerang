"""Doctor piece 1 (owner A, docs/18 §3): signals.collect(), actions.apply(), break_service(). No Docker needed."""
import json
import subprocess
from pathlib import Path

import pytest

from boomerang import verifier
from boomerang.doctor import actions, signals
from boomerang.models import Action, Check, RunSummary, Signal, Verification

ROOT = Path(__file__).resolve().parent.parent
MIN = ROOT / "tests" / "fixtures" / "leaseweb-min"

PS_ALL_UP = [{"Service": s, "State": "running", "Health": "healthy" if s != "worker" else "", "Status": "Up 2 minutes",
              "ExitCode": 0} for s in signals.SERVICES]


def _ps(rows, lines=True) -> str:
    return "\n".join(json.dumps(r) for r in rows) if lines else json.dumps(rows)


class FakeCompose:
    """Answers `docker compose … ps|logs|restart|start|stop` with canned output and records every call."""

    def __init__(self, ps_rows=PS_ALL_UP, logs=None, rc=0, lines=True):
        self.ps_rows, self.logs, self.rc, self.lines, self.calls = ps_rows, logs or {}, rc, lines, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "ps" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=_ps(self.ps_rows, self.lines), stderr="")
        if "logs" in cmd:
            svc = cmd[-1]
            out = "\n".join(f"{svc}-1  | {ln}" for ln in self.logs.get(svc, ["started"]))
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, self.rc, stdout="", stderr="" if self.rc == 0 else "boom")


def _postgres_down():
    rows = [dict(r) for r in PS_ALL_UP]
    pg = next(r for r in rows if r["Service"] == "postgres")
    pg.update(State="exited", Health="", Status="Exited (0) 5 seconds ago", ExitCode=0)
    logs = {"api": ["INFO started", 'psycopg.OperationalError: connection to server at "postgres" (172.18.0.2), '
                    "port 5432 failed: Connection refused"],
            "postgres": ["database system is shut down"]}
    return FakeCompose(rows, logs)


def _refuse(service):
    def connect(host, port, timeout):
        if port == {"postgres": 5432, "rabbitmq": 5672, "seaweedfs": 8333, "api": 8080}[service]:
            raise ConnectionRefusedError(61, "Connection refused")
    return connect


@pytest.fixture(autouse=True)
def fast_health(monkeypatch):
    """check_health polls for seconds; the tests decide its answer."""
    monkeypatch.setattr(verifier, "check_health",
                        lambda url, timeout_s=30: Check(name="health", ok=False, detail="health check failed: HTTP 500"))


# ---------------------------------------------------------------- containers
@pytest.mark.parametrize("lines", [True, False])          # Compose prints JSON lines (≥ 2.21) or one array
def test_container_signals_read_both_ps_formats(lines):
    sig = signals.container_signals(MIN, FakeCompose(lines=lines))
    assert [s.service for s in sig] == list(signals.SERVICES)
    assert all(s.ok and s.source == "container" for s in sig)


def test_exited_unhealthy_and_missing_containers_are_not_ok():
    rows = [r for r in PS_ALL_UP if r["Service"] != "worker"]                 # worker never created
    rows = [dict(r, State="exited", Status="Exited (1)", ExitCode=1) if r["Service"] == "postgres" else r for r in rows]
    rows = [dict(r, Health="unhealthy") if r["Service"] == "rabbitmq" else r for r in rows]
    by = {s.service: s for s in signals.container_signals(MIN, FakeCompose(rows))}
    assert not by["postgres"].ok and by["postgres"].data["exit_code"] == 1
    assert not by["rabbitmq"].ok and by["rabbitmq"].data["health"] == "unhealthy"
    assert not by["worker"].ok and by["worker"].detail == "not created"
    assert by["api"].ok


def test_docker_missing_becomes_one_failing_signal():
    def no_docker(cmd, **kw):
        raise FileNotFoundError("docker")
    [s] = signals.container_signals(MIN, no_docker)
    assert not s.ok and s.service == "stack" and "docker not available" in s.detail


# ---------------------------------------------------------------- logs
def test_logs_strip_the_compose_prefix_and_flag_errors():
    s = signals.log_signal(MIN, "api", _postgres_down())
    assert s.lines[0] == "INFO started"                      # "api-1  | " removed
    assert not s.ok and s.data["errors"] == 1
    assert signals.log_signal(MIN, "worker", FakeCompose()).ok


def test_logs_keep_only_the_last_40_lines():
    many = FakeCompose(logs={"api": [f"line {i}" for i in range(100)]})
    assert len(signals.log_signal(MIN, "api", many).lines) == signals.LOG_TAIL


# ---------------------------------------------------------------- probes and queues
def test_probe_reports_a_refused_port():
    by = {s.service: s for s in signals.probe_signals(_refuse("postgres"))}
    assert not by["postgres"].ok and "connection refused" in by["postgres"].detail
    assert by["rabbitmq"].ok and by["api"].ok


def _laptop_env(tmp_path: Path) -> Path:
    (tmp_path / "laptop.env").write_text("AMQP_URL=amqp://shoplite:s3cret@127.0.0.1:5672/shoplite\n", encoding="utf-8")
    return tmp_path


def test_queue_waiting_without_consumers_is_stalled_but_the_dlq_is_not(tmp_path):
    seen = {}

    def fetch(url, headers, timeout):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return [{"name": "order-events", "messages": 12, "consumers": 0},
                {"name": "order-events-dlq", "messages": 3, "consumers": 0}]
    by = {s.data["queue"]: s for s in signals.queue_signals(_laptop_env(tmp_path), fetch)}
    assert seen["url"].endswith("/api/queues/shoplite") and seen["auth"].startswith("Basic ")
    assert not by["order-events"].ok and by["order-events"].data["depth"] == 12
    assert by["order-events-dlq"].ok


def test_queue_signals_are_skipped_when_the_management_api_is_down(tmp_path):
    def down(url, headers, timeout):
        raise ConnectionRefusedError()
    assert signals.queue_signals(_laptop_env(tmp_path), down) == []


# ---------------------------------------------------------------- collect: the "done when" of docs/18 §3
def test_collect_with_postgres_stopped(tmp_path):
    sig = signals.collect(_laptop_env(tmp_path), _postgres_down(), connect=_refuse("postgres"),
                          fetch=lambda *a: [{"name": "order-events", "messages": 0, "consumers": 1}])
    failing = {(s.source, s.service) for s in sig if not s.ok}
    assert ("container", "postgres") in failing
    assert ("probe", "postgres") in failing
    assert ("health", "api") in failing
    api_logs = next(s for s in sig if s.source == "logs" and s.service == "api")
    assert any("Connection refused" in ln for ln in api_logs.lines)
    assert any(s.source == "logs" and s.service == "postgres" for s in sig)   # logs of what is down
    assert not signals.healthy(sig)


# ---------------------------------------------------------------- actions
def test_restart_sends_exactly_docker_compose_restart():
    fake = FakeCompose()
    a = Action(id="restart_service", target="postgres", label="Restart postgres", risk="low")
    r = actions.apply("run-1", MIN, a, by="cli", run=fake)
    assert r["ok"]
    assert verifier._compose(MIN) + ["restart", "postgres"] in fake.calls
    assert all("restart" not in c or c[-1] == "postgres" for c in fake.calls)    # nothing else restarted


def test_restart_waits_until_the_service_is_healthy_again():
    """Real Docker shows "health: starting" right after a restart; ok means ready, not just started."""
    class Starting(FakeCompose):
        def __init__(self):
            super().__init__()
            self.ps_calls = 0

        def __call__(self, cmd, **kw):
            if "ps" in cmd:
                self.ps_calls += 1
                self.ps_rows = [dict(r, Health="starting") if r["Service"] == "postgres" and self.ps_calls < 3
                                else dict(r) for r in PS_ALL_UP]
            return super().__call__(cmd, **kw)
    fake = Starting()
    a = Action(id="restart_service", target="postgres", label="Restart postgres", risk="low")
    r = actions.apply("run-1", MIN, a, by="cli", run=fake)
    assert r["ok"] and "healthy after" in r["detail"] and fake.ps_calls == 3


def test_restart_reports_a_service_that_never_gets_healthy():
    rows = [dict(r, Health="unhealthy") if r["Service"] == "postgres" else r for r in PS_ALL_UP]
    a = Action(id="restart_service", target="postgres", label="Restart postgres", risk="low")
    r = actions.apply("run-1", MIN, a, by="cli", run=FakeCompose(rows), ready_timeout_s=0)
    assert not r["ok"] and "not healthy" in r["detail"]


def test_a_failing_restart_reports_it():
    a = Action(id="start_service", target="worker", label="Start worker", risk="low")
    r = actions.apply("run-1", MIN, a, by="ui", run=FakeCompose(rc=1))
    assert not r["ok"] and "boom" in r["detail"]


def test_unknown_service_and_unknown_action_are_refused():
    with pytest.raises(ValueError, match="unknown service"):
        actions.apply("run-1", MIN, Action(id="restart_service", target="postgres; rm -rf /", label="x", risk="low"),
                      by="cli", run=FakeCompose())
    rogue = Action.model_construct(id="exec_shell", target="api", label="x", risk="low")   # not in the Literal
    with pytest.raises(ValueError, match="not allowed"):
        actions.apply("run-1", MIN, rogue, by="cli", run=FakeCompose())


def test_reverify_uses_the_verifier(monkeypatch):
    monkeypatch.setattr(verifier, "verify", lambda d, emit=None, run=None: Verification(
        checks=[Check(name="boot", ok=True), Check(name="health", ok=False)]))
    r = actions.apply("run-1", MIN, Action(id="reverify", target="stack", label="Re-verify", risk="low"),
                      by="cli", run=FakeCompose())
    assert not r["ok"] and "health" in r["detail"]


def test_move_back_calls_the_pipeline_step(monkeypatch):
    from boomerang import pipeline
    calls = {}

    def move_back(run_id, components, *, by="cli"):
        calls.update(run_id=run_id, components=components, by=by)
        return RunSummary(run_id=run_id, source_dir="x", status="passed", started_at=0, out_dir="x")
    monkeypatch.setattr(pipeline, "move_back", move_back)
    r = actions.apply("run-1", MIN, Action(id="move_back", target="orders", label="Move orders back", risk="high"),
                      by="ui", run=FakeCompose())
    assert r["ok"] and calls == {"run_id": "run-1", "components": ["orders"], "by": "ui"}


def test_actions_emit_doctor_events():
    events = []

    class Sink:
        def emit(self, stage, message, level="info", **data):
            events.append((stage, level, message))
    actions.apply("run-1", MIN, Action(id="restart_service", target="api", label="Restart api", risk="low"),
                  by="ui", emit=Sink(), run=FakeCompose())
    assert [e[0] for e in events] == ["doctor", "doctor"] and events[-1][1] == "success"


# ---------------------------------------------------------------- break (demo only)
def test_break_stops_the_service_on_the_local_stack(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    fake = FakeCompose()
    actions.break_service(MIN, "postgres", fake)
    assert fake.calls[-1] == verifier._compose(MIN) + ["stop", "postgres"]


def test_break_refuses_a_remote_docker_and_unknown_services(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://admin@prod-server")
    with pytest.raises(PermissionError):
        actions.break_service(MIN, "postgres", FakeCompose())
    monkeypatch.delenv("DOCKER_HOST")
    with pytest.raises(ValueError):
        actions.break_service(MIN, "everything", FakeCompose())
