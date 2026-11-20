import http.server
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from boomerang import verifier

ROOT = Path(__file__).resolve().parent.parent
MIN = ROOT / "tests" / "fixtures" / "leaseweb-min"


# ---------------------------------------------------------------- run_pytest / junit
def _app(tmp_path, test_body: str) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    return tmp_path


def test_run_pytest_counts_pass_and_fail(tmp_path):
    d = _app(tmp_path, "def test_ok():\n    assert 1\n\ndef test_bad():\n    assert 1 == 2, 'nope'\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb")
    assert (r.passed, r.failed, r.errors, r.total) == (1, 1, 0, 2)
    assert not r.ok
    assert r.failures[0].test.endswith("test_bad") and "nope" in r.failures[0].message


def test_run_pytest_collection_error_is_reported(tmp_path):
    d = _app(tmp_path, "import does_not_exist\n\ndef test_x():\n    pass\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb")
    assert not r.ok and r.errors >= 1 and r.failures


def test_run_pytest_strips_wrong_platform_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://leak")
    d = _app(tmp_path, "import os\n\ndef test_env():\n"
                       "    assert 'AWS_ENDPOINT_URL' not in os.environ\n"
                       "    assert os.environ['DATABASE_URL'] == 'postgresql://mine'\n")
    r = verifier.run_pytest(d, d / "tests", {"DATABASE_URL": "postgresql://mine"}, "leaseweb")
    assert r.ok, r.output_tail


def test_run_pytest_timeout(tmp_path):
    d = _app(tmp_path, "import time\n\ndef test_slow():\n    time.sleep(10)\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb", timeout_s=3)
    assert not r.ok and "timed out" in r.output_tail


# ---------------------------------------------------------------- env files
def test_laptop_env_rewrites_container_hosts():
    env = verifier.laptop_env(MIN)
    assert env["DATABASE_URL"] == "postgresql://shoplite:dev@127.0.0.1:5432/shoplite"
    assert env["AMQP_URL"] == "amqp://shoplite:dev@127.0.0.1:5672/shoplite"
    assert env["S3_ENDPOINT_URL"] == "http://127.0.0.1:8333"


def test_laptop_env_prefers_laptop_env_file(tmp_path):
    (tmp_path / ".env.example").write_text("A=container\n", encoding="utf-8")
    (tmp_path / "laptop.env").write_text("A=laptop # comment\n", encoding="utf-8")
    assert verifier.laptop_env(tmp_path) == {"A": "laptop"}


# ---------------------------------------------------------------- config check
def test_config_check_passes_on_fixture():
    c = verifier.check_config(MIN)
    assert c.ok, c.detail


def test_config_check_catches_bad_yaml(tmp_path):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw" / "docker-compose.yml").write_text("services: [unclosed\n", encoding="utf-8")
    assert not verifier.check_config(tmp_path / "lw").ok


@pytest.mark.skipif(not verifier.find_bash(), reason="no usable bash (Git Bash) available")
def test_config_check_catches_bad_deploy_script(tmp_path):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw" / "deploy.sh").write_text("if then fi (\n", encoding="utf-8")
    c = verifier.check_config(tmp_path / "lw")
    assert not c.ok and "deploy.sh" in c.detail


def test_config_check_requires_cloud_config_header(tmp_path):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw" / "cloud-init.yaml").write_text("packages: []\n", encoding="utf-8")
    assert not verifier.check_config(tmp_path / "lw").ok


# ---------------------------------------------------------------- full verify with fake docker
class FakeDocker:
    """Records docker compose calls; `fail_up` simulates a service that never gets healthy."""

    def __init__(self, fail_up=False):
        self.calls, self.fail_up = [], fail_up

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if Path(cmd[0]).stem.lower() == "bash":
            return subprocess.run(cmd, **kw)
        rc = 1 if (self.fail_up and "up" in cmd) else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="svc  exited (1)", stderr="dependency failed" if rc else "")


@pytest.fixture
def health_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/health"
    srv.shutdown()


def test_verify_all_green_with_fake_docker(monkeypatch, health_server):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    v = verifier.verify(MIN, run=FakeDocker(), health_url=health_server)
    assert [c.name for c in v.checks] == ["config", "boot", "schema", "tests", "health"]
    assert v.ok, [c.detail for c in v.checks]
    assert v.tests.passed == 1


def test_verify_stops_when_boot_fails(monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    v = verifier.verify(MIN, run=FakeDocker(fail_up=True))
    assert [c.name for c in v.checks] == ["config", "boot"]
    assert not v.ok and "dependency failed" in v.checks[1].detail


def test_boot_without_docker_is_a_clear_failure(monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: None)
    c = verifier.check_boot(MIN)
    assert not c.ok and "Docker is not installed" in c.detail


def test_health_check_fails_cleanly():
    c = verifier.check_health("http://127.0.0.1:9/health", timeout_s=1.5)
    assert not c.ok and "health check failed" in c.detail


def test_service_and_reset_issue_the_right_compose_commands(monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeDocker()
    verifier.stop_service(MIN, "worker", run=fake)
    assert fake.calls[-1][-2:] == ["stop", "worker"]
    verifier.start_service(MIN, "worker", run=fake)
    assert fake.calls[-1][-2:] == ["start", "worker"]
    verifier.reset_stack(MIN, run=fake)
    assert ["down", "-v"] == fake.calls[-2][-2:] and "up" in fake.calls[-1]


def test_compose_gets_secrets_env_file(tmp_path):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw" / "secrets.env").write_text("POSTGRES_PASSWORD=s3cret\n", encoding="utf-8")
    cmd = verifier._compose(tmp_path / "lw")
    files = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--env-file"]
    assert [Path(f).name for f in files] == [".env.example", "secrets.env"]  # secrets win


def test_verify_stops_worker_during_tests_and_restarts_it(monkeypatch, health_server):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeDocker()
    verifier.verify(MIN, run=fake, health_url=health_server)
    tail = [c[-2:] for c in fake.calls if c[0] == "docker"][-2:]
    assert tail == [["stop", "worker"], ["start", "worker"]]


def test_run_tests_uses_the_apps_own_tests_folder(tmp_path):
    d = _app(tmp_path, "def test_ok():\n    pass\n")
    r = verifier.run_tests(d, {}, "aws", 0)
    assert r.ok and r.total == 1 and r.attempt == 0


def test_test_env_is_an_allowlist(tmp_path, monkeypatch):
    """L8: the AI-edited app never sees our API key or AWS credentials."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "real-aws-secret")
    monkeypatch.setenv("SOME_RANDOM_VAR", "x")
    d = _app(tmp_path, "import os\n\ndef test_env():\n"
                       "    for k in ('ANTHROPIC_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'SOME_RANDOM_VAR'):\n"
                       "        assert k not in os.environ, k\n"
                       "    assert os.environ['PYTHONUTF8'] == '1'\n"
                       "    assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n")
    r = verifier.run_tests(d, {}, "leaseweb", 1)
    assert r.ok, r.output_tail


def test_leaseweb_env_is_laptop_env():
    assert verifier.leaseweb_env(MIN) == verifier.laptop_env(MIN)


def test_aws_env_includes_last_provisioned_names(monkeypatch):
    from boomerang import provisioner_aws
    monkeypatch.setattr(provisioner_aws, "last_names", lambda: {"ORDERS_TABLE": "orders"})
    env = verifier.aws_env()
    assert env["ORDERS_TABLE"] == "orders" and env["AWS_ENDPOINT_URL"] == "http://127.0.0.1:4566"


@pytest.mark.integration
@pytest.mark.skipif(not verifier.shutil.which("docker"), reason="needs Docker")
def test_real_boot_of_minimal_stack():
    try:
        c = verifier.check_boot(MIN)
        assert c.ok, c.detail
    finally:
        verifier.teardown(MIN)


# ---------------------------------------------------------------- added
def test_junit_counts_skipped_tests(tmp_path):
    d = _app(tmp_path, "import pytest\n\ndef test_ok():\n    pass\n\n"
                       "@pytest.mark.skip(reason='x')\ndef test_skip():\n    pass\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb")
    assert (r.passed, r.total, r.failed, r.errors) == (1, 2, 0, 0) and r.ok


def test_no_tests_is_not_ok(tmp_path):
    d = _app(tmp_path, "x = 1\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb")
    assert r.total == 0 and not r.ok


def test_traceback_is_trimmed(tmp_path):
    d = _app(tmp_path, "def test_long():\n    raise AssertionError('\n'.join(str(i) for i in range(500)))\n")
    r = verifier.run_pytest(d, d / "tests", {}, "leaseweb")
    assert len(r.failures[0].traceback.splitlines()) <= 60


def test_kv_tables_parsed_from_schema(tmp_path):
    (tmp_path / "schema.sql").write_text(
        'CREATE TABLE IF NOT EXISTS "orders" (order_id TEXT PRIMARY KEY, data JSONB NOT NULL);\n'
        "create table events (id text primary key);\nCREATE INDEX orders_c ON orders (customer_id);\n",
        encoding="utf-8")
    assert verifier._kv_tables(tmp_path) == ["orders", "events"]


def test_schema_check_without_schema_file_is_ok(tmp_path):
    assert verifier.check_schema(tmp_path, {}).ok


def test_schema_check_reports_unreachable_postgres(tmp_path):
    (tmp_path / "schema.sql").write_text("CREATE TABLE orders (id text);", encoding="utf-8")
    c = verifier.check_schema(tmp_path, {"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db"})
    assert not c.ok and ("OperationalError" in c.detail or "ConnectionTimeout" in c.detail)


def test_config_check_missing_compose(tmp_path):
    c = verifier.check_config(tmp_path)
    assert not c.ok and "docker-compose.yml missing" in c.detail


def test_config_check_catches_bad_json(tmp_path):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw" / "definitions.json").write_text("{not json", encoding="utf-8")
    assert not verifier.check_config(tmp_path / "lw").ok


def test_config_check_reports_compose_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd, **kw):
        if Path(cmd[0]).stem.lower() == "bash":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "service api: undefined volume")
    c = verifier.check_config(MIN, run=run)
    assert not c.ok and "undefined volume" in c.detail


def test_laptop_env_with_no_files_is_empty(tmp_path):
    assert verifier.laptop_env(tmp_path) == {}


def test_read_env_file_handles_quotes_comments_and_blank_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# c\n\nA="1"\nB=\'two\'\nC=x # trailing\nD=a=b\nnot a pair\n', encoding="utf-8")
    assert verifier.read_env_file(p) == {"A": "1", "B": "two", "C": "x", "D": "a=b"}


def test_verify_emits_one_event_per_check(monkeypatch, health_server):
    from boomerang.events import Emitter
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    events = []
    verifier.verify(MIN, run=FakeDocker(), health_url=health_server, emit=Emitter("t", [events.append]))
    assert [e.data["check"]["name"] for e in events] == ["config", "boot", "schema", "tests", "health"]
    assert all(e.stage == "verify" for e in events)


def test_verify_reports_failing_app_tests(tmp_path, monkeypatch, health_server):
    shutil.copytree(MIN, tmp_path / "lw")
    (tmp_path / "lw/app/tests/test_min.py").write_text("def test_broken():\n    assert False\n", encoding="utf-8")
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    v = verifier.verify(tmp_path / "lw", run=FakeDocker(), health_url=health_server)
    tests = next(c for c in v.checks if c.name == "tests")
    assert not tests.ok and tests.detail == "0/1 passed" and not v.ok


def test_find_bash_skips_the_wsl_launcher(monkeypatch):
    """On Windows the System32 bash.exe is WSL's launcher; it must never be picked."""
    monkeypatch.setattr(verifier.os, "name", "nt")
    monkeypatch.setattr(verifier.shutil, "which",
                        lambda name: r"C:\WINDOWS\system32\bash.exe" if name == "bash" else None)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    assert verifier.find_bash() is None


def test_reset_data_on_partial_stack_leaves_aws_untouched(tmp_path):
    """docs/16 §5: reset_data reads schema.sql and definitions.json, so an empty/partial stack
    with only AWS env vars never touches AWS."""
    (tmp_path / "laptop.env").write_text(
        "AWS_REGION=us-east-1\nAWS_ENDPOINT_URL=http://127.0.0.1:4566\nORDER_EVENTS_QUEUE=order-events\n",
        encoding="utf-8",
    )
    removed = verifier.reset_data(tmp_path)
    assert removed == {"rows": 0, "messages": 0, "objects": 0}
