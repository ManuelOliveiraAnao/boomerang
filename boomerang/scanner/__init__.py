"""Scanner: static hints + AI pass → PAM. Owner B, tasks B2/B3.

Spec: docs/03 §3.3. Stub created by C1: B replaces the body.
"""

from __future__ import annotations

from pathlib import Path

from boomerang.events import Emitter
from boomerang.models import AppModel
from .ai import scan_ai
from .static import scan_static


def scan(app_dir: Path, emit: Emitter) -> AppModel:
    """Run static hints, then the AI merge pass."""
    return scan_ai(app_dir, emit)
