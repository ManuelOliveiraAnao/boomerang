"""Live verification of a generated Leaseweb setup, plus the shared pytest runner.

verify():  config valid -> stack boots -> schema exists -> ORIGINAL tests pass -> health 200
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import yaml

from .events import Emitter, null_emitter
from .models import Check, Platform, TestFailure, TestReport, Verification

PROJECT = "bmr-verify"
HEALTH_URL = "http://127.0.0.1:8080/health"
# Container host -> laptop address (contract with B, docs/roles/A-platform-and-data.md §4.1)
HOST_PORTS = {"postgres:5432": "127.0.0.1:5432", "rabbitmq:5672": "127.0.0.1:5672",
              "seaweedfs:8333": "127.0.0.1:8333", "api:8080": "127.0.0.1:8080"}
APP_ENV_KEYS = {"DATABASE_URL", "AMQP_URL", "ORDERS_TABLE", "RECEIPTS_BUCKET", "ORDER_EVENTS_QUEUE"}
APP_ENV_PREFIXES = ("AWS_", "S3_")

Run = Callable[..., subprocess.CompletedProcess]


# ---------------------------------------------------------------- pytest runner
def clean_env(extra: dict[str, str], app_dir: Path) -> dict[str, str]:
    """os.environ minus anything that could point the app at the wrong platform, plus `extra`."""
    env = {k: v for k, v in os.environ.items()
           if k not in APP_ENV_KEYS and not k.startswith(APP_ENV_PREFIXES)}
    env.update(extra)
    env["PYTHONPATH"] = str(app_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def parse_junit(path: Path, attempt: int, platform: Platform, duration_s: float, output: str) -> TestReport:
    tail = "\n".join(output.splitlines()[-80:])
    if not path.exists():
        return TestReport(attempt=attempt, platform=platform, passed=0, failed=0, errors=1, total=0,
                          duration_s=duration_s, output_tail=tail,
                          failures=[TestFailure(test="<session>", message="pytest produced no report",
                                                traceback=tail)])
    root = ET.parse(path).getroot()
    failures: list[TestFailure] = []
    total = failed = errors = skipped = 0
    for case in root.iter("testcase"):
        total += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is not None:
                if tag == "failure":
                    failed += 1
                else:
                    errors += 1
                tb = "\n".join((node.text or "").splitlines()[-60:])
                failures.append(TestFailure(test=name, message=node.get("message", "")[:500], traceback=tb))
        if case.find("skipped") is not None:
            skipped += 1
    # collection errors appear as suite-level errors without testcases
    suite_errors = sum(int(s.get("errors", 0)) for s in root.iter("testsuite"))
    if suite_errors > errors:
        errors = suite_errors
        if not failures:
            failures.append(TestFailure(test="<collection>", message="collection error", traceback=tail))
    passed = total - failed - errors - skipped
    return TestReport(attempt=attempt, platform=platform, passed=max(passed, 0), failed=failed, errors=errors,
                      total=total, duration_s=duration_s, failures=failures, output_tail=tail)


def run_pytest(app_dir: Path, tests_dir: Path, env: dict[str, str], platform: Platform,
               attempt: int = 1, timeout_s: int = 180) -> TestReport:
    """Run the original tests against an app, in a subprocess with a clean environment."""
    app_dir, tests_dir = Path(app_dir).resolve(), Path(tests_dir).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        ini = Path(tmp) / "pytest.ini"  # empty config: the app's tests never inherit Boomerang's settings
        ini.write_text("[pytest]\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", str(tests_dir), "-p", "no:cacheprovider",
               "-c", str(ini), f"--junitxml={junit}", f"--rootdir={app_dir}"]
        start = time.time()
        try:
            proc = subprocess.run(cmd, cwd=app_dir, env=clean_env(env, app_dir), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as e:
            output = f"pytest timed out after {timeout_s}s\n{e.stdout or ''}"
        return parse_junit(junit, attempt, platform, round(time.time() - start, 2), output)


# ---------------------------------------------------------------- env files
def read_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def laptop_env(leaseweb_dir: Path) -> dict[str, str]:
    """Env for running the app on the laptop against the booted stack.
    Prefers B's laptop.env; otherwise derives it from .env.example + secrets.env."""
    leaseweb_dir = Path(leaseweb_dir)
    if (leaseweb_dir / "laptop.env").exists():
        return read_env_file(leaseweb_dir / "laptop.env")
    env = read_env_file(leaseweb_dir / ".env.example") | read_env_file(leaseweb_dir / "secrets.env")
    for k, v in env.items():
        for container, local in HOST_PORTS.items():
            v = v.replace(f"@{container}", f"@{local}").replace(f"//{container}", f"//{local}")
        env[k] = v
    return env


# ---------------------------------------------------------------- docker compose
def _compose(leaseweb_dir: Path) -> list[str]:
    cmd = ["docker", "compose", "-f", str(Path(leaseweb_dir) / "docker-compose.yml"), "-p", PROJECT]
    if (Path(leaseweb_dir) / ".env.example").exists():
        cmd += ["--env-file", str(Path(leaseweb_dir) / ".env.example")]
    return cmd


def _timed(name: str, fn) -> Check:
    start = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # a check must never crash the pipeline
        ok, detail = False, f"{type(e).__name__}: {e}"
    return Check(name=name, ok=ok, detail=detail[:4000], duration_s=round(time.time() - start, 2))


def check_config(leaseweb_dir: Path, run: Run = subprocess.run) -> Check:
    def body():
        d = Path(leaseweb_dir)
        notes = []
        if not (d / "docker-compose.yml").exists():
            return False, "docker-compose.yml missing"
        for f in list(d.rglob("*.yml")) + list(d.rglob("*.yaml")):
            yaml.safe_load(f.read_text(encoding="utf-8"))
        for f in d.rglob("*.json"):
            json.loads(f.read_text(encoding="utf-8"))
        notes.append("YAML/JSON parse")
        if shutil.which("docker"):
            p = run(_compose(d) + ["config", "-q"], capture_output=True, text=True)
            if p.returncode != 0:
                return False, f"docker compose config failed:\n{p.stderr}"
            notes.append("compose config")
        else:
            notes.append("compose config skipped (no docker)")
        if (d / "deploy.sh").exists():
            if shutil.which("bash"):
                p = run(["bash", "-n", str(d / "deploy.sh")], capture_output=True, text=True)
                if p.returncode != 0:
                    return False, f"deploy.sh syntax error:\n{p.stderr}"
                notes.append("deploy.sh syntax")
            else:
                notes.append("deploy.sh skipped (no bash)")
        if (d / "cloud-init.yaml").exists():
            text = (d / "cloud-init.yaml").read_text(encoding="utf-8")
            if not text.startswith("#cloud-config"):
                return False, "cloud-init.yaml must start with '#cloud-config'"
            notes.append("cloud-init header")
        return True, ", ".join(notes)
    return _timed("config", body)


def check_boot(leaseweb_dir: Path, run: Run = subprocess.run, timeout_s: int = 180) -> Check:
    def body():
        if not shutil.which("docker"):
            return False, "Docker is not installed or not running"
        p = run(_compose(leaseweb_dir) + ["up", "-d", "--build", "--wait", "--wait-timeout", str(timeout_s)],
                capture_output=True, text=True, timeout=timeout_s + 60)
        if p.returncode == 0:
            return True, "all services healthy"
        ps = run(_compose(leaseweb_dir) + ["ps"], capture_output=True, text=True)
        logs = run(_compose(leaseweb_dir) + ["logs", "--tail", "40"], capture_output=True, text=True)
        return False, f"{p.stderr}\n{ps.stdout}\n{logs.stdout}"
    return _timed("boot", body)


def _kv_tables(leaseweb_dir: Path) -> list[str]:
    schema = Path(leaseweb_dir) / "schema.sql"
    if not schema.exists():
        return []
    return re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+\"?(\w+)\"?", schema.read_text(encoding="utf-8"), re.I)


def check_schema(leaseweb_dir: Path, env: dict[str, str]) -> Check:
    def body():
        tables = _kv_tables(leaseweb_dir)
        if not tables:
            return True, "no kv tables in schema.sql"
        import psycopg
        with psycopg.connect(env["DATABASE_URL"], connect_timeout=5) as conn:
            rows = conn.execute("SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema = 'public'").fetchall()
        found = {r[0] for r in rows}
        missing = [t for t in tables if t not in found]
        return (not missing), f"missing tables: {missing}" if missing else f"tables present: {tables}"
    return _timed("schema", body)


def check_health(url: str = HEALTH_URL, timeout_s: float = 30) -> Check:
    def body():
        deadline, last = time.time() + timeout_s, ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    payload = json.loads(r.read() or b"{}")
                    if r.status == 200 and payload.get("status") == "ok":
                        return True, f"GET {url} -> 200"
                    last = f"HTTP {r.status} {payload}"
            except OSError as e:
                last = str(e)
            time.sleep(1)
        return False, f"health check failed: {last}"
    return _timed("health", body)


def verify(leaseweb_dir: Path, attempt: int = 1, emit: Emitter | None = None,
           run: Run = subprocess.run, health_url: str = HEALTH_URL) -> Verification:
    """config -> boot -> schema -> tests -> health. Stops at the first failing infra check."""
    emit = emit or null_emitter()
    d = Path(leaseweb_dir)
    checks: list[Check] = []

    def record(c: Check) -> bool:
        checks.append(c)
        emit("verify", f"{c.name}: {'OK' if c.ok else 'FAILED'} - {c.detail[:200]}",
             level="success" if c.ok else "error", check=c.model_dump())
        return c.ok

    if not record(check_config(d, run)) or not record(check_boot(d, run)):
        return Verification(checks=checks)
    env = laptop_env(d)
    if not record(check_schema(d, env)):
        return Verification(checks=checks)
    report = run_pytest(d / "app", d / "app" / "tests", env, "leaseweb", attempt)
    record(Check(name="tests", ok=report.ok, detail=f"{report.passed}/{report.total} passed",
                 duration_s=report.duration_s))
    record(check_health(health_url))
    return Verification(checks=checks, tests=report)


def reset_stack(leaseweb_dir: Path, emit: Emitter | None = None, run: Run = subprocess.run) -> Check:
    """Wipe data volumes and boot again (the tests wrote orders; data moves need a clean stack)."""
    emit = emit or null_emitter()
    run(_compose(leaseweb_dir) + ["down", "-v"], capture_output=True, text=True)
    c = check_boot(leaseweb_dir, run)
    emit("data", f"stack reset: {'OK' if c.ok else 'FAILED'}", level="success" if c.ok else "error")
    return c


def freeze(leaseweb_dir: Path, emit: Emitter | None = None, run: Run = subprocess.run) -> None:
    """Write freeze before copying data back: stop the api and worker containers."""
    emit = emit or null_emitter()
    run(_compose(leaseweb_dir) + ["stop", "api", "worker"], capture_output=True, text=True)
    emit("rollback", "write freeze: api + worker stopped")


def teardown(leaseweb_dir: Path, run: Run = subprocess.run) -> None:
    run(_compose(leaseweb_dir) + ["down", "-v"], capture_output=True, text=True)
