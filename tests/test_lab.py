import boto3
import pytest

from boomerang import lab


@pytest.fixture(scope="module")
def running_lab():
    lab.up(backend="auto")
    yield
    # leave it running for other test modules; `boomerang lab down` stops it


def test_moto_reachable(running_lab):
    assert lab.status()["moto"] is True


def test_aws_env_works_with_boto3(running_lab, monkeypatch):
    for k, v in lab.aws_env().items():
        monkeypatch.setenv(k, v)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="lab-smoke")
    assert "lab-smoke" in [b["Name"] for b in s3.list_buckets()["Buckets"]]


def test_reset_wipes_state(running_lab, monkeypatch):
    for k, v in lab.aws_env().items():
        monkeypatch.setenv(k, v)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="lab-reset-me")
    lab.reset_moto()
    assert s3.list_buckets()["Buckets"] == []


def test_up_is_idempotent(running_lab):
    lab.up(backend="auto")
    assert lab.moto_ready()


# ---------------------------------------------------------------- added
def test_docker_backend_without_docker_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    with pytest.raises(lab.LabError, match="Docker is not installed"):
        lab.up(backend="docker")


def test_adapter_services_without_docker_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    with pytest.raises(lab.LabError, match="need Docker"):
        lab.up(adapters=True, backend="process")


def test_auto_backend_picks_process_without_docker(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    assert lab._pick_backend("auto") == "process"


def test_down_then_up_restarts_moto():
    # restore whatever was running, so later tests (and teammates' tests) keep their databases
    had_adapters = lab.status()["test-postgres"] and lab.status()["test-rabbitmq"]
    try:
        lab.up()
        lab.down()
        assert not lab.moto_ready()
        lab.down()  # stopping twice is harmless
        lab.up()
        assert lab.moto_ready()
    finally:
        lab.up(adapters=had_adapters)


def test_wait_for_moto_times_out(monkeypatch):
    monkeypatch.setattr(lab, "moto_ready", lambda: False)
    with pytest.raises(lab.LabError, match="did not answer"):
        lab.wait_for_moto(timeout_s=0.5)


def test_status_reports_every_service():
    assert set(lab.status()) == {"moto", "test-postgres", "test-rabbitmq", "docker"}


def test_compose_file_is_valid_yaml_with_expected_services():
    import yaml
    data = yaml.safe_load(lab.COMPOSE_FILE.read_text(encoding="utf-8"))
    assert set(data["services"]) == {"moto", "test-postgres", "test-rabbitmq"}
    assert data["services"]["moto"]["ports"] == ["127.0.0.1:4566:5000"]
    assert data["services"]["test-rabbitmq"]["profiles"] == ["adapters"]
