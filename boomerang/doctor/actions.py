"""Doctor, piece 1 (owner A, docs/18 §3): the fixes the Doctor may run, and nothing else.

apply() runs one approved Action from a fixed allowlist with fixed arguments (docs/13 S2: the AI never
produces or runs commands; the action ids and targets come from the rules, and a human approves each one):

    restart_service <service>   docker compose restart <service>
    start_service   <service>   docker compose start <service>
    reverify        <any>       verifier.verify(): config → boot → schema → tests → health
    move_back       <component> pipeline.move_back(run_id, [component]): that service runs on AWS again

break_service() stops a service for the demo. It only works on a local Docker engine, never on a remote one.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import verifier
from ..events import Emitter
from ..models import Action
from ..verifier import Run
from .signals import SERVICES, container_signals

READY_TIMEOUT_S = 60

ALLOWED = {"restart_service", "start_service", "reverify", "move_back"}


def _emit(emit: Emitter | None, message: str, level: str = "info", **data: Any) -> None:
    verifier._emit(emit, "doctor", message, level, **data)


def _service(target: str) -> str:
    if target not in SERVICES:
        raise ValueError(f"unknown service {target!r}: expected one of {', '.join(SERVICES)}")
    return target


def _compose_call(leaseweb_dir: Path, args: list[str], run: Run) -> dict[str, Any]:
    p = run(verifier._compose(leaseweb_dir) + args, capture_output=True, text=True, timeout=120)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return {"ok": p.returncode == 0, "detail": out[-300:] or f"docker compose {' '.join(args)}: exit {p.returncode}"}


def _wait_ready(leaseweb_dir: Path, service: str, run: Run, timeout_s: float) -> tuple[bool, str]:
    """After a (re)start, wait until the container is running and healthy, not just started."""
    start = time.time()
    while True:
        sig = next((s for s in container_signals(leaseweb_dir, run) if s.service == service), None)
        if sig is not None and sig.ok:
            return True, f"{service} healthy after {time.time() - start:.0f} s"
        if time.time() - start >= timeout_s:
            return False, f"{service} not healthy after {timeout_s:.0f} s ({sig.detail if sig else 'no container'})"
        time.sleep(1)


def apply(run_id: str, leaseweb_dir: Path, action: Action, *, by: str, emit: Emitter | None = None,
          run: Run = subprocess.run, ready_timeout_s: float = READY_TIMEOUT_S) -> dict[str, Any]:
    """Run one approved action. Returns {"ok": bool, "detail": str}; raises ValueError if it isn't allowed."""
    if action.id not in ALLOWED:
        raise ValueError(f"action {action.id!r} is not allowed")
    leaseweb_dir = Path(leaseweb_dir)
    _emit(emit, f"{action.label} (approved by {by})", action=action.model_dump(), by=by)

    if action.id in ("restart_service", "start_service"):
        verb = "restart" if action.id == "restart_service" else "start"
        result = _compose_call(leaseweb_dir, [verb, _service(action.target)], run)
        if result["ok"]:                      # "ok" means ready to serve again, not just "docker said started"
            ready, detail = _wait_ready(leaseweb_dir, action.target, run, ready_timeout_s)
            result = {"ok": ready, "detail": detail}
    elif action.id == "reverify":
        v = verifier.verify(leaseweb_dir, emit=emit, run=run)
        failed = [c.name for c in v.checks if not c.ok]
        result = {"ok": v.ok, "detail": "all checks passed" if v.ok else f"failing: {', '.join(failed) or 'tests'}"}
    else:  # move_back: C's existing step, for one component
        from .. import pipeline
        summary = pipeline.move_back(run_id, [action.target], by=by)
        result = {"ok": summary.status == "passed",
                  "detail": f"{action.target} runs on AWS again (run {summary.status})"}

    _emit(emit, f"{action.label}: {'done' if result['ok'] else 'failed'} · {result['detail'][:160]}",
          "success" if result["ok"] else "error", action=action.model_dump(), ok=result["ok"])
    return result


def _local_docker() -> bool:
    """True when the Docker engine is on this machine (no DOCKER_HOST, or a local socket or pipe)."""
    host = os.environ.get("DOCKER_HOST", "")
    return not host or host.startswith(("unix://", "npipe://"))


def break_service(leaseweb_dir: Path, service: str, run: Run = subprocess.run,
                  emit: Emitter | None = None) -> None:
    """Demo only: stop one service of the local verify stack so the Doctor has something to find."""
    service = _service(service)
    if verifier.PROJECT != "bmr-verify" or not _local_docker():
        raise PermissionError("doctor break only runs on the local verify stack (bmr-verify on this machine)")
    verifier.stop_service(Path(leaseweb_dir), service, run)
    _emit(emit, f"demo: stopped {service} on purpose", "warning", service=service)
