"""LLM layer: redaction, cache, Ollama (default) and Anthropic (fallback). Owner C, task C2.

Spec: docs/03 §3.2, docs/15 §3.2.

Call it as ``llm.structured(...)`` (module attribute), never ``from boomerang.llm import structured``:
the unit tests replace ``boomerang.llm.structured`` with a fake (tests/conftest.py ``fake_llm``).

Flow of one call:

1. CRLF → LF, then ``redact()`` system and user. The cache key **and** the cache file use the redacted
   text, so secrets never reach disk or ``demo/llm-cache/``.
2. Size guard: a warning above ~6000 tokens (callers keep prompts small; nothing is truncated).
3. ``key = sha256([provider, model, system, user, schema JSON])``; the label is not part of it.
4. ``replay``: cache or ``CacheMiss``. ``record``: cache hit, else call and store. ``live``: always call,
   never store.
5. Ollama (JSON-schema constrained output, temperature 0, fixed seed) with one validation retry, or the
   Anthropic fallback (``beta.messages.parse`` with server-side refusal fallback).
6. Emit ``"<label>: 12.3 s · 812→164 tokens · cached=False"`` with the same values in ``data``.

Callers keep prompts **run-independent** (no run id, absolute path, timestamp, uuid or address): pass
tracebacks and test output through ``stable_text()`` first, or ``replay`` misses on every run.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from boomerang.config import Settings, get_settings
from boomerang.fsutil import atomic_write_text
from boomerang.redact import redact

if TYPE_CHECKING:
    from boomerang.events import Emitter
    from boomerang.models import Stage

log = logging.getLogger("boomerang.llm")

SIZE_WARN_TOKENS = 6000
CHARS_PER_TOKEN = 3.5
OLLAMA_TIMEOUT_S = 180
OLLAMA_KEEP_ALIVE = "60m"           # no unload/reload freeze mid-demo
OLLAMA_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 2048}
ANTHROPIC_MAX_TOKENS = 16000
ANTHROPIC_BETAS = ["server-side-fallback-2026-07-01"]
RETRY_ERROR_CHARS = 1000


class LLMError(Exception):
    """The model answered, but not with something we can use (validation failed twice, refusal, max_tokens)."""


class LLMUnavailable(LLMError):
    """Ollama isn't running / no API key. The message says what to do. Stops the stage."""


class CacheMiss(LLMError):
    """``BOOMERANG_LLM_MODE=replay`` and no cached answer for this prompt. Stops the stage."""


# ---------- public API ----------

def structured[T: BaseModel](*, system: str, user: str, schema: type[T], label: str,
                             emit: Emitter | None = None, stage: Stage = "delock") -> T:
    """Binding (docs/03 §3.2) + two optional kwargs. Redacts, caches, calls the model, validates."""
    settings = get_settings()
    system, user = redact(_lf(system)), redact(_lf(user))

    approx_tokens = round((len(system) + len(user)) / CHARS_PER_TOKEN)
    if approx_tokens > SIZE_WARN_TOKENS:
        _notify(emit, stage, f"{label}: prompt is ~{approx_tokens} tokens (> {SIZE_WARN_TOKENS}); send less code",
                "warning", label=label, approx_tokens=approx_tokens)

    key = cache_key(settings.provider, settings.model, system, user, schema)
    path = settings.cache_dir / f"{key}.json"

    if settings.llm_mode in ("record", "replay"):
        start = time.perf_counter()
        entry = _read_cache(path, schema, emit, stage, label)
        if entry is not None:
            result, usage, recorded = entry
            _report(emit, stage, label, settings, time.perf_counter() - start, usage, cached=True,
                    key=key, recorded_duration_s=recorded)
            return result
        if settings.llm_mode == "replay":
            raise CacheMiss(f"{label}: no cached answer in {settings.cache_dir} (BOOMERANG_LLM_MODE=replay). "
                            "Record it first with BOOMERANG_LLM_MODE=record, or check that the prompts, "
                            "the model and the schema are the ones that were recorded.")

    start = time.perf_counter()
    if settings.provider == "anthropic":
        result, usage = _call_anthropic(settings, system, user, schema, label)
    else:
        result, usage = _call_ollama(settings, system, user, schema, label)
    duration = time.perf_counter() - start

    if settings.llm_mode == "record":
        _write_cache(path, {
            "key": key, "label": label, "provider": settings.provider, "model": settings.model,
            "schema": schema.__name__, "system": system, "user": user,
            "response": result.model_dump(mode="json"), "usage": usage, "duration_s": round(duration, 3),
            "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        })
    _report(emit, stage, label, settings, duration, usage, cached=False, key=key)
    return result


def cache_key(provider: str, model: str, system: str, user: str, schema: type[BaseModel]) -> str:
    """sha256 of what decides the answer. ``label`` is deliberately not part of it."""
    payload = json.dumps([provider, model, system, user, schema.model_json_schema()], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------- run-independent prompt text ----------

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
                   r"|\b[0-9a-f]{32}\b")
_ADDR = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
_RUN_ID = re.compile(r"\b\d{8}-\d{6}-[0-9a-f]{4}\b")
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_EPOCH = re.compile(r"\b1[6-9]\d{8}(?:\.\d+)?\b")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s?(?:s|sec|secs|seconds)\b")
_PATH_CHAR = r"[^\s\"'<>|*?:;,()\[\]]"            # any char of a path, slashes included
_SEG_CHAR = r"[^\s\"'<>|*?:;,()\[\]\\/]"          # a char of one path segment
_WIN_PATH = re.compile(rf"(?<![\w/\\])[A-Za-z]:[\\/](?:{_SEG_CHAR}+[\\/])*({_SEG_CHAR}*)")
_POSIX_PATH = re.compile(                          # only well-known roots: `/orders/1` is a route, not a path
    rf"(?<![\w.:/\\])/(?:home|Users|tmp|var|usr|opt|private|root|mnt|srv|runner|builds|github)/"
    rf"(?:{_SEG_CHAR}+/)*({_SEG_CHAR}*)")


def stable_text(text: str, app_dir: str | Path | None = None) -> str:
    """Make text run-independent before it goes into a prompt.

    - paths under ``app_dir`` become relative POSIX paths (``app/db.py``);
    - other absolute paths become ``<path>/<file name>``;
    - run ids → ``<run>``, uuids (with or without dashes) → ``<uuid>``, ``0x…`` addresses → ``<addr>``,
      timestamps → ``<ts>``, durations (``1.23s``, ``2 seconds``) → ``<n>s``;
    - CRLF → LF.
    """
    text = _lf(text)
    if app_dir is not None:
        text = _strip_prefix(text, Path(app_dir))
    text = _WIN_PATH.sub(lambda m: f"<path>/{m.group(1)}", text)
    text = _POSIX_PATH.sub(lambda m: f"<path>/{m.group(1)}", text)
    text = _RUN_ID.sub("<run>", text)
    text = _UUID.sub("<uuid>", text)
    text = _ISO_TS.sub("<ts>", text)
    text = _EPOCH.sub("<ts>", text)
    text = _ADDR.sub("<addr>", text)
    return _DURATION.sub("<n>s", text)


def _strip_prefix(text: str, app_dir: Path) -> str:
    """``C:\\…\\app\\app\\db.py`` → ``app/db.py`` (either slash style, any case on Windows)."""
    raw = str(app_dir.resolve()) if app_dir.exists() else str(app_dir)
    variants = {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}
    for prefix in sorted(variants, key=len, reverse=True):
        prefix = prefix.rstrip("\\/")
        if not prefix:
            continue
        pattern = re.compile(re.escape(prefix) + r"[\\/]" + rf"({_PATH_CHAR}*)", re.IGNORECASE)
        text = pattern.sub(lambda m: m.group(1).replace("\\", "/"), text)
    return text


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------- cache ----------

def _read_cache[T: BaseModel](path: Path, schema: type[T], emit: Emitter | None, stage: Stage,
                              label: str) -> tuple[T, dict[str, int | None], float | None] | None:
    if not path.is_file():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        result = schema.model_validate(entry["response"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        _notify(emit, stage, f"{label}: ignoring unreadable cache entry {path.name} ({type(e).__name__})",
                "warning", label=label, cache_file=path.name)
        return None
    usage = entry.get("usage") or {}
    return result, {"in": usage.get("in"), "out": usage.get("out")}, entry.get("duration_s")


def _write_cache(path: Path, entry: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(entry, indent=2, ensure_ascii=False) + "\n")


# ---------- providers ----------

def _call_ollama[T: BaseModel](settings: Settings, system: str, user: str, schema: type[T],
                               label: str) -> tuple[T, dict[str, int | None]]:
    import httpx
    import ollama

    client = ollama.Client(host=settings.ollama_host, timeout=OLLAMA_TIMEOUT_S)
    messages: list[dict[str, str]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    usage: dict[str, int | None] = {"in": 0, "out": 0}
    unreachable = (f"Ollama not reachable at {settings.ollama_host}; run 'ollama serve' "
                   "or set BOOMERANG_LLM_PROVIDER=anthropic")

    for attempt in (1, 2):
        try:
            resp = client.chat(model=settings.model, messages=messages, format=schema.model_json_schema(),
                               options=OLLAMA_OPTIONS, keep_alive=OLLAMA_KEEP_ALIVE)
        except (ConnectionError, httpx.ConnectError):
            raise LLMUnavailable(unreachable) from None
        except httpx.TimeoutException:
            raise LLMUnavailable(f"{label}: Ollama gave no answer within {OLLAMA_TIMEOUT_S} s "
                                 f"(model {settings.model}); check `ollama ps`, send a smaller prompt, "
                                 "or set BOOMERANG_LLM_PROVIDER=anthropic") from None
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise LLMUnavailable(f"model {settings.model} is not on this Ollama: "
                                     f"run 'ollama pull {settings.model}'") from None
            raise LLMError(f"{label}: Ollama error {e.status_code}: {e.error}") from None

        usage["in"] = (usage["in"] or 0) + (resp.prompt_eval_count or 0)
        usage["out"] = (usage["out"] or 0) + (resp.eval_count or 0)
        content = resp.message.content or ""
        try:
            return schema.model_validate_json(content), usage
        except ValidationError as e:
            if attempt == 2:
                raise LLMError(f"{label}: the answer failed validation twice: "
                               f"{_short(str(e), RETRY_ERROR_CHARS)}") from None
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That JSON failed validation: {_short(str(e), RETRY_ERROR_CHARS)}. "
                                            "Return only JSON matching the schema."},
            ]
    raise AssertionError("unreachable")  # pragma: no cover


def _call_anthropic[T: BaseModel](settings: Settings, system: str, user: str, schema: type[T],
                                  label: str, http_client: Any = None) -> tuple[T, dict[str, int | None]]:
    """Fallback provider. Pinned variant: ``beta.messages.parse`` with server-side refusal fallback.

    No ``temperature`` and no ``thinking`` argument: both return 400 on claude-opus-5-5 (thinking is
    always on there). The key is passed explicitly: config never copies `.env` into ``os.environ``.
    """
    if settings.anthropic_api_key is None:
        raise LLMUnavailable("BOOMERANG_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set "
                             "(in .env or the environment)")
    import anthropic

    kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key.get_secret_value()}
    if http_client is not None:
        kwargs["http_client"] = http_client
    client = anthropic.Anthropic(**kwargs)
    try:
        resp = client.beta.messages.parse(
            model=settings.model, max_tokens=ANTHROPIC_MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": user}], output_format=schema,
            betas=ANTHROPIC_BETAS, fallbacks="default",
        )
    except anthropic.APIConnectionError as e:          # includes APITimeoutError
        raise LLMUnavailable(f"Anthropic API not reachable ({type(e).__name__}); check the network "
                             "or switch back to BOOMERANG_LLM_PROVIDER=ollama") from None
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise LLMUnavailable("Anthropic rejected ANTHROPIC_API_KEY (401/403)") from None
    except anthropic.NotFoundError:
        raise LLMUnavailable(f"Anthropic model {settings.model} not found (BOOMERANG_MODEL)") from None
    except anthropic.APIStatusError as e:
        raise LLMError(f"{label}: Anthropic API error {e.status_code}: {_short(str(e.message), 300)}") from None
    except ValidationError as e:
        raise LLMError(f"{label}: the answer failed validation: {_short(str(e), RETRY_ERROR_CHARS)}") from None

    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        category = getattr(details, "category", None) or "unknown"
        raise LLMError(f"{label}: the model refused (category {category})")
    if resp.stop_reason == "max_tokens":
        raise LLMError(f"{label}: the answer hit max_tokens={ANTHROPIC_MAX_TOKENS}; send a smaller prompt")
    parsed = resp.parsed_output
    if parsed is None:
        raise LLMError(f"{label}: no structured output in the answer (stop_reason={resp.stop_reason})")
    return parsed, {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}


# ---------- events ----------

def _report(emit: Emitter | None, stage: Stage, label: str, settings: Settings, duration: float,
            usage: dict[str, int | None], *, cached: bool, key: str, **extra: Any) -> None:
    tokens = f"{usage.get('in') if usage.get('in') is not None else '?'}→" \
             f"{usage.get('out') if usage.get('out') is not None else '?'} tokens"
    message = f"{label}: {duration:.1f} s · {tokens} · cached={cached}"
    _notify(emit, stage, message, "info", label=label, duration_s=round(duration, 3),
            tokens_in=usage.get("in"), tokens_out=usage.get("out"), cached=cached,
            provider=settings.provider, model=settings.model, cache_key=key, **extra)


def _notify(emit: Emitter | None, stage: Stage, message: str, level: str, **data: Any) -> None:
    if emit is not None:
        emit.emit(stage, message, level, **data)  # type: ignore[arg-type]
    elif level != "info":
        log.warning(message)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
