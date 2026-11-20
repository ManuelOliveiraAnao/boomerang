"""Run the pipeline from the UI (owner: D, task D7).

docs/15 L2: the approval gate is two calls, and the UI never runs the pipeline inside
Streamlit's script thread. Each step is a `boomerang <cmd>` subprocess (list args, never a
shell); the page tails runs/<id>/events.jsonl and run.json while it runs.

    Migrate      → boomerang plan <app> --run-id <id>        (exit 0/4 → awaiting approval)
    Approve plan → boomerang approve <id> --by ui
                   then boomerang execute <id>
    Round trip   → boomerang roundtrip <id>
"""
from __future__ import annotations

import importlib.util
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ui.data import ROOT, RUNS_DIR

EXIT_TEXT = {0: "passed", 1: "failed", 3: "error", 4: "awaiting approval"}


def new_run_id() -> str:
    """Same format as boomerang.pipeline.new_run_id: YYYYMMDD-HHMMSS-<4 hex>."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _cli() -> list[str] | None:
    exe = Path(sys.executable).parent / ("boomerang.exe" if os.name == "nt" else "boomerang")
    if exe.is_file():
        return [str(exe)]
    if importlib.util.find_spec("boomerang.cli") is not None:
        return [sys.executable, "-c", "from boomerang.cli import app; app()"]
    return None


def available() -> tuple[bool, str]:
    """Whether the engine's CLI is installed in this environment."""
    try:
        has_pipeline = importlib.util.find_spec("boomerang.pipeline") is not None
    except (ImportError, ValueError):
        has_pipeline = False
    if not has_pipeline or _cli() is None:
        return False, "The engine isn't installed here (needs C's pipeline: `uv pip install -e .`)."
    return True, ""


@dataclass
class Step:
    """One CLI call running in the background for a run."""

    name: str                      # plan | approve | execute | roundtrip
    run_id: str
    proc: subprocess.Popen
    log: Path
    started: float = field(default_factory=time.time)
    then: list[str] = field(default_factory=list)   # steps to start after this one succeeds

    @property
    def done(self) -> bool:
        return self.proc.poll() is not None

    @property
    def code(self) -> int | None:
        return self.proc.poll()

    def tail(self, n: int = 12) -> str:
        try:
            lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])


def _args(name: str, run_id: str, app_dir: str) -> list[str]:
    return {
        "plan": ["plan", app_dir, "--run-id", run_id],
        "approve": ["approve", run_id, "--by", "ui"],
        "execute": ["execute", run_id],
        "roundtrip": ["roundtrip", run_id],
    }[name]


def start(name: str, run_id: str, app_dir: str = "sample-app/aws", then: list[str] | None = None) -> Step:
    """Start `boomerang <name>` for this run in the background; output goes to runs/<id>/ui-<name>.log."""
    cli = _cli()
    if cli is None:
        raise RuntimeError("boomerang CLI not found")
    root = RUNS_DIR / run_id
    root.mkdir(parents=True, exist_ok=True)
    log = root / f"ui-{name}.log"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1",
           "COLUMNS": "120"}
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cli + _args(name, run_id, app_dir), cwd=ROOT, stdout=fh,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    return Step(name=name, run_id=run_id, proc=proc, log=log, then=list(then or []))


def advance(step: Step, app_dir: str = "sample-app/aws") -> Step | None:
    """If `step` finished OK and has follow-ups, start the next one."""
    if not step.done or not step.then:
        return None
    ok = step.code == 0 or (step.name == "plan" and step.code == 4)
    if not ok:
        return None
    nxt, *rest = step.then
    return start(nxt, step.run_id, app_dir, then=rest)


def run_status(run_id: str) -> str | None:
    """status from runs/<id>/run.json, if written yet."""
    import json
    p = RUNS_DIR / run_id / "run.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return None
