"""Static hints: Python ast, Terraform, Dockerfile, env. Owner B, task B2.

Spec: docs/03 §3.3. Stub created by C1: B replaces the bodies; internals are up to B.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.models import AppModel


def scan_static(app_dir: Path, exclude: tuple[str, ...] = ("tests", "sovereign_sdk")) -> AppModel:
    """Proposed (docs/15 L16): static-only PAM, no AI. Used for "lock-in after" and the residual check."""
    raise NotBuilt("scanner.static.scan_static", owner="B", doc="docs/15 L16")
