"""Events for every stage → `events.jsonl`, the console and the UI (docs/03 §3.1, docs/15 §2.4).

``Stage`` is a closed Literal: pipeline steps map onto it (plan → scan/assess, gate → approve,
baseline and test integrity → verify, prepare and generate → generate, lock-in after → assess,
data → data, round trip → rollback, report → report) and put the step name in ``data["step"]``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Literal

from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape

from boomerang.models import Event, Stage

Level = Literal["info", "success", "warning", "error"]
Sink = Callable[[Event], None]

log = logging.getLogger("boomerang.events")

LEVEL_ICONS = {"info": "·", "success": "✅", "warning": "⚠️", "error": "❌"}
LEVEL_STYLES = {"info": "", "success": "green", "warning": "yellow", "error": "bold red"}


def to_jsonable(obj: Any) -> Any:
    """``json.dumps(default=...)`` hook: paths, decimals, pydantic models, dataclasses, sets, dates."""
    if isinstance(obj, PurePath):
        return obj.as_posix()
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return repr(obj)


class Emitter:
    """Fans each event out to every sink. Thread-safe; a failing sink never stops the pipeline."""

    def __init__(self, run_id: str, sinks: list[Sink] | None = None) -> None:
        self.run_id = run_id
        self._sinks: list[Sink] = list(sinks or [])
        self._lock = threading.RLock()      # re-entrant: a sink may emit
        self._broken: set[int] = set()      # sinks already reported as failing (log once)

    def add_sink(self, sink: Sink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def emit(self, stage: Stage, message: str, level: Level = "info", **data: Any) -> Event:
        payload = json.loads(json.dumps(data, default=to_jsonable)) if data else None
        event = Event(ts=time.time(), run_id=self.run_id, stage=stage, level=level, message=message, data=payload)
        with self._lock:
            for sink in list(self._sinks):
                try:
                    sink(event)
                except Exception:
                    if id(sink) not in self._broken:
                        self._broken.add(id(sink))
                        log.exception("event sink %r failed; later failures of this sink are not logged", sink)
        return event


def jsonl_sink(path: Path) -> Sink:
    """Append one JSON line per event (UTF-8, LF). Opens per write: no long-lived handle on Windows,
    and a reader tailing the file always sees whole lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def sink(event: Event) -> None:
        line = event.model_dump_json() + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()

    return sink


def console_sink(console: Console | None = None) -> Sink:
    """``[delock] ✅ app/db.py:get_order rewritten``"""
    console = console or Console()

    def sink(event: Event) -> None:
        icon = LEVEL_ICONS[event.level]
        style = LEVEL_STYLES[event.level]
        text = f"{icon} {escape(event.message)}"
        if style:
            text = f"[{style}]{text}[/{style}]"
        console.print(f"[dim]\\[{event.stage}][/dim] {text}", highlight=False, soft_wrap=True)

    return sink


def read_events(path: Path) -> list[Event]:
    """All events in a JSONL file (for D7 replay). A torn last line (file still being written) is skipped."""
    path = Path(path)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[Event] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            events.append(Event.model_validate_json(line))
        except ValueError:
            if i == len(lines) - 1:
                break
            raise
    return events
