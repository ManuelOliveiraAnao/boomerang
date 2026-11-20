"""Doctor, piece 1 (owner A, docs/18 §3): read the self-hosted stack's state as Signals.

collect() gathers five kinds of signal from the stack `verify()` boots (compose project `bmr-verify`):

1. container: `docker compose ps` state, health and exit code per service
2. logs:      the last 40 log lines of api, worker and every service that isn't running
3. health:    GET /health on the app
4. probe:     a TCP connect to each published port (postgres 5432, rabbitmq 5672, seaweedfs 8333, api 8080)
5. queue:     RabbitMQ queue depth and consumers (management API; skipped if it isn't reachable)

Every check is independent: a failure becomes a Signal(ok=False), never an exception, so a broken stack is
still described in full. The I/O (docker, sockets, HTTP) is injectable so the tests need no Docker.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import subprocess
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .. import verifier
from ..models import Signal
from ..verifier import Run

SERVICES = ("postgres", "rabbitmq", "seaweedfs", "api", "worker")
LOG_TAIL = 40
ALWAYS_LOG = ("api", "worker")                       # the app: its logs show what users hit
ERROR_LINE = re.compile(r"error|exception|traceback|refused|timed out|fatal", re.I)
MGMT_URL = "http://127.0.0.1:15672"

Connect = Callable[[str, int, float], None]          # raises OSError when the port is closed
FetchJson = Callable[[str, dict[str, str], float], Any]


def _connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


# ---------------------------------------------------------------- 1. containers
def _ps_rows(stdout: str) -> list[dict[str, Any]]:
    """`docker compose ps --format json` prints one JSON object per line (Compose ≥ 2.21) or one array."""
    text = stdout.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def container_signals(leaseweb_dir: Path, run: Run = subprocess.run) -> list[Signal]:
    try:
        p = run(verifier._compose(leaseweb_dir) + ["ps", "--all", "--format", "json"],
                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return [Signal(source="container", service="stack", ok=False, detail=f"docker not available: {e}")]
    if p.returncode != 0:
        return [Signal(source="container", service="stack", ok=False,
                       detail=f"docker compose ps failed: {(p.stderr or p.stdout).strip()[:200]}")]
    try:
        rows = _ps_rows(p.stdout)
    except json.JSONDecodeError:
        return [Signal(source="container", service="stack", ok=False, detail="unreadable docker compose ps output")]
    seen: dict[str, Signal] = {}
    for r in rows:
        name = r.get("Service") or r.get("Name", "")
        if name not in SERVICES:
            continue
        state = (r.get("State") or "").lower()
        health = (r.get("Health") or "").lower()
        ok = state == "running" and health in ("", "healthy")
        detail = r.get("Status") or state
        seen[name] = Signal(source="container", service=name, ok=ok, detail=detail,
                            data={"state": state, "health": health, "exit_code": r.get("ExitCode")})
    for name in SERVICES:                    # a service compose doesn't list at all is down too
        seen.setdefault(name, Signal(source="container", service=name, ok=False, detail="not created",
                                     data={"state": "missing"}))
    return [seen[n] for n in SERVICES]


# ---------------------------------------------------------------- 2. logs
_PREFIX = re.compile(r"^[\w.-]+\s*\|\s?")            # "api-1  | " in front of each compose log line


def log_signal(leaseweb_dir: Path, service: str, run: Run = subprocess.run) -> Signal:
    try:
        p = run(verifier._compose(leaseweb_dir) + ["logs", "--tail", str(LOG_TAIL), "--no-color", service],
                capture_output=True, text=True, timeout=20)
        out = (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return Signal(source="logs", service=service, ok=False, detail=f"logs unavailable: {e}")
    # Drop "api-1  | ", then the indentation every line shares (compose pads it), keeping tracebacks' own.
    body = textwrap.dedent("\n".join(_PREFIX.sub("", ln).rstrip() for ln in out.splitlines() if ln.strip()))
    lines = body.splitlines()[-LOG_TAIL:]
    errors = [ln for ln in lines if ERROR_LINE.search(ln)]
    return Signal(source="logs", service=service, ok=not errors,
                  detail=f"{len(errors)} error line(s) in the last {len(lines)}" if errors else f"{len(lines)} lines, no errors",
                  lines=lines, data={"errors": len(errors)})


# ---------------------------------------------------------------- 3. health
def health_signal(url: str = verifier.HEALTH_URL) -> Signal:
    c = verifier.check_health(url, timeout_s=3)
    return Signal(source="health", service="api", ok=c.ok, detail=c.detail, data={"url": url})


# ---------------------------------------------------------------- 4. probes
def probe_signals(connect: Connect = _connect, timeout: float = 2.0) -> list[Signal]:
    out = []
    for container, local in verifier.HOST_PORTS.items():
        service = container.split(":")[0]
        host, port = local.rsplit(":", 1)
        try:
            connect(host, int(port), timeout)
            out.append(Signal(source="probe", service=service, ok=True, detail=f"{local} open"))
        except OSError as e:
            reason = "connection refused" if isinstance(e, ConnectionRefusedError) else (str(e) or type(e).__name__)
            out.append(Signal(source="probe", service=service, ok=False, detail=f"{local}: {reason}"))
    return out


# ---------------------------------------------------------------- 5. queues
def queue_signals(leaseweb_dir: Path, fetch: FetchJson = _fetch_json, timeout: float = 3.0) -> list[Signal]:
    """Depth and consumers per queue. Not ok when messages wait and nobody consumes them."""
    try:
        amqp = urllib.parse.urlsplit(verifier.laptop_env(leaseweb_dir).get("AMQP_URL", ""))
    except OSError:
        return []
    if not amqp.username:
        return []
    vhost = urllib.parse.quote(amqp.path.lstrip("/") or "/", safe="")
    token = base64.b64encode(f"{urllib.parse.unquote(amqp.username)}:{urllib.parse.unquote(amqp.password or '')}"
                             .encode()).decode()
    try:
        queues = fetch(f"{MGMT_URL}/api/queues/{vhost}", {"Authorization": f"Basic {token}"}, timeout)
    except (OSError, ValueError):
        return []                             # management API not exposed or the broker is down: probes cover it
    out = []
    for q in queues or []:
        depth, consumers = int(q.get("messages") or 0), int(q.get("consumers") or 0)
        stalled = depth > 0 and consumers == 0 and not str(q.get("name", "")).endswith(("dlq", ".dlq", "-dlq"))
        out.append(Signal(source="queue", service="rabbitmq", ok=not stalled,
                          detail=f"{q.get('name')}: {depth} waiting, {consumers} consumer(s)",
                          data={"queue": q.get("name"), "depth": depth, "consumers": consumers}))
    return out


# ---------------------------------------------------------------- all of it
def collect(leaseweb_dir: Path, run: Run = subprocess.run, health_url: str = verifier.HEALTH_URL, *,
            connect: Connect = _connect, fetch: FetchJson = _fetch_json) -> list[Signal]:
    """Every signal for the stack in `leaseweb_dir` (runs/<id>/leaseweb), in a stable order."""
    containers = container_signals(leaseweb_dir, run)
    down = [s.service for s in containers if not s.ok and s.service in SERVICES]
    log_for = [s for s in SERVICES if s in ALWAYS_LOG or s in down]
    return (containers
            + [log_signal(leaseweb_dir, s, run) for s in log_for]
            + [health_signal(health_url)]
            + probe_signals(connect)
            + queue_signals(leaseweb_dir, fetch))


def healthy(signals: list[Signal]) -> bool:
    return all(s.ok for s in signals)
