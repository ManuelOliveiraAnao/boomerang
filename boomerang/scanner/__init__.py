"""Scanner: static hints + AI pass → PAM. Owner B, tasks B2/B3.

Spec: docs/03 §3.3. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel


def scan(app_dir: Path, emit: Emitter) -> AppModel:
    """Binding (docs/03 §3.3). `CodeRef.file` paths: POSIX, relative to `app_dir`."""
    raise NotBuilt("scanner.scan", owner="B", doc="docs/03 §3.3")
