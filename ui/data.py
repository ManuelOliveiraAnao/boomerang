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
DEMO_RUN_DIR = FIXTURES_DIR / "run-demo"   # C's runs/<id>/ layout (docs/15 §5)
RUNS_DIR = ROOT / os.getenv("BOOMERANG_RUNS_DIR", "runs")


@dataclass
class Source:
    """A run folder in C's layout: run.json (RunSummary), events.jsonl, pam.json,
    assessment.json, delock.json, diffs/, leaseweb/ (docs/15 §5)."""

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

    @property
    def summary(self) -> dict[str, Any]:
        """run.json (RunSummary as a dict), or {} if the run hasn't written it yet."""
        return self.json("run") or {}

    @property
    def is_demo(self) -> bool:
        return bool(self.summary.get("fixtures_used"))

    def text(self, rel: str) -> str | None:
        """A text file inside the run folder (e.g. 'diffs/x.diff'), or None."""
        if self.path is None:
            return None
        p = (self.path / rel).resolve()
        if self.path.resolve() not in p.parents or not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
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
    runs = [p for p in RUNS_DIR.iterdir()
            if p.is_dir() and ((p / "run.json").is_file() or (p / "events.jsonl").is_file())]
    runs.sort(key=lambda p: (p.name != "golden", -p.stat().st_mtime))
    return runs


def fixtures_source() -> Source:
    if DEMO_RUN_DIR.is_dir():
        return Source("Demo run", DEMO_RUN_DIR, "fixtures", read_events(DEMO_RUN_DIR))
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


# ---------------------------------------------------------------- secrets on screen
# The demo is projected and the repo is public: never show generated secrets (docs/13).
import re as _re

HIDDEN_FILES = {"secrets.env", "laptop.env", ".env"}
_SECRET_KEY = r"[A-Za-z0-9_]*(?:PASS(?:WORD)?|SECRET|TOKEN|CREDENTIAL|ACCESS_KEY|API_KEY)[A-Za-z0-9_]*"
_PATTERNS = [
    # KEY=value / KEY: value (env, YAML), unless the value is a ${VAR} reference
    (_re.compile(rf"(?im)^(\s*-?\s*{_SECRET_KEY}\s*[=:]\s*)(?!\$\{{)(?!CHANGE_ME)(\S.*)$"), r"\1••••••"),
    # "password": "x" in JSON
    (_re.compile(r'(?i)("(?:password|password_hash|secretKey|accessKey|secret|token)"\s*:\s*")[^"]*(")'), r"\1••••••\2"),
    # scheme://user:password@host
    (_re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s]+:)(?!\$\{)[^@\s]+(@)"), r"\1••••••\2"),
]


def mask_secrets(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
