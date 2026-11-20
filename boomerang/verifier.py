"""Live verification of a generated Leaseweb setup, plus the shared pytest runner. Owner A, task A5.

verify():  config valid -> stack boots -> schema exists -> ORIGINAL tests pass -> health 200

Contract with C (docs/03 §3.8, docs/15 L4/L8): verify(leaseweb_dir, attempt, emit),
run_tests(app_dir, env, platform, attempt, emit), aws_env(), leaseweb_env(leaseweb_dir),
reset_data(leaseweb_dir), stop_service(leaseweb_dir, name), start_service(leaseweb_dir, name).
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

from .events import Emitter
from .models import Check, Platform, TestFailure, TestReport, Verification

PROJECT = "bmr-verify"
HEALTH_URL = "http://127.0.0.1:8080/health"
# Container host -> laptop address (contract with B, docs/roles/A-platform-and-data.md §4.1)
HOST_PORTS = {"postgres:5432": "127.0.0.1:5432", "rabbitmq:5672": "127.0.0.1:5672",
              "seaweedfs:8333": "127.0.0.1:8333", "api:8080": "127.0.0.1:8080"}
# L8: the app under test (AI-edited) gets an allowlisted environment, never a copy of ours:
# no ANTHROPIC_API_KEY, no developer AWS credentials, nothing from the other platform.
ENV_ALLOWLIST = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
                 "TMPDIR", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL")

Run = Callable[..., subprocess.CompletedProcess]


# ---------------------------------------------------------------- pytest runner
def _emit(emit: Emitter | None, stage, message: str, level: str = "info", **data) -> None:
    if emit is not None:
        emit.emit(stage, message, level, **data)


def clean_env(extra: dict[str, str], app_dir: Path) -> dict[str, str]:
    """Allowlisted OS variables + the platform's variables (`extra`) + Python settings."""
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOWLIST}
    env.update(extra)
    env.update({"PYTHONPATH": str(app_dir), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1"})
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


def run_tests(app_dir: Path, env: dict[str, str], platform: Platform, attempt: int,
              emit: Emitter | None = None, timeout_s: int = 180) -> TestReport:
    """C's contract (L8): the app's own `tests/` folder, in a subprocess, against `env`."""
    report = run_pytest(Path(app_dir), Path(app_dir) / "tests", env, platform, attempt, timeout_s)
    _emit(emit, "verify", f"tests on {platform}: {report.passed}/{report.total} passed",
          "success" if report.ok else "error", step="tests", report=report)
    return report


def aws_env() -> dict[str, str]:
    """Env for the AWS side: the fake AWS (moto) + the resource names of the last provision()."""
    from . import lab, provisioner_aws
    return lab.aws_env() | provisioner_aws.last_names()


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


def leaseweb_env(leaseweb_dir: Path) -> dict[str, str]:
    """C's contract (L8): env for the Leaseweb side on the laptop (127.0.0.1 ports, real secrets)."""
    return laptop_env(leaseweb_dir)


# ---------------------------------------------------------------- docker compose
def _compose(leaseweb_dir: Path) -> list[str]:
    """`docker compose` for the stack. Later --env-file entries win: secrets.env holds the generated
    passwords the compose file references (${POSTGRES_PASSWORD} ...)."""
    d = Path(leaseweb_dir)
    cmd = ["docker", "compose", "-f", str(d / "docker-compose.yml"), "-p", PROJECT]
    for name in (".env.example", "secrets.env"):
        if (d / name).exists():
            cmd += ["--env-file", str(d / name)]
    return cmd


def _timed(name: str, fn) -> Check:
    start = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # a check must never crash the pipeline
        ok, detail = False, f"{type(e).__name__}: {e}"
    return Check(name=name, ok=ok, detail=detail[:4000], duration_s=round(time.time() - start, 2))


def find_bash() -> str | None:
    """A bash that can check shell syntax. On Windows, C:\\Windows\\System32\\bash.exe is the WSL
    launcher (it runs inside a Linux distro and fails if that distro has no bash, e.g. docker-desktop),
    so prefer Git Bash."""
    found = shutil.which("bash")
    if os.name != "nt":
        return found
    candidates = []
    git = shutil.which("git")
    if git:  # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    candidates += [Path(os.environ.get(v, "")) / "Git" / "bin" / "bash.exe"
                   for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if os.environ.get(v)]
    for c in candidates:
        if c.is_file():
            return str(c)
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    return None


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
            bash = find_bash()
            if bash:
                p = run([bash, "-n", str(d / "deploy.sh")], capture_output=True, text=True)
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
    """config -> boot -> schema -> tests -> health. Stops at the first failing infra check.
    L4: the `worker` container is stopped while the tests run (they call process_once themselves)."""
    d = Path(leaseweb_dir)
    checks: list[Check] = []

    def record(c: Check) -> bool:
        checks.append(c)
        _emit(emit, "verify", f"{c.name}: {'OK' if c.ok else 'FAILED'} - {c.detail[:200]}",
              "success" if c.ok else "error", check=c, attempt=attempt)
        return c.ok

    if not record(check_config(d, run)) or not record(check_boot(d, run)):
        return Verification(checks=checks)
    env = leaseweb_env(d)
    if not record(check_schema(d, env)):
        return Verification(checks=checks)
    stop_service(d, "worker", run)
    try:
        report = run_pytest(d / "app", d / "app" / "tests", env, "leaseweb", attempt)
    finally:
        start_service(d, "worker", run)
    record(Check(name="tests", ok=report.ok, detail=f"{report.passed}/{report.total} passed",
                 duration_s=report.duration_s))
    record(check_health(health_url))
    return Verification(checks=checks, tests=report)


def stop_service(leaseweb_dir: Path, name: str, run: Run = subprocess.run) -> None:
    """`docker compose stop <name>` (e.g. the worker during tests and data moves, L4)."""
    run(_compose(leaseweb_dir) + ["stop", name], capture_output=True, text=True)


def start_service(leaseweb_dir: Path, name: str, run: Run = subprocess.run) -> None:
    run(_compose(leaseweb_dir) + ["start", name], capture_output=True, text=True)


def _queue_names(leaseweb_dir: Path) -> list[str]:
    defs = Path(leaseweb_dir) / "rabbitmq" / "definitions.json"
    if not defs.exists():
        return []
    return [q["name"] for q in json.loads(defs.read_text(encoding="utf-8")).get("queues", [])]


def reset_data(leaseweb_dir: Path, emit: Emitter | None = None) -> dict[str, int]:
    """Empty tables, queues and buckets of the verify stack; keep the schema (L4).
    Returns what was removed. Needs the stack running."""
    d = Path(leaseweb_dir)
    env = leaseweb_env(d)
    removed = {"rows": 0, "messages": 0, "objects": 0}
    tables = _kv_tables(d)
    if tables and env.get("DATABASE_URL"):
        import psycopg
        from psycopg import sql
        with psycopg.connect(env["DATABASE_URL"], connect_timeout=5, autocommit=True) as conn:
            for t in tables:
                removed["rows"] += conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(t))).rowcount
    queues = _queue_names(d)
    if queues and env.get("AMQP_URL"):
        import pika
        conn = pika.BlockingConnection(pika.URLParameters(env["AMQP_URL"]))
        try:
            ch = conn.channel()
            for q in queues:
                removed["messages"] += ch.queue_purge(q).method.message_count
        finally:
            conn.close()
    if env.get("S3_ENDPOINT_URL"):
        import boto3
        from botocore.config import Config
        s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT_URL"], region_name=env.get("S3_REGION", "us-east-1"),
                          aws_access_key_id=env.get("S3_ACCESS_KEY_ID"),
                          aws_secret_access_key=env.get("S3_SECRET_ACCESS_KEY"),
                          config=Config(s3={"addressing_style": "path"}))
        for b in s3.list_buckets().get("Buckets", []):
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b["Name"]):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    s3.delete_objects(Bucket=b["Name"], Delete={"Objects": keys, "Quiet": True})
                    removed["objects"] += len(keys)
    _emit(emit, "data", f"verify stack emptied: {removed}", "success", step="reset_data", removed=removed)
    return removed


def reset_stack(leaseweb_dir: Path, emit: Emitter | None = None, run: Run = subprocess.run) -> Check:
    """Heavier alternative to reset_data: wipe the volumes and boot again."""
    run(_compose(leaseweb_dir) + ["down", "-v"], capture_output=True, text=True)
    c = check_boot(leaseweb_dir, run)
    _emit(emit, "data", f"stack reset: {'OK' if c.ok else 'FAILED'}", "success" if c.ok else "error")
    return c


def teardown(leaseweb_dir: Path, run: Run = subprocess.run) -> None:
    run(_compose(leaseweb_dir) + ["down", "-v"], capture_output=True, text=True)
