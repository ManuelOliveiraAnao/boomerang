"""Doctor, piece 2 (B): diagnosis rules (docs/18 §4.2). Synthetic signals, the real ShopLite source for routes."""

from __future__ import annotations

from pathlib import Path

import pytest

from boomerang.doctor import rules
from boomerang.models import AppModel, CodeRef, Component, Scope, Signal

SHOPLITE = Path(__file__).resolve().parents[1] / "sample-app" / "aws"


def _ref(file: str, symbol: str) -> CodeRef:
    return CodeRef(file=file, line=1, symbol=symbol, operation="call", snippet="")


PAM = AppModel(app_name="ShopLite", platform="aws", entrypoints=["app.main:app"], files=[], env=[], components=[
    Component(id="orders", kind="kv_table", provider="aws.dynamodb", resource_name="orders",
              usages=[_ref("app/db.py", s) for s in ("put_order", "get_order", "list_orders_by_customer")]),
    Component(id="order_events", kind="queue", provider="aws.sqs", resource_name="order-events",
              usages=[_ref("app/queue.py", "publish_order_created")]),
    Component(id="receipts", kind="object_store", provider="aws.s3", resource_name="receipts",
              usages=[_ref("app/storage.py", s) for s in ("put_receipt", "get_receipt")]),
    Component(id="receipt_thumbnailer", kind="function", provider="aws.lambda", resource_name="thumb"),
])

SERVICES = ("postgres", "rabbitmq", "seaweedfs", "api", "worker")


def healthy() -> list[Signal]:
    return ([Signal(source="container", service=s, ok=True, detail="Up") for s in SERVICES]
            + [Signal(source="logs", service=s, ok=True) for s in ("api", "worker")]
            + [Signal(source="health", service="api", ok=True)]
            + [Signal(source="probe", service=s, ok=True) for s in ("postgres", "rabbitmq", "seaweedfs", "api")])


def with_(*changes: Signal) -> list[Signal]:
    """Healthy signals with some replaced (same source + service) or added."""
    out = healthy()
    for c in changes:
        idx = next((i for i, s in enumerate(out) if (s.source, s.service) == (c.source, c.service)), None)
        if idx is None:
            out.append(c)
        else:
            out[idx] = c
    return out


def down(service: str) -> list[Signal]:
    return [Signal(source="container", service=service, ok=False, detail="Exited (0)", data={"state": "exited"}),
            Signal(source="probe", service=service, ok=False, detail="127.0.0.1:1: connection refused")]


API_REFUSED = Signal(source="logs", service="api", ok=False,
                     lines=["INFO started", "psycopg.OperationalError: connection to server failed: Connection refused"])
HEALTH_500 = Signal(source="health", service="api", ok=False, detail="GET /health 500")


def _ids(d) -> list[tuple[str, str]]:
    return [(a.id, a.target) for a in d.actions]


# ---------- one test per rule ----------

def test_db_down():
    d = rules.diagnose(with_(*down("postgres"), API_REFUSED, HEALTH_500), PAM, None, app_dir=SHOPLITE)
    assert (d.rule, d.service, d.component_id, d.severity) == ("db_down", "postgres", "orders", "down")
    assert _ids(d) == [("restart_service", "postgres"), ("move_back", "orders"), ("reverify", "stack")]
    assert "Create order (POST /orders)" in d.impact and "Get order (GET /orders/{order_id})" in d.impact
    assert "List customer orders (GET /customers/{customer_id}/orders)" in d.impact
    assert {(s.source, s.service) for s in d.evidence} == {("container", "postgres"), ("probe", "postgres"),
                                                            ("logs", "api"), ("health", "api")}


def test_db_down_needs_the_app_to_be_hurt():
    d = rules.diagnose(with_(*down("postgres")), PAM, None)
    assert d.rule == "unknown"


def test_queue_down():
    d = rules.diagnose(with_(*down("rabbitmq")), PAM, None, app_dir=SHOPLITE)
    assert (d.rule, d.component_id, d.severity) == ("queue_down", "order_events", "degraded")
    assert _ids(d)[0] == ("restart_service", "rabbitmq") and ("move_back", "order_events") in _ids(d)
    assert d.impact == ["Create order (POST /orders)"]


def test_storage_down():
    d = rules.diagnose(with_(*down("seaweedfs")), PAM, None, app_dir=SHOPLITE)
    assert (d.rule, d.component_id, d.severity) == ("storage_down", "receipts", "degraded")
    assert "Get receipt (GET /orders/{order_id}/receipt)" in d.impact


def test_worker_stalled_with_waiting_messages():
    stuck = Signal(source="queue", service="rabbitmq", ok=False, detail="order-events: 120 waiting, 0 consumer(s)",
                   data={"queue": "order-events", "depth": 120, "consumers": 0})
    d = rules.diagnose(with_(stuck), PAM, None)
    assert (d.rule, d.service, d.severity) == ("worker_stalled", "worker", "degraded")
    assert "120 message(s) are waiting" in d.root_cause
    assert _ids(d) == [("start_service", "worker"), ("reverify", "stack")]


def test_worker_stalled_when_its_container_stopped():
    d = rules.diagnose(with_(Signal(source="container", service="worker", ok=False, detail="Exited (1)")), PAM, None)
    assert d.rule == "worker_stalled" and "new orders aren't being processed" in d.root_cause


def test_config_error_names_the_setting():
    logs = Signal(source="logs", service="api", ok=False,
                  lines=["Traceback (most recent call last):", "  File \"app/db.py\", line 9", "KeyError: 'ORDERS_TABLE'"])
    crashed = Signal(source="container", service="api", ok=False, detail="Restarting (1)")
    d = rules.diagnose(with_(crashed, logs, HEALTH_500), PAM, None)
    assert (d.rule, d.service, d.severity) == ("config_error", "api", "down")
    assert "ORDERS_TABLE" in d.root_cause and _ids(d) == [("reverify", "stack")]


def test_app_crash_quotes_the_last_exception():
    logs = Signal(source="logs", service="api", ok=False,
                  lines=["Traceback (most recent call last):", "  File \"app/main.py\", line 3",
                         "ModuleNotFoundError: No module named 'sovereign_sdk'"])
    crashed = Signal(source="container", service="api", ok=False, detail="Exited (1)")
    d = rules.diagnose(with_(crashed, logs), PAM, None)
    assert (d.rule, d.severity) == ("app_crash", "down")
    assert "ModuleNotFoundError: No module named 'sovereign_sdk'" in d.root_cause
    assert _ids(d) == [("restart_service", "api"), ("reverify", "stack")]


def test_unknown():
    d = rules.diagnose(with_(HEALTH_500), PAM, None)
    assert (d.rule, d.severity, _ids(d)) == ("unknown", "degraded", [("reverify", "stack")])
    assert d.evidence == [HEALTH_500]


def test_stack_unreachable_when_docker_cant_be_read():
    no_docker = [Signal(source="container", service="stack", ok=False, detail="docker not available: not found")]
    d = rules.diagnose(no_docker + [HEALTH_500], PAM, None)
    assert (d.rule, d.severity) == ("stack_unreachable", "down") and "docker not available" in d.root_cause


# ---------- order, hybrid runs, move back ----------

def test_db_down_beats_app_crash():
    crashed = Signal(source="container", service="api", ok=False, detail="Restarting (1)")
    logs = Signal(source="logs", service="api", ok=False, lines=["Traceback", "OperationalError: connection refused"])
    d = rules.diagnose(with_(*down("postgres"), crashed, logs), PAM, None)
    assert d.rule == "db_down"


def test_hybrid_ignores_services_of_components_on_aws():
    scope = Scope(selfhosted=["order_events", "receipts"])            # orders stays on AWS: no postgres here
    signals = with_(Signal(source="container", service="postgres", ok=False, detail="not created"),
                    Signal(source="probe", service="postgres", ok=False, detail="connection refused"))
    assert all(s.ok for s in rules.relevant(signals, PAM, scope))
    assert {s.service for s in rules.relevant(signals, PAM, scope)} == {"rabbitmq", "seaweedfs", "api", "worker"}
    d = rules.diagnose(rules.relevant(signals, PAM, scope) + [HEALTH_500], PAM, scope)
    assert d.rule == "unknown"                                          # never db_down on a hybrid run without postgres


def test_hybrid_never_offers_move_back_for_a_component_on_aws():
    scope = Scope(selfhosted=["order_events", "receipts"])
    d = rules.diagnose(with_(*down("rabbitmq")), PAM, scope)
    assert ("move_back", "order_events") in _ids(d)
    assert all(target != "orders" for _, target in _ids(d))


def test_move_back_not_offered_for_the_last_selfhosted_component():
    d = rules.diagnose(with_(*down("postgres"), API_REFUSED), PAM, Scope(selfhosted=["orders"]))
    assert d.rule == "db_down" and "move_back" not in [a for a, _ in _ids(d)]


@pytest.mark.parametrize("app_dir", [None, Path("does-not-exist")])
def test_impact_falls_back_to_the_component(app_dir):
    d = rules.diagnose(with_(*down("postgres"), API_REFUSED), PAM, None, app_dir=app_dir)
    assert d.impact == ["Everything that uses orders"]


def test_every_action_is_on_the_allowlist():
    """docs/18 §1.2: rules only ever offer allowlisted action ids, with a service or component target."""
    cases = [with_(*down(s), API_REFUSED, HEALTH_500) for s in ("postgres", "rabbitmq", "seaweedfs", "api", "worker")]
    for signals in cases:
        for a in rules.diagnose(signals, PAM, None).actions:
            assert a.id in {"restart_service", "start_service", "reverify", "move_back"}
            assert a.target in {*SERVICES, "stack", "orders", "order_events", "receipts"}
