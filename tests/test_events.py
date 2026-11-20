from __future__ import annotations

import io
import json
import logging
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from rich.console import Console

from boomerang.events import Emitter, console_sink, jsonl_sink, read_events
from boomerang.models import Check


def test_emit_writes_jsonl_and_reads_back(tmp_path):
    path = tmp_path / "run" / "events.jsonl"
    emitter = Emitter("r1", [jsonl_sink(path)])
    first = emitter.emit("delock", "app/db.py:get_order rewritten", "success", function="get_order")
    emitter.emit("verify", "boot ok")
    events = read_events(path)
    assert [e.message for e in events] == ["app/db.py:get_order rewritten", "boot ok"]
    assert events[0] == first and events[0].data == {"function": "get_order"} and events[1].data is None
    assert b"\r" not in path.read_bytes()


def test_unknown_stage_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        Emitter("r1", []).emit("baseline", "not a stage")


def test_data_is_made_json_safe(tmp_path):
    event = Emitter("r1", []).emit(
        "data", "counts", path=Path("app") / "db.py", amount=Decimal(12), price=Decimal("1.5"),
        check=Check(name="orders", ok=True), tags={"b", "a"},
    )
    assert event.data == {"path": "app/db.py", "amount": 12, "price": 1.5,
                          "check": {"name": "orders", "ok": True, "detail": "", "duration_s": 0.0}, "tags": ["a", "b"]}
    json.dumps(event.data)


def test_failing_sink_never_stops_the_pipeline_and_logs_once(caplog):
    seen = []

    def broken(_event):
        raise RuntimeError("UI went away")

    emitter = Emitter("r1", [broken, seen.append])
    with caplog.at_level(logging.ERROR, logger="boomerang.events"):
        emitter.emit("scan", "one")
        emitter.emit("scan", "two")
    assert [e.message for e in seen] == ["one", "two"]
    assert len([r for r in caplog.records if "sink" in r.getMessage()]) == 1


def test_thread_safe_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    emitter = Emitter("r1", [jsonl_sink(path)])

    def work(n: int) -> None:
        for i in range(50):
            emitter.emit("fix", f"t{n}-{i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    messages = [e.message for e in read_events(path)]
    assert len(messages) == 400 and len(set(messages)) == 400


def test_sink_may_emit_without_deadlock():
    seen = []
    emitter = Emitter("r1", [])

    def echo(event):
        seen.append(event.message)
        if event.message == "outer":
            emitter.emit("report", "inner")

    emitter.add_sink(echo)
    emitter.emit("report", "outer")
    assert seen == ["outer", "inner"]


def test_read_events_skips_a_torn_last_line_only(tmp_path):
    path = tmp_path / "events.jsonl"
    Emitter("r1", [jsonl_sink(path)]).emit("scan", "whole")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": 1.0, "run_id": "r1", "sta')
    assert [e.message for e in read_events(path)] == ["whole"]
    assert read_events(tmp_path / "missing.jsonl") == []
    path.write_text('{"broken": \n' + path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_events(path)


def test_console_sink_formats_and_escapes_markup():
    buf = io.StringIO()
    console = Console(file=buf, width=200, color_system=None, force_terminal=False)
    emitter = Emitter("r1", [console_sink(console)])
    emitter.emit("delock", "app/db.py:get_order rewritten", "success")
    emitter.emit("verify", "[red]not markup[/red]", "error")
    out = buf.getvalue()
    assert "[delock] ✅ app/db.py:get_order rewritten" in out
    assert "[verify] ❌ [red]not markup[/red]" in out
