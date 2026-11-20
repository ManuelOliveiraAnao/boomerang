"""The lab: fake AWS (moto) on 127.0.0.1:4566, plus optional services for adapter tests.

Backends:
  docker  -> infra/lab.compose.yml (moto container; `adapters` profile adds Postgres + RabbitMQ)
  process -> a local `python -m moto.server` process (no Docker needed; AWS only)
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "infra" / "lab.compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", "boomerang-lab"]
HOST, PORT = "127.0.0.1", 4566
AWS_ENDPOINT = f"http://{HOST}:{PORT}"
STATE_DIR = ROOT / ".cache" / "lab"
PID_FILE = STATE_DIR / "moto.pid"

TEST_POSTGRES_URL = "postgresql://test:test@127.0.0.1:55432/test"
TEST_AMQP_URL = "amqp://guest:guest@127.0.0.1:55672/%2F"


class LabError(RuntimeError):
    pass


def aws_env() -> dict[str, str]:
    """Env vars an AWS app needs to talk to the fake AWS."""
    return {
        "AWS_ENDPOINT_URL": AWS_ENDPOINT,
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
    }


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def moto_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{AWS_ENDPOINT}/moto-api/", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def wait_for_moto(timeout_s: float = 30) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if moto_ready():
            return
        time.sleep(0.3)
    raise LabError(f"moto did not answer on {AWS_ENDPOINT} within {timeout_s:.0f}s")


def _pick_backend(backend: str) -> str:
    if backend == "auto":
        return "docker" if docker_available() else "process"
    if backend == "docker" and not docker_available():
        raise LabError("Docker is not installed or not running. Use --backend process for AWS only.")
    return backend


def up(adapters: bool = False, backend: str = "auto") -> str:
    """Start the lab. Returns the backend used."""
    backend = _pick_backend(backend)
    if moto_ready() and not adapters:
        return backend
    if backend == "docker":
        cmd = COMPOSE + (["--profile", "adapters"] if adapters else []) + ["up", "-d"]
        if adapters:
            cmd.append("--wait")
        subprocess.run(cmd, check=True)
    else:
        if adapters:
            raise LabError("Adapter test services (Postgres, RabbitMQ) need Docker.")
        if not moto_ready():
            _start_moto_process()
    wait_for_moto()
    return backend


def _start_moto_process() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / "moto.log", "ab")
    kwargs: dict = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", "moto.server", "-H", HOST, "-p", str(PORT)], **kwargs)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")


def down() -> None:
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text(encoding="utf-8").strip() or 0)
        if pid:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        PID_FILE.unlink(missing_ok=True)
    if docker_available():
        subprocess.run(COMPOSE + ["--profile", "adapters", "down", "-v"], capture_output=True)
    deadline = time.time() + 10
    while moto_ready() and time.time() < deadline:
        time.sleep(0.2)


def reset_moto() -> None:
    """Wipe all fake AWS state."""
    req = urllib.request.Request(f"{AWS_ENDPOINT}/moto-api/reset", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        if r.status != 200:
            raise LabError(f"moto reset failed: HTTP {r.status}")


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def status() -> dict[str, bool]:
    return {
        "moto": moto_ready(),
        "test-postgres": _port_open(55432),
        "test-rabbitmq": _port_open(55672),
        "docker": docker_available(),
    }
