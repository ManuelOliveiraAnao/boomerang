"""The demo LLM cache: check it before committing, freeze what it was recorded with (docs/15 §8). Owner C, C7.

The golden run records only the answers it needs into an empty ``demo/llm-cache/`` (``BOOMERANG_LLM_MODE=record``,
``BOOMERANG_CACHE_DIR=demo/llm-cache``); the demo replays them with ``BOOMERANG_LLM_MODE=replay`` (no model, no
network). ``boomerang cache check --run golden`` must be green before the cache and ``runs/golden`` are committed:

- **entries**: each one parses, its file name is its key, the key is what ``llm.cache_key`` gives for its stored
  provider/model/system/user/schema, and the stored answer validates against that schema;
- **secrets**: nothing ``redact.find_secrets`` would replace, in any entry or in any file of the given runs
  (``secrets.env`` holds the secrets by design: it must be git-ignored instead, checked with ``git check-ignore``);
- **coverage**: every LLM call of the runs (their events carry ``cache_key``) has an entry (a missing one was made
  in ``live`` mode), and every entry was used by one of the runs (an unused one is stale; ``--prune`` deletes it);
- **freeze**: ``boomerang cache freeze`` writes ``manifest.json`` (sha256 of every ``prompts/*.md`` and of each
  LLM-facing schema, provider, model, entries). Any later edit to a prompt or schema changes the cache keys, so
  replay would miss: the check reports it as drift, and ``tests/test_democache.py`` fails on it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from boomerang import llm, models
from boomerang.config import get_settings
from boomerang.fsutil import atomic_write_text
from boomerang.redact import find_secrets, redact_with_map, secret_values

MANIFEST = "manifest.json"
LLM_SCHEMAS = ("AppModel", "Assessment", "FunctionRewrite", "FixResult")     # the LLM-facing models (docs/02 §3)
ENTRY_FIELDS = ("key", "label", "provider", "model", "schema", "system", "user", "response")
SECRET_FILE = "secrets.env"
MAX_SCAN_BYTES = 5_000_000


@dataclass
class Entry:
    key: str
    label: str
    schema: str
    path: Path


@dataclass
class CacheReport:
    cache_dir: Path
    entries: list[Entry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)       # any of these: don't commit, don't demo
    notes: list[str] = field(default_factory=list)          # worth a look, not blocking
    unused: list[Entry] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)        # "label (key)" of calls with no entry
    drift: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.problems or self.unused or self.missing or self.drift)


# ---------- fingerprint ----------

def fingerprint() -> dict[str, Any]:
    """What the cache keys depend on, besides the code being migrated: prompts, schemas, provider, model."""
    s = get_settings()
    prompts = {p.name: _sha(p.read_bytes().replace(b"\r\n", b"\n"))
               for p in sorted(s.prompts_dir.glob("*.md"))}
    schemas = {name: _sha(json.dumps(getattr(models, name).model_json_schema(), sort_keys=True).encode("utf-8"))
               for name in LLM_SCHEMAS}
    return {"provider": s.provider, "model": s.model, "prompts": prompts, "schemas": schemas}


def write_manifest(cache_dir: Path, run_id: str) -> Path:
    """``manifest.json`` next to the entries: run it right after the golden run (prompt freeze)."""
    cache_dir = Path(cache_dir)
    keys = sorted(p.stem for p in cache_dir.glob("*.json") if p.name != MANIFEST)
    data = {"recorded_run": run_id, "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            **fingerprint(), "entries": keys}
    path = cache_dir / MANIFEST
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False) + "\n")
    return path


def drift(cache_dir: Path) -> list[str]:
    """Differences between the manifest and now. ``[]`` when there's no manifest yet."""
    path = Path(cache_dir) / MANIFEST
    if not path.is_file():
        return []
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{MANIFEST} is unreadable ({type(e).__name__})"]
    now = fingerprint()
    out: list[str] = []
    for key in ("provider", "model"):
        if recorded.get(key) != now[key]:
            out.append(f"{key} is {now[key]!r} but the cache was recorded with {recorded.get(key)!r}")
    for kind in ("prompts", "schemas"):
        before, after = recorded.get(kind) or {}, now[kind]
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                what = "added" if name not in before else "removed" if name not in after else "changed"
                out.append(f"{kind[:-1]} {name} {what} since the recording: replay will miss; re-record the golden run")
    return out


# ---------- the check ----------

def check(cache_dir: Path, run_dirs: list[Path] | None = None) -> CacheReport:
    cache_dir = Path(cache_dir)
    report = CacheReport(cache_dir)
    if not cache_dir.is_dir():
        report.problems.append(f"no cache folder {cache_dir}")
        return report
    for path in sorted(cache_dir.glob("*.json")):
        if path.name != MANIFEST:
            _check_entry(path, report)
    run_dirs = [Path(r) for r in run_dirs or []]
    used: dict[str, str] = {}
    for root in run_dirs:
        if not (root / "events.jsonl").is_file():
            report.problems.append(f"{root.name}: no events.jsonl (not a run folder?)")
            continue
        used.update(_llm_calls(root / "events.jsonl", report))
        _scan_run(root, report)
    if run_dirs:
        keys = {e.key for e in report.entries}
        report.missing = [f"{label} ({key[:12]})" for key, label in sorted(used.items(), key=lambda kv: kv[1])
                          if key not in keys]
        report.unused = [e for e in report.entries if e.key not in used]
    report.drift = drift(cache_dir)
    if not report.entries:
        report.notes.append("the cache is empty")
    return report


def prune(report: CacheReport) -> list[Path]:
    """Delete the unused (stale) entries of a check that was given runs. Returns what was deleted."""
    gone = []
    for e in report.unused:
        e.path.unlink(missing_ok=True)
        gone.append(e.path)
    report.entries = [e for e in report.entries if e not in report.unused]
    report.unused = []
    return gone


def _check_entry(path: Path, report: CacheReport) -> None:
    name = path.name
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        report.problems.append(f"{name}: unreadable ({type(e).__name__})")
        return
    if not isinstance(data, dict) or any(k not in data for k in ENTRY_FIELDS):
        report.problems.append(f"{name}: not a cache entry (needs {', '.join(ENTRY_FIELDS)})")
        return
    found = _secrets_in(text, json_lines=False)
    if found:
        report.problems.append(f"{name}: contains a secret ({', '.join(found)}); delete the entry and re-record")
    entry = Entry(key=str(data["key"]), label=str(data["label"]), schema=str(data["schema"]), path=path)
    report.entries.append(entry)
    if path.stem != entry.key:
        report.problems.append(f"{name}: file name is not its key {entry.key[:12]}…")
    schema = getattr(models, entry.schema, None)
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        report.notes.append(f"{name} ({entry.label}): schema {entry.schema} is not in boomerang.models; key not "
                            "re-checked")
        return
    expected = llm.cache_key(str(data["provider"]), str(data["model"]), str(data["system"]), str(data["user"]), schema)
    if expected != entry.key:
        report.problems.append(f"{name} ({entry.label}): its key doesn't match its content (schema {entry.schema} "
                               "changed since it was recorded?)")
    try:
        schema.model_validate(data["response"])
    except ValidationError as e:
        report.problems.append(f"{name} ({entry.label}): the stored answer no longer validates as {entry.schema} "
                               f"({e.error_count()} error(s))")


def _llm_calls(events: Path, report: CacheReport) -> dict[str, str]:
    """``{cache_key: label}`` of every LLM call a run made (llm.structured emits ``cache_key``)."""
    calls: dict[str, str] = {}
    for n, line in enumerate(events.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = (json.loads(line).get("data") or {})
        except (ValueError, AttributeError):
            report.notes.append(f"{events.parent.name}/events.jsonl:{n}: unreadable line")
            continue
        key = data.get("cache_key")
        if isinstance(key, str) and key:
            calls[key] = str(data.get("label") or key[:12])
    return calls


def _scan_run(root: Path, report: CacheReport) -> None:
    known = set(secret_values().values())
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.name == SECRET_FILE:
            _check_ignored(path, f"{root.name}/{rel}", report)
            continue
        if any(part in ("__pycache__", ".pytest_cache") for part in path.parts) or path.suffix in (".pyc", ".pyo"):
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                report.notes.append(f"{root.name}/{rel}: larger than {MAX_SCAN_BYTES} bytes, not scanned")
                continue
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue                                         # binary: nothing the LLM or the report shows
        found = _secrets_in(text, json_lines=path.suffix == ".jsonl") if path.suffix in (".json", ".jsonl")             else find_secrets(text)
        if not found:
            continue
        if path.name == ".env.example":                      # placeholders are expected there; real values aren't
            real = {name for name, value in _hits(text) if value in known}
            if real:
                report.problems.append(f"{root.name}/{rel}: contains a real secret ({', '.join(sorted(real))})")
            else:
                report.notes.append(f"{root.name}/{rel}: secret-looking placeholders ({', '.join(found)}); "
                                    "check they aren't real")
            continue
        report.problems.append(f"{root.name}/{rel}: contains a secret ({', '.join(found)})")


def _secrets_in(text: str, *, json_lines: bool) -> list[str]:
    """``find_secrets`` on the raw JSON **and** on every decoded string in it: JSON escapes newlines, so an
    ``.env``-style line inside a prompt (stored as ``\\nDB_PASSWORD=…``) only starts a line once decoded."""
    found = find_secrets(text)
    docs: list[Any] = []
    for chunk in (text.splitlines() if json_lines else [text]):
        try:
            docs.append(json.loads(chunk))
        except ValueError:
            continue
    for value in _strings(docs):
        found += find_secrets(value)
    return list(dict.fromkeys(found))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _hits(text: str) -> list[tuple[str, str]]:
    _, mapping = redact_with_map(text)
    return [(placeholder[len("<REDACTED:"):-1], value) for placeholder, value in mapping.items()]


def _check_ignored(path: Path, shown: str, report: CacheReport) -> None:
    git = shutil.which("git")
    if git is None:
        report.notes.append(f"{shown}: git not on PATH; can't check that it is ignored")
        return
    try:
        done = subprocess.run([git, "check-ignore", "-q", str(path)], cwd=path.parent, capture_output=True,
                              timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        report.notes.append(f"{shown}: git check-ignore failed ({type(e).__name__})")
        return
    if done.returncode == 1:
        report.problems.append(f"{shown}: NOT git-ignored; it holds the passwords (add `secrets.env` to .gitignore)")
    elif done.returncode != 0:
        report.notes.append(f"{shown}: not in a git repository; can't check that it is ignored")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
