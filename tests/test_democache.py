"""Demo LLM cache (docs/15 §8): record → check → freeze → replay offline. No model, no Docker.

The pipeline runs for real on mini_app with the real cache layer (`llm.structured`); only the provider call
(`llm._call_ollama`) is faked. Replay then runs under another run id with the provider "stopped".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from test_delock import GET_ORDER, LIST_BY_CUSTOMER
from test_fixer import BROKEN_GET_ORDER, FIXED
from test_pipeline import SECRET, World, llm, root_of, source, verification, world  # noqa: F401 - fixtures
from typer.testing import CliRunner

from boomerang import democache, pipeline
from boomerang import fixer as fixer_mod
from boomerang import llm as llm_mod
from boomerang.cli import app as cli_app
from boomerang.config import get_settings
from boomerang.events import read_events
from boomerang.models import FunctionRewrite, TestFailure

REAL_STRUCTURED = llm_mod.structured          # `world` (through fake_llm) replaces both of these
REAL_FIX = fixer_mod.fix
ANSWERS: dict[str, BaseModel] = {
    "delock:app/db.py:get_order": FunctionRewrite(analysis="ConsistentRead dropped", new_source=GET_ORDER),
    "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="Key(...).eq", new_source=LIST_BY_CUSTOMER),
    "fix:1:app/db.py:get_order": FIXED,
}


class Provider:
    """Stands in for Ollama behind the real cache layer."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down = False

    def __call__(self, settings, system: str, user: str, schema, label: str):
        if self.down:
            raise AssertionError(f"the provider was called in replay: {label}")
        self.calls.append(label)
        answer = next(v for k, v in ANSWERS.items() if label.startswith(k))
        return answer, {"in": 100, "out": 20}


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "demo" / "llm-cache"


@pytest.fixture
def provider(world: World, cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Provider:
    p = Provider()
    monkeypatch.setattr("boomerang.llm.structured", REAL_STRUCTURED)
    monkeypatch.setattr("boomerang.fixer.fix", REAL_FIX)
    monkeypatch.setattr("boomerang.llm._call_ollama", p)
    monkeypatch.setenv("BOOMERANG_CACHE_DIR", str(cache_dir))
    world.on_verify = _break_get_order_on_attempt_1(world)
    return p


def _break_get_order_on_attempt_1(world: World) -> Callable[[Path, int], None]:
    """Verify 1 fails in get_order, so the fixer runs; its traceback has this run's absolute paths."""
    def on_verify(leaseweb_dir: Path, attempt: int) -> None:
        if attempt != 1:
            world.verifications = [verification(attempt=2)]
            return
        db = leaseweb_dir / "app" / "app" / "db.py"
        text = db.read_bytes().decode("utf-8")
        db.write_bytes(text.replace(GET_ORDER, BROKEN_GET_ORDER).encode("utf-8"))
        line = next(i for i, ln in enumerate(db.read_text(encoding="utf-8").splitlines(), 1)
                    if ln.strip() == "return item")
        failing = verification(tests_ok=False)
        assert failing.tests is not None
        failing.tests.failures = [TestFailure(
            test="tests/test_mini.py::test_order_round_trip", message="TypeError: Decimal at 0x7f3a9c2b1e50",
            traceback=f"tests/test_mini.py:14: \n\n{db}:{line}: TypeError\nE   TypeError: Decimal ({db})")]
        world.verifications = [failing, verification(attempt=2)]
    return on_verify


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("BOOMERANG_LLM_MODE", mode)
    get_settings.cache_clear()


def _use_prompts(monkeypatch: pytest.MonkeyPatch, prompts: Path) -> None:
    """Point the (cached) settings at another prompts folder. Call it after the last cache_clear()."""
    monkeypatch.setattr(get_settings(), "prompts_dir", prompts)


def _flat(output: str) -> str:
    return " ".join(output.split())                         # rich wraps long lines in CliRunner


def _record_golden(source: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _mode(monkeypatch, "record")
    summary = pipeline.migrate(source, yes=True, run_id="golden", force=True)
    assert summary.status == "passed"
    return root_of("golden")


def _rewrites(run_id: str) -> list[tuple[Any, ...]]:
    return [(r.file, r.function, r.kind, r.attempt, r.accepted, r.changed_lines, r.analysis)
            for r in pipeline.load_run(run_id).rewrites]


def _llm_events(run_id: str) -> list[dict[str, Any]]:
    return [e.data or {} for e in read_events(root_of(run_id) / "events.jsonl") if (e.data or {}).get("cache_key")]


# ---------- record → replay offline ----------

def test_replay_offline_gives_the_golden_result_under_another_run_id(provider, source, cache_dir, monkeypatch):
    golden = _record_golden(source, monkeypatch)
    assert provider.calls == ["delock:app/db.py:get_order", "delock:app/db.py:list_orders_by_customer",
                              "fix:1:app/db.py:get_order"]
    assert len(list(cache_dir.glob("*.json"))) == 3

    provider.down = True                                   # Ollama stopped, Wi-Fi off
    _mode(monkeypatch, "replay")
    replay = pipeline.migrate(source, yes=True, run_id="offline")
    assert replay.status == "passed"
    assert _rewrites("offline") == _rewrites("golden")
    assert [e["cached"] for e in _llm_events("offline")] == [True, True, True]
    assert [e["cache_key"] for e in _llm_events("offline")] == [e["cache_key"] for e in _llm_events("golden")]
    for diff in sorted((golden / "diffs").iterdir()):
        assert (root_of("offline") / "diffs" / diff.name).read_bytes() == diff.read_bytes()


def test_a_prompt_edit_after_recording_is_a_cache_miss(provider, source, cache_dir, monkeypatch, tmp_path):
    _record_golden(source, monkeypatch)
    prompts = tmp_path / "prompts"
    shutil.copytree(get_settings().prompts_dir, prompts)
    (prompts / "fix.md").write_text((prompts / "fix.md").read_text(encoding="utf-8").replace(
        "Look for a different one.", "Look again."), encoding="utf-8")
    edited = (prompts / "delock.md").read_text(encoding="utf-8").replace("You migrate ONE", "You migrate one")
    (prompts / "delock.md").write_text(edited, encoding="utf-8")
    provider.down = True
    _mode(monkeypatch, "replay")
    _use_prompts(monkeypatch, prompts)
    with pytest.raises(llm_mod.CacheMiss, match="delock:app/db.py:get_order"):
        pipeline.migrate(source, yes=True, run_id="after-edit")


# ---------- the check ----------

def test_check_is_green_for_the_recorded_cache_and_its_runs(provider, source, cache_dir, monkeypatch):
    golden = _record_golden(source, monkeypatch)
    result = democache.check(cache_dir, [golden])
    assert result.ok, (result.problems, result.missing, [e.label for e in result.unused], result.drift)
    assert sorted(e.label for e in result.entries) == sorted(ANSWERS)
    for entry in cache_dir.glob("*.json"):
        assert SECRET not in entry.read_text(encoding="utf-8")


def test_check_finds_secrets_tampering_and_bad_answers(provider, source, cache_dir, monkeypatch):
    golden = _record_golden(source, monkeypatch)
    entries = sorted(cache_dir.glob("*.json"))
    data = json.loads(entries[0].read_text(encoding="utf-8"))
    data["user"] += "\nDB_PASSWORD=hunter2-but-longer"
    entries[0].write_text(json.dumps(data), encoding="utf-8")
    data = json.loads(entries[1].read_text(encoding="utf-8"))
    data["response"] = {"analysis": "no new_source"}
    entries[1].write_text(json.dumps(data), encoding="utf-8")
    (cache_dir / "not-an-entry.json").write_text("{}", encoding="utf-8")
    shutil.copyfile(entries[2], cache_dir / ("0" * 64 + ".json"))

    result = democache.check(cache_dir, [golden])
    text = "\n".join(result.problems)
    assert f"{entries[0].name}: contains a secret (DB_PASSWORD)" in text
    assert f"{entries[0].name}" in text and "its key doesn't match its content" in text
    assert "no longer validates as FunctionRewrite" in text or "no longer validates as FixResult" in text
    assert "not-an-entry.json: not a cache entry" in text
    assert f"{'0' * 64}.json: file name is not its key" in text
    assert "hunter2" not in text                            # the check never prints a secret
    assert not result.ok


def test_check_finds_missing_and_unused_entries_and_prunes(provider, source, cache_dir, monkeypatch):
    golden = _record_golden(source, monkeypatch)
    keys = [e["cache_key"] for e in _llm_events("golden")]
    (cache_dir / f"{keys[0]}.json").unlink()                # as if that call had run in live mode
    other = json.loads((cache_dir / f"{keys[1]}.json").read_text(encoding="utf-8"))
    other.update(user=other["user"] + "\n(an older prompt)", label="delock:app/db.py:old")
    other["key"] = llm_mod.cache_key(other["provider"], other["model"], other["system"], other["user"],
                                     FunctionRewrite)
    stale = cache_dir / f"{other['key']}.json"
    stale.write_text(json.dumps(other), encoding="utf-8")

    result = democache.check(cache_dir, [golden])
    assert result.missing == [f"delock:app/db.py:get_order ({keys[0][:12]})"]
    assert [e.label for e in result.unused] == ["delock:app/db.py:old"]
    assert democache.prune(result) == [stale] and not stale.exists()
    assert result.unused == []


def test_check_scans_the_run_folder_for_secrets(provider, source, cache_dir, monkeypatch):
    golden = _record_golden(source, monkeypatch)
    assert democache.check(cache_dir, [golden]).ok
    (golden / "leaseweb" / "DEPLOY.md").write_text(f"psql with password {SECRET}\n", encoding="utf-8")
    (golden / "leaseweb" / ".env.example").write_text("DB_PASSWORD=change-me-later\n", encoding="utf-8")
    result = democache.check(cache_dir, [golden])
    assert "golden/leaseweb/DEPLOY.md: contains a secret (DB_PASSWORD)" in result.problems
    assert any(".env.example: secret-looking placeholders" in n for n in result.notes)
    assert not any(".env.example" in p for p in result.problems)
    (golden / "leaseweb" / ".env.example").write_text(f"DB_PASSWORD={SECRET}\n", encoding="utf-8")
    assert any(".env.example: contains a real secret" in p for p in democache.check(cache_dir, [golden]).problems)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_secrets_env_must_be_git_ignored(tmp_path):
    repo = tmp_path / "repo"
    run = repo / "runs" / "golden"
    (run / "leaseweb").mkdir(parents=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "leaseweb" / "secrets.env").write_text("DB_PASSWORD=pw-Zq8xR2mN5vL1kT7c\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    assert any("NOT git-ignored" in p for p in democache.check(cache, [run]).problems)
    (repo / ".gitignore").write_text("secrets.env\n", encoding="utf-8")
    assert not any("secrets.env" in p for p in democache.check(cache, [run]).problems)


# ---------- freeze ----------

def test_freeze_then_drift_on_a_prompt_or_schema_change(provider, source, cache_dir, monkeypatch, tmp_path):
    _record_golden(source, monkeypatch)
    path = democache.write_manifest(cache_dir, "golden")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["recorded_run"] == "golden" and len(manifest["entries"]) == 3
    assert {"delock.md", "fix.md", "adapter_interface.md"} <= set(manifest["prompts"])
    assert set(manifest["schemas"]) == set(democache.LLM_SCHEMAS)
    assert democache.drift(cache_dir) == []

    prompts = tmp_path / "prompts"
    shutil.copytree(get_settings().prompts_dir, prompts)
    (prompts / "fix.md").write_text("changed", encoding="utf-8")
    monkeypatch.setenv("BOOMERANG_MODEL", "qwen2.5-coder:14b")
    get_settings.cache_clear()
    _use_prompts(monkeypatch, prompts)
    assert democache.drift(cache_dir) == [
        "model is 'qwen2.5-coder:14b' but the cache was recorded with 'qwen2.5-coder:7b'",
        "prompt fix.md changed since the recording: replay will miss; re-record the golden run"]


def test_crlf_checkouts_of_the_prompts_do_not_count_as_drift(cache_dir, monkeypatch, tmp_path):
    cache_dir.mkdir(parents=True)
    democache.write_manifest(cache_dir, "golden")
    prompts = tmp_path / "prompts"
    shutil.copytree(get_settings().prompts_dir, prompts)
    for p in prompts.glob("*.md"):
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    _use_prompts(monkeypatch, prompts)
    assert democache.drift(cache_dir) == []


def test_the_committed_demo_cache_matches_the_prompts():
    """Prompt freeze (docs/15 §8 step 5): edits after the golden run need a re-recording."""
    folder = get_settings().repo_root / "demo" / "llm-cache"
    if not (folder / democache.MANIFEST).is_file():
        pytest.skip("no golden recording yet (demo/llm-cache/manifest.json)")
    changed = [d for d in democache.drift(folder) if d.startswith(("prompt ", "schema "))]
    assert changed == [], "prompts or schemas changed after the golden run: re-record demo/llm-cache (README)"


# ---------- CLI ----------

def test_cli_cache_check_and_freeze(provider, source, cache_dir, monkeypatch):
    _record_golden(source, monkeypatch)
    runner = CliRunner()
    ok = runner.invoke(cli_app, ["cache", "check", "--run", "golden"])
    assert ok.exit_code == 0, ok.output
    assert "3 entr(y/ies) (2 delock, 1 fix)" in _flat(ok.output) and "✅ cache ok" in _flat(ok.output)
    frozen = runner.invoke(cli_app, ["cache", "freeze", "--run", "golden"])
    assert frozen.exit_code == 0, frozen.output
    assert (cache_dir / democache.MANIFEST).is_file()

    (next(cache_dir.glob("[0-9a-f]*.json"))).unlink()
    bad = runner.invoke(cli_app, ["cache", "freeze", "--run", "golden"])
    assert bad.exit_code == 1 and "missing:" in _flat(bad.output) and "not frozen" in _flat(bad.output)
    assert runner.invoke(cli_app, ["cache", "check", "--run", "nope"]).exit_code == 3
