"""Doctor, piece 2 (owner B, docs/18 §4): turn the stack's signals into one diagnosis.

Deterministic and table-driven, like ``assess.py``: the rules below run in order and the **first match wins**.
Each rule names the root cause, the failing service, the component behind it, what users lose, and the fixes,
best first. The AI only explains the result afterwards (piece 3); it can't change any of it.

Hybrid runs: a component that stays on AWS isn't part of the self-hosted stack, so its service (and that
service's "not created" container and refused port) is ignored, and nothing offers to move it back.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from boomerang import scope as scopes
from boomerang.models import Action, AppModel, Component, Diagnosis, Scope, Severity, Signal

# compose service ↔ PAM component kind (docs/18 §2)
SERVICE_KIND = {"postgres": "kv_table", "rabbitmq": "queue", "seaweedfs": "object_store"}
APP_SERVICES = ("api", "worker")
STACK = "stack"                                   # piece 1's service name when Docker itself can't be read

ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
TRACEBACK = re.compile(r"traceback|exception|error", re.IGNORECASE)
EXCEPTION_LINE = re.compile(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b(?::\s*(.*))?$")
MISSING_KEY = re.compile(r"KeyError:\s*['\"]([A-Z][A-Z0-9_]*)['\"]")
MISSING_ENV = re.compile(r"(?i:environment variable)\s*['\"`]?([A-Z][A-Z0-9_]*)")


# ---------- public API ----------

def diagnose(signals: list[Signal], pam: AppModel, scope: Scope | None, *, app_dir: Path | None = None) -> Diagnosis:
    """The first rule that matches the failing signals. ``app_dir`` (the app's source, e.g. ``runs/<id>/original``)
    lets the impact name the routes users hit; without it the impact names the component."""
    ctx = _Context(relevant(signals, pam, scope), pam, _selfhosted(pam, scope), app_dir)
    for rule in RULES:
        diagnosis = rule(ctx)
        if diagnosis is not None:
            return diagnosis
    raise AssertionError("unreachable: `unknown` always matches")  # pragma: no cover


def relevant(signals: list[Signal], pam: AppModel, scope: Scope | None) -> list[Signal]:
    """Drop the signals of backing services no self-hosted component needs (hybrid runs)."""
    selfhosted_kinds = {c.kind for c in _selfhosted(pam, scope)}
    return [s for s in signals if SERVICE_KIND.get(s.service) is None or SERVICE_KIND[s.service] in selfhosted_kinds]


def impact(pam: AppModel, component: Component | None, app_dir: Path | None = None) -> list[str]:
    """User-facing features that use ``component``: the app's routes that call its functions, e.g.
    ``"Create order (POST /orders)"``. Falls back to the component id when the routes can't be read."""
    if component is None:
        return []
    routes = _routes_using(pam, component, app_dir) if app_dir is not None else []
    return routes or [f"Everything that uses {component.id}"]


# ---------- the rules, in order ----------

@dataclass
class _Context:
    signals: list[Signal]
    pam: AppModel
    selfhosted: list[Component]
    app_dir: Path | None

    def failing(self, service: str, *sources: str) -> list[Signal]:
        return [s for s in self.signals if s.service == service and not s.ok and (not sources or s.source in sources)]

    def down(self, service: str) -> list[Signal]:
        """The service's container isn't running, or its port doesn't answer."""
        return self.failing(service, "container", "probe")

    def app_hurt(self) -> list[Signal]:
        return self.failing("api", "logs", "health")

    def component(self, kind: str) -> Component | None:
        return next((c for c in self.selfhosted if c.kind == kind), None)

    def diagnosis(self, rule: str, root_cause: str, severity: Severity, evidence: list[Signal], actions: list[Action],
                  service: str | None = None, component: Component | None = None,
                  impact_items: list[str] | None = None) -> Diagnosis:
        return Diagnosis(rule=rule, root_cause=root_cause, service=service,
                         component_id=component.id if component else None,
                         impact=impact_items if impact_items is not None else impact(self.pam, component, self.app_dir),
                         severity=severity, evidence=_unique(evidence), actions=actions)

    def move_back(self, component: Component | None) -> list[Action]:
        """Offered only for a self-hosted component that isn't the last one (moving the last back = round trip)."""
        if component is None or component not in self.selfhosted or len(self.selfhosted) < 2:
            return []
        return [Action(id="move_back", target=component.id, label=f"Move {component.id} back to AWS", risk="high")]


def _stack_unreachable(ctx: _Context) -> Diagnosis | None:
    evidence = ctx.failing(STACK)
    if not evidence:
        return None
    return ctx.diagnosis("stack_unreachable", f"Docker can't be read, so the stack's state is unknown: {evidence[0].detail}.",
                         "down", evidence, [_reverify()], service=None)


def _backing_service_down(service: str, rule: str, root_cause: str, severity: Severity,
                          needs_app_hurt: bool) -> Callable[[_Context], Diagnosis | None]:
    def check(ctx: _Context) -> Diagnosis | None:
        component = ctx.component(SERVICE_KIND[service])
        down = ctx.down(service)
        if component is None or not down:
            return None
        hurt = ctx.app_hurt()
        if needs_app_hurt and not hurt:
            return None
        return ctx.diagnosis(rule, root_cause, severity, down + ctx.failing(service, "logs") + hurt,
                             [_restart(service), *ctx.move_back(component), _reverify()], service, component)
    check.__name__ = f"_{rule}"
    return check


_db_down = _backing_service_down(
    "postgres", "db_down", "The database (Postgres) is down, so the app can't read or write orders.", "down",
    needs_app_hurt=True)
_queue_down = _backing_service_down(
    "rabbitmq", "queue_down", "The message queue (RabbitMQ) is down; new orders aren't being processed.", "degraded",
    needs_app_hurt=False)
_storage_down = _backing_service_down(
    "seaweedfs", "storage_down", "Object storage is down; receipts can't be saved or read.", "degraded",
    needs_app_hurt=False)


def _worker_stalled(ctx: _Context) -> Diagnosis | None:
    worker = ctx.failing("worker", "container")
    stuck = [s for s in ctx.signals if s.source == "queue" and not s.ok]
    if not worker and not stuck:
        return None
    depth = sum(int(s.data.get("depth") or 0) for s in stuck)
    cause = (f"The worker has stopped; {depth} message(s) are waiting." if depth
             else "The worker has stopped, so new orders aren't being processed.")
    queue = ctx.component("queue")
    return ctx.diagnosis("worker_stalled", cause, "degraded", worker + stuck + ctx.failing("worker", "logs"),
                         [_start("worker"), _reverify()], "worker", queue)


def _config_error(ctx: _Context) -> Diagnosis | None:
    """Before app_crash: a missing setting usually *is* why the app crashes on start, and it names the fix."""
    if not ctx.failing("api", "container", "health"):
        return None
    logs = ctx.failing("api", "logs")
    name = _first_match((MISSING_KEY, MISSING_ENV), [line for s in logs for line in s.lines])
    if name is None:
        return None
    return ctx.diagnosis("config_error", f"A setting is missing: {name}. Add it to the stack's env file, then re-verify.",
                         "down", ctx.failing("api", "container", "health") + logs, [_reverify()], "api",
                         impact_items=["The whole app (it can't start)"])


def _app_crash(ctx: _Context) -> Diagnosis | None:
    container = ctx.failing("api", "container")
    logs = [s for s in ctx.failing("api", "logs") if any(TRACEBACK.search(line) for line in s.lines)]
    if not container or not logs:
        return None
    last = _last_exception([line for s in logs for line in s.lines]) or "see the log lines below"
    return ctx.diagnosis("app_crash", f"The app is crashing on start: {last}.", "down",
                         container + logs + ctx.failing("api", "health"), [_restart("api"), _reverify()], "api",
                         impact_items=["The whole app (it isn't running)"])


def _unknown(ctx: _Context) -> Diagnosis:
    failing = [s for s in ctx.signals if not s.ok]
    service = failing[0].service if failing else None
    return ctx.diagnosis("unknown", "Something is failing; the evidence is below.", "degraded", failing,
                         [_reverify()], service)


RULES: list[Callable[[_Context], Diagnosis | None]] = [
    _stack_unreachable, _db_down, _queue_down, _storage_down, _worker_stalled, _config_error, _app_crash, _unknown,
]


# ---------- actions ----------

def _restart(service: str) -> Action:
    return Action(id="restart_service", target=service, label=f"Restart {service}", risk="low")


def _start(service: str) -> Action:
    return Action(id="start_service", target=service, label=f"Start {service}", risk="low")


def _reverify() -> Action:
    return Action(id="reverify", target="stack", label="Re-verify the stack (config, boot, tests, health)", risk="low")


# ---------- impact: routes that reach a component ----------

def _routes_using(pam: AppModel, component: Component, app_dir: Path) -> list[str]:
    """Routes in the app's entrypoint modules that call one of the component's functions (``db.put_order``)."""
    wanted = {(Path(u.file).stem, u.symbol) for u in component.usages if u.symbol}
    out: list[str] = []
    for entry in pam.entrypoints:
        path = app_dir / (entry.split(":", 1)[0].replace(".", "/") + ".py")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for fn in (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            route = _route(fn)
            if route is None:
                continue
            calls = {(c.func.value.id, c.func.attr) for c in ast.walk(fn)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                     and isinstance(c.func.value, ast.Name)}
            if calls & wanted:
                out.append(f"{fn.name.replace('_', ' ').capitalize()} ({route})")
    return list(dict.fromkeys(out))


def _route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """``@app.post("/orders")`` → ``"POST /orders"``."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in ROUTE_METHODS
                and dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
            return f"{dec.func.attr.upper()} {dec.args[0].value}"
    return None


# ---------- helpers ----------

def _selfhosted(pam: AppModel, scope: Scope | None) -> list[Component]:
    ids = set(scopes.normalize_scope(pam, scope).selfhosted)
    return [c for c in scopes.movable(pam) if c.id in ids]


def _unique(signals: list[Signal]) -> list[Signal]:
    seen: list[Signal] = []
    for s in signals:
        if s not in seen:
            seen.append(s)
    return seen


def _first_match(patterns: tuple[re.Pattern[str], ...], lines: list[str]) -> str | None:
    for line in reversed(lines):                  # newest first
        for pattern in patterns:
            m = pattern.search(line)
            if m:
                return m.group(1)
    return None


def _last_exception(lines: list[str]) -> str | None:
    for line in reversed(lines):
        m = EXCEPTION_LINE.match(line)
        if m:
            return f"{m.group(1)}: {m.group(2)}".rstrip(": ") if m.group(2) else m.group(1)
    return None
