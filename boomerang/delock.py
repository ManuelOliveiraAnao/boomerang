"""De-lock: AWS calls → sovereign_sdk, per function (docs/02 D3/D4). Owner C, task C3.

Spec: docs/03 §3.6, docs/15 §4.4. Stub until C3.
"""

from __future__ import annotations

from pathlib import Path

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel, Assessment, FunctionRewrite, RewriteRecord


def delock(app_dir: Path, pam: AppModel, assessment: Assessment, emit: Emitter) -> list[FunctionRewrite]:
    """Binding (docs/03 §3.6). ``app_dir`` is always the copy under runs/<id>/leaseweb/app/."""
    raise NotBuilt("delock.delock", owner="C", doc="docs/15 §4.4")


def delock_detailed(app_dir: Path, pam: AppModel, assessment: Assessment, emit: Emitter,
                    diffs_dir: Path) -> list[RewriteRecord]:
    """What the pipeline calls; ``delock()`` wraps it."""
    raise NotBuilt("delock.delock_detailed", owner="C", doc="docs/15 §4.4")
