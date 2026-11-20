"""Shared fixtures. Unit tests never need Docker, a model, or the developer's real `.env`."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from boomerang.config import get_settings

# tests/data/ holds small apps (mini_app) whose own tests need moto: never collect them here.
collect_ignore_glob = ["data/*"]

_ISOLATED_PREFIXES = ("BOOMERANG_",)
_ISOLATED_NAMES = {"OLLAMA_HOST", "ANTHROPIC_API_KEY"}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every test sees default settings, an empty `.env`, and private runs/cache folders."""
    for name in list(os.environ):
        if name.startswith(_ISOLATED_PREFIXES) or name in _ISOLATED_NAMES:
            monkeypatch.delenv(name, raising=False)
    home = tmp_path_factory.mktemp("boomerang-home")
    empty = home / "empty.env"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("BOOMERANG_DOTENV", str(empty))
    monkeypatch.setenv("BOOMERANG_RUNS_DIR", str(home / "runs"))
    monkeypatch.setenv("BOOMERANG_CACHE_DIR", str(home / "cache"))
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


@dataclass
class FakeLLM:
    """Canned answers by label prefix (longest prefix wins). A value may be a model or a callable(call) → model."""

    responses: dict[str, BaseModel | Callable[[dict[str, Any]], BaseModel]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *, system: str, user: str, schema: type[BaseModel], label: str,
                 emit=None, stage: str = "delock") -> BaseModel:
        call = {"system": system, "user": user, "schema": schema, "label": label, "stage": stage}
        self.calls.append(call)
        for prefix in sorted(self.responses, key=len, reverse=True):
            if label.startswith(prefix):
                answer = self.responses[prefix]
                result = answer(call) if callable(answer) and not isinstance(answer, BaseModel) else answer
                assert isinstance(result, schema), f"fake_llm: {label} expects {schema.__name__}, got {type(result)}"
                return result
        raise AssertionError(f"fake_llm: no canned response for label {label!r}")


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Replaces ``boomerang.llm.structured``. Code must call ``llm.structured(...)`` through the module."""
    fake = FakeLLM()
    monkeypatch.setattr("boomerang.llm.structured", fake)
    return fake
