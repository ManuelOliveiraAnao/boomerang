"""Settings from the environment and `.env` (docs/03 §3.1, docs/15 §2.3).

`.env` is read with ``dotenv_values()`` and **never copied into os.environ**: otherwise its secrets
(e.g. ``ANTHROPIC_API_KEY``) would reach every subprocess, including the AI-edited app under test.
Real environment variables win over `.env`.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODELS = {"ollama": "qwen2.5-coder:7b", "anthropic": "claude-opus-5-5"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class Settings(BaseModel):
    provider: Literal["ollama", "anthropic"] = "ollama"
    model: str = DEFAULT_MODELS["ollama"]
    ollama_host: str = "http://127.0.0.1:11434"
    llm_mode: Literal["live", "record", "replay"] = "record"
    cache_dir: Path
    runs_dir: Path
    max_fix_attempts: int = Field(default=2, ge=0)
    auto_approve: bool = False
    allow_fixtures: bool = False          # L9
    repo_root: Path = REPO_ROOT
    prompts_dir: Path = REPO_ROOT / "prompts"
    anthropic_api_key: SecretStr | None = None
    dotenv_path: Path | None = None       # the `.env` that was read; redact.secret_values() scans it and its `.env*` siblings

    @property
    def llm_badge(self) -> str:
        """What the UI and the report must show (S5): local model, or the cloud fallback, clearly."""
        if self.provider == "ollama":
            return f"ollama · {self.model} · local"
        return f"anthropic · {self.model} · cloud fallback"


def _bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean (use true/false)")


def _int(name: str, raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not an integer") from None


def _dir(raw: str | None, default: str, root: Path) -> Path:
    path = Path(raw.strip() if raw and raw.strip() else default)
    return path if path.is_absolute() else root / path


def load_settings(environ: dict[str, str] | None = None, dotenv_path: Path | None = None) -> Settings:
    """Build Settings from `.env` + the environment (environment wins). Pure: never touches os.environ."""
    environ = dict(os.environ if environ is None else environ)
    if dotenv_path is None:
        dotenv_path = Path(environ.get("BOOMERANG_DOTENV") or REPO_ROOT / ".env")
    file_values = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None} if dotenv_path.is_file() else {}
    values = {**file_values, **environ}

    def get(name: str) -> str | None:
        raw = values.get(name)
        return raw.strip() if isinstance(raw, str) else raw

    provider = (get("BOOMERANG_LLM_PROVIDER") or "ollama").lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"BOOMERANG_LLM_PROVIDER={provider!r}: use 'ollama' or 'anthropic'")
    model = get("BOOMERANG_MODEL") or DEFAULT_MODELS[provider]
    if provider == "anthropic" and not model.startswith("claude"):
        # .env.example sets BOOMERANG_MODEL to the Ollama model; switching only the provider must still work.
        warnings.warn(f"BOOMERANG_MODEL={model!r} is not a Claude model; using {DEFAULT_MODELS['anthropic']}",
                      stacklevel=2)
        model = DEFAULT_MODELS["anthropic"]

    ollama_host = get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if "://" not in ollama_host:
        ollama_host = f"http://{ollama_host}"
    host = urlparse(ollama_host).hostname or ""
    if provider == "ollama" and host not in LOCAL_HOSTS:
        warnings.warn(f"OLLAMA_HOST={ollama_host} is not on this machine: the model no longer runs locally (S5)",
                      stacklevel=2)

    mode = (get("BOOMERANG_LLM_MODE") or "record").lower()
    if mode not in {"live", "record", "replay"}:
        raise ValueError(f"BOOMERANG_LLM_MODE={mode!r}: use live, record or replay")

    max_fix = _int("BOOMERANG_MAX_FIX_ATTEMPTS", get("BOOMERANG_MAX_FIX_ATTEMPTS"), 2)
    if max_fix < 0:
        raise ValueError(f"BOOMERANG_MAX_FIX_ATTEMPTS={max_fix} must be >= 0")

    api_key = get("ANTHROPIC_API_KEY")
    return Settings(
        provider=provider,
        model=model,
        ollama_host=ollama_host,
        llm_mode=mode,
        cache_dir=_dir(get("BOOMERANG_CACHE_DIR"), ".cache/llm", REPO_ROOT),
        runs_dir=_dir(get("BOOMERANG_RUNS_DIR"), "runs", REPO_ROOT),
        max_fix_attempts=max_fix,
        auto_approve=_bool("BOOMERANG_AUTO_APPROVE", get("BOOMERANG_AUTO_APPROVE"), False),
        allow_fixtures=_bool("BOOMERANG_ALLOW_FIXTURES", get("BOOMERANG_ALLOW_FIXTURES"), False),
        anthropic_api_key=SecretStr(api_key) if api_key else None,
        dotenv_path=dotenv_path,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings. Tests call ``get_settings.cache_clear()`` after changing the environment."""
    return load_settings()
