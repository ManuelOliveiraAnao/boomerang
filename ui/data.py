"""Where the UI reads its data from: fixtures, a recorded run, or the live pipeline.

Everything here returns plain dicts so the UI keeps working before
`boomerang/models.py` exists. Tabs validate with the Pydantic models once C1 lands.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
RUNS_DIR = ROOT / os.getenv("BOOMERANG_RUNS_DIR", "runs")


@dataclass
class Source:
    """A place the UI reads artefacts from (a fixtures folder or a run folder)."""

    label: str
    path: Path | None
    kind: str  # "fixtures" | "run" | "none"
    events: list[dict[str, Any]] = field(default_factory=list)

    def json(self, name: str) -> Any | None:
        """Load `<name>.json` from this source, or None if it isn't there yet."""
        if self.path is None:
            return None
        p = self.path / f"{name}.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def read_events(path: Path) -> list[dict[str, Any]]:
    f = path / "events.jsonl"
    if not f.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def list_runs() -> list[Path]:
    """Run folders, newest first; `golden` is always offered first if present."""
    if not RUNS_DIR.is_dir():
        return []
    runs = [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    runs.sort(key=lambda p: (p.name != "golden", -p.stat().st_mtime))
    return runs


def fixtures_source() -> Source:
    if FIXTURES_DIR.is_dir():
        return Source("Fixtures", FIXTURES_DIR, "fixtures", read_events(FIXTURES_DIR))
    return Source("No data yet", None, "none")


def run_source(path: Path) -> Source:
    return Source(f"Run {path.name}", path, "run", read_events(path))


def stage_status(events: list[dict[str, Any]]) -> dict[str, str]:
    """Map each pipeline stage to done / running / failed / waiting from its events."""
    status: dict[str, str] = {}
    for ev in events:
        stage = ev.get("stage")
        if not stage:
            continue
        level = ev.get("level", "info")
        if level == "error":
            status[stage] = "failed"
        elif level == "success":
            status[stage] = "done"
        elif stage == "approve" and status.get(stage) != "done":
            status[stage] = "waiting"
        elif status.get(stage) not in ("done", "failed"):
            status[stage] = "running"
    return status


def llm_badge() -> tuple[str, bool]:
    """Text for the LLM badge and whether inference is local."""
    provider = os.getenv("BOOMERANG_LLM_PROVIDER", "ollama")
    model = os.getenv("BOOMERANG_MODEL", "qwen2.5-coder:7b")
    local = provider == "ollama"
    return f"{provider} · {model} · {'local' if local else 'cloud fallback'}", local
