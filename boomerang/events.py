"""Minimal event emitter (provisional; owner C). Stages call emit(stage, message, level, **data)."""
from __future__ import annotations

import time
from typing import Callable

from .models import Event

Sink = Callable[[Event], None]


class Emitter:
    def __init__(self, run_id: str = "dev", sinks: list[Sink] | None = None):
        self.run_id = run_id
        self.sinks = sinks or []
        self.events: list[Event] = []

    def __call__(self, stage, message: str, level: str = "info", **data) -> Event:
        ev = Event(ts=time.time(), run_id=self.run_id, stage=stage, level=level, message=message, data=data or None)
        self.events.append(ev)
        for sink in self.sinks:
            sink(ev)
        return ev


def null_emitter() -> Emitter:
    return Emitter("null")
