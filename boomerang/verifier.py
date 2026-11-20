"""Live verification of the generated Leaseweb stack. Owner A, task A5.

Spec: docs/03 §3.8, docs/14 §3, helpers proposed in docs/15 L4/L8. Stub created by C1: A replaces the bodies.

L8 notes for A: ``run_tests`` always runs a subprocess (``python -m pytest -q tests -c <empty pytest.ini>
--rootdir <app_dir> -p no:cacheprovider --junitxml ...``), with ``PYTHONDONTWRITEBYTECODE=1`` and an env
built from an allowlist (PATH, SYSTEMROOT, TEMP/TMP, USERPROFILE, PYTHONUTF8=1 + platform vars), never
``os.environ.copy()``. L4: ``worker`` is stopped while the tests run.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import Platform, TestReport, Verification


def verify(leaseweb_dir: Path, attempt: int, emit: Emitter) -> Verification:
    """Binding (docs/03 §3.8): config → boot → schema → tests → health."""
    raise NotBuilt("verifier.verify", owner="A", doc="docs/03 §3.8")


def run_tests(app_dir: Path, env: dict[str, str], platform: Platform, attempt: int, emit: Emitter) -> TestReport:
    """Proposed (L8): the app's original tests in a subprocess against ``env``."""
    raise NotBuilt("verifier.run_tests", owner="A", doc="docs/15 L8")


def aws_env() -> dict[str, str]:
    """Proposed (L8): env for the AWS side (moto on 127.0.0.1:4566)."""
    raise NotBuilt("verifier.aws_env", owner="A", doc="docs/15 L8")


def leaseweb_env(leaseweb_dir: Path) -> dict[str, str]:
    """Proposed (L8): env for the Leaseweb side on the laptop (127.0.0.1 ports, values from secrets.env)."""
    raise NotBuilt("verifier.leaseweb_env", owner="A", doc="docs/15 L8")


def reset_data(leaseweb_dir: Path) -> None:
    """Proposed (L4): empty tables, queues and buckets of the verify stack, keep the schema."""
    raise NotBuilt("verifier.reset_data", owner="A", doc="docs/15 L4")


def stop_service(leaseweb_dir: Path, name: str) -> None:
    """Proposed (L4/L8): ``docker compose stop <name>`` (list args)."""
    raise NotBuilt("verifier.stop_service", owner="A", doc="docs/15 L8")


def start_service(leaseweb_dir: Path, name: str) -> None:
    """Proposed (L4/L8): ``docker compose start <name>`` (list args)."""
    raise NotBuilt("verifier.start_service", owner="A", doc="docs/15 L8")
