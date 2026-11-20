"""Role A CLI commands, run through Typer's test runner."""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boomerang import lab
from boomerang.cli import app

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


@pytest.fixture(autouse=True)
def _cwd(monkeypatch):
    monkeypatch.chdir(ROOT)  # default paths (sample-app/aws, tests/fixtures/...) are repo-relative


def test_help_lists_role_a_commands():
    out = runner.invoke(app, ["--help"]).output
    for cmd in ("lab", "aws", "verify", "data"):
        assert cmd in out


def test_lab_up_status_reset():
    assert runner.invoke(app, ["lab", "up"]).exit_code == 0
    r = runner.invoke(app, ["lab", "status"])
    assert r.exit_code == 0 and "OK moto" in r.output
    assert runner.invoke(app, ["lab", "reset"]).exit_code == 0


def test_lab_up_docker_backend_fails_cleanly(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    r = runner.invoke(app, ["lab", "up", "--backend", "docker"])
    assert r.exit_code == 1 and "Docker is not installed" in r.output


def test_aws_provision_with_seed():
    r = runner.invoke(app, ["aws", "provision", "--reset", "--seed", "5"])
    assert r.exit_code == 0, r.output
    assert "'orders': 5" in r.output and "ORDERS_TABLE=orders" in r.output


def test_aws_test_passes_for_shoplite():
    lab.up()
    lab.reset_moto()
    r = runner.invoke(app, ["aws", "test", "sample-app/aws"])
    assert r.exit_code == 0 and "7/7 passed" in r.output, r.output


def test_aws_test_fails_with_exit_code_for_broken_app(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    r = runner.invoke(app, ["aws", "test", str(tmp_path)])  # spec tests can't import app.main
    assert r.exit_code == 1 and "FAIL" in r.output


def test_verify_without_docker_fails_at_boot():
    if lab.docker_available():
        pytest.skip("Docker present: covered by the real boot test")
    r = runner.invoke(app, ["verify", "tests/fixtures/leaseweb-min"])
    assert r.exit_code == 1 and "OK" in r.output and "config" in r.output and "boot" in r.output


def test_data_rejects_unknown_direction():
    r = runner.invoke(app, ["data", "tests/fixtures/leaseweb-min", "--direction", "sideways"])
    assert r.exit_code == 1 and "unknown direction" in r.output


def test_data_without_kv_adapter_explains_what_is_missing():
    import importlib.util
    if importlib.util.find_spec("sovereign_sdk.kv") is not None:
        pytest.skip("kv adapter exists now")
    lab.up()
    r = runner.invoke(app, ["data", "tests/fixtures/leaseweb-min"])
    assert r.exit_code == 1 and "B5" in r.output
