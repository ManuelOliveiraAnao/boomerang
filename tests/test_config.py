from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from boomerang.config import REPO_ROOT, get_settings, load_settings


def _load(tmp_path: Path, env: dict[str, str] | None = None, dotenv: str = ""):
    path = tmp_path / "test.env"
    path.write_text(dotenv, encoding="utf-8")
    return load_settings(environ=env or {}, dotenv_path=path)


def test_defaults(tmp_path):
    s = _load(tmp_path)
    assert (s.provider, s.model, s.llm_mode) == ("ollama", "qwen2.5-coder:7b", "record")
    assert s.ollama_host == "http://127.0.0.1:11434"
    assert s.max_fix_attempts == 2 and not s.auto_approve and not s.allow_fixtures
    assert s.cache_dir == REPO_ROOT / ".cache" / "llm" and s.runs_dir == REPO_ROOT / "runs"
    assert s.prompts_dir == REPO_ROOT / "prompts" and s.anthropic_api_key is None
    assert s.llm_badge == "ollama · qwen2.5-coder:7b · local"


def test_env_overrides(tmp_path):
    s = _load(tmp_path, {
        "BOOMERANG_LLM_MODE": "Replay", "BOOMERANG_MAX_FIX_ATTEMPTS": "1", "BOOMERANG_AUTO_APPROVE": "true",
        "BOOMERANG_ALLOW_FIXTURES": "1", "BOOMERANG_RUNS_DIR": "out/runs", "BOOMERANG_CACHE_DIR": str(tmp_path / "c"),
    })
    assert s.llm_mode == "replay" and s.max_fix_attempts == 1 and s.auto_approve and s.allow_fixtures
    assert s.runs_dir == REPO_ROOT / "out" / "runs"          # relative → against the repo, not the cwd
    assert s.cache_dir == tmp_path / "c"                     # absolute kept


def test_anthropic_defaults_and_wrong_model(tmp_path):
    s = _load(tmp_path, {"BOOMERANG_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-test-123456789"})
    assert s.model == "claude-opus-5-5"
    assert s.llm_badge == "anthropic · claude-opus-5-5 · cloud fallback"
    assert "sk-test" not in repr(s) and s.anthropic_api_key.get_secret_value() == "sk-test-123456789"
    with pytest.warns(UserWarning, match="not a Claude model"):
        s = _load(tmp_path, {"BOOMERANG_LLM_PROVIDER": "anthropic", "BOOMERANG_MODEL": "qwen2.5-coder:7b"})
    assert s.model == "claude-opus-5-5"


@pytest.mark.parametrize("env, match", [
    ({"BOOMERANG_LLM_MODE": "offline"}, "BOOMERANG_LLM_MODE"),
    ({"BOOMERANG_MAX_FIX_ATTEMPTS": "-1"}, "must be >= 0"),
    ({"BOOMERANG_MAX_FIX_ATTEMPTS": "two"}, "not an integer"),
    ({"BOOMERANG_AUTO_APPROVE": "maybe"}, "not a boolean"),
    ({"BOOMERANG_LLM_PROVIDER": "openai"}, "BOOMERANG_LLM_PROVIDER"),
])
def test_invalid_values_fail_clearly(tmp_path, env, match):
    with pytest.raises(ValueError, match=match):
        _load(tmp_path, env)


def test_remote_ollama_host_warns(tmp_path):
    with pytest.warns(UserWarning, match="not on this machine"):
        _load(tmp_path, {"OLLAMA_HOST": "http://192.168.1.20:11434"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _load(tmp_path, {"OLLAMA_HOST": "localhost:11434"}).ollama_host == "http://localhost:11434"


def test_leaseweb_location(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")                       # the badge already says where the model runs
        s = _load(tmp_path, {"BOOMERANG_LLM_LOCATION": "Leaseweb", "BOOMERANG_MODEL": "qwen2.5-coder:32b",
                             "OLLAMA_HOST": "http://127.0.0.1:11435"})
    assert s.llm_location == "leaseweb"
    assert s.llm_badge == "ollama · qwen2.5-coder:32b · Leaseweb (EU)"
    with pytest.raises(ValueError, match="BOOMERANG_LLM_LOCATION"):
        _load(tmp_path, {"BOOMERANG_LLM_LOCATION": "aws"})


def test_env_example_parses_with_inline_comments():
    s = load_settings(environ={}, dotenv_path=REPO_ROOT / ".env.example")
    assert (s.provider, s.model, s.llm_mode, s.max_fix_attempts) == ("ollama", "qwen2.5-coder:7b", "record", 2)
    assert s.anthropic_api_key is None and not s.auto_approve


def test_env_wins_over_dotenv_and_dotenv_never_reaches_os_environ(tmp_path):
    s = _load(tmp_path, {"BOOMERANG_LLM_MODE": "live"},
              dotenv="BOOMERANG_LLM_MODE=replay\nBOOMERANG_TEST_ONLY_SECRET=abcdefgh123\n")
    assert s.llm_mode == "live"
    assert "BOOMERANG_TEST_ONLY_SECRET" not in os.environ


def test_get_settings_is_cached_until_cleared(monkeypatch):
    first = get_settings()
    monkeypatch.setenv("BOOMERANG_LLM_MODE", "live")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().llm_mode == "live"
