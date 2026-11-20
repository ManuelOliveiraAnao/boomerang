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


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
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
        if cmd[0] == "bash":
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


def test_freeze_and_reset_issue_the_right_compose_commands(monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeDocker()
    verifier.freeze(MIN, run=fake)
    assert fake.calls[-1][-3:] == ["stop", "api", "worker"]
    verifier.reset_stack(MIN, run=fake)
    assert ["down", "-v"] == fake.calls[-2][-2:] and "up" in fake.calls[-1]


@pytest.mark.skipif(not verifier.shutil.which("docker"), reason="needs Docker")
def test_real_boot_of_minimal_stack():
    try:
        c = verifier.check_boot(MIN)
        assert c.ok, c.detail
    finally:
        verifier.teardown(MIN)
