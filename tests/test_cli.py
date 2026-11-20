from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from boomerang.cli import app, light_counts, lights_table
from boomerang.config import get_settings
from boomerang.events import Emitter
from boomerang.models import Assessment, CodeRef, Finding, RunSummary
from boomerang.pipeline import RunContext, list_runs, load_run

runner = CliRunner()

COMMANDS = ["lab", "scan", "migrate", "plan", "approve", "execute", "verify", "data", "roundtrip",
            "cost", "llm-check", "runs"]


def _assessment() -> Assessment:
    ref = CodeRef(file="app/db.py", line=12, symbol="put_order", operation="put_item", snippet="table.put_item(...)")
    findings = [
        Finding(analysis="a", component_id="receipts", feature="basic object ops", light="green", what_changes="endpoint"),
        Finding(analysis="a", component_id="orders", feature="conditional_write", light="amber",
                what_changes="kv adapter", evidence=[ref]),
        Finding(analysis="a", component_id="receipt_thumbnailer", feature="lambda", light="red", what_changes="human"),
    ]
    return Assessment(findings=findings, overall="amber", summary="s")


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.output
    lab = runner.invoke(app, ["lab", "--help"])
    assert all(c in lab.output for c in ("up", "down", "status"))


def test_unbuilt_command_exits_3_and_names_the_owner(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 3
    result = runner.invoke(app, ["migrate", str(tmp_path), "--yes"])        # plan runs; B's scan isn't built
    assert result.exit_code == 3


def test_llm_check_answers_with_a_fake_model(fake_llm):
    fake_llm.responses["llm-check"] = lambda call: call["schema"](analysis="2 + 3 is 5.", answer=5)
    result = runner.invoke(app, ["llm-check"])
    assert result.exit_code == 0 and "answer=5" in result.output
    assert "ollama · qwen2.5-coder:7b · local" in result.output


def test_llm_check_replay_miss_exits_3(monkeypatch):
    monkeypatch.setenv("BOOMERANG_LLM_MODE", "replay")
    get_settings.cache_clear()
    result = runner.invoke(app, ["llm-check"])
    assert result.exit_code == 3 and "CacheMiss" in result.output


def test_unknown_run_exits_3():
    result = runner.invoke(app, ["verify", "no-such-run"])
    assert result.exit_code == 3 and "no run" in result.output


def test_runs_lists_saved_runs():
    assert runner.invoke(app, ["runs"]).output.strip() == "no runs yet"
    root = get_settings().runs_dir / "20261120-101500-abcd"
    summary = RunSummary(run_id=root.name, source_dir="sample-app/aws", status="awaiting_approval",
                         started_at=1_700_000_000.0, out_dir=str(root))
    RunContext(root.name, root, Path("sample-app/aws"), summary, Emitter(root.name)).save()
    assert load_run(root.name) == summary and [s.run_id for s in list_runs()] == [root.name]
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0 and root.name in result.output and "awaiting_approval" in result.output


def test_lights_table_counts():
    assessment = _assessment()
    assert light_counts(assessment) == "1 🟢 · 1 🟠 · 1 🔴"
    assert lights_table(assessment).row_count == 3
