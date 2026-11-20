"""LLM layer: redaction, cache, Ollama (default) and Anthropic (fallback). Owner C, task C2.

Spec: docs/03 §3.2, docs/15 §3.2. Stub until C2.

Call it as ``llm.structured(...)`` (module attribute), never ``from boomerang.llm import structured``:
the unit tests replace ``boomerang.llm.structured`` with a fake (tests/conftest.py ``fake_llm``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from boomerang._stub import NotBuilt

if TYPE_CHECKING:
    from boomerang.events import Emitter
    from boomerang.models import Stage


class LLMError(Exception):
    """The model answered, but not with something we can use (validation failed twice, refusal, max_tokens)."""


class LLMUnavailable(LLMError):
    """Ollama isn't running / no API key. The message says what to do. Stops the stage."""


class CacheMiss(LLMError):
    """``BOOMERANG_LLM_MODE=replay`` and no cached answer for this prompt. Stops the stage."""


def structured[T: BaseModel](*, system: str, user: str, schema: type[T], label: str,
               emit: Emitter | None = None, stage: Stage = "delock") -> T:
    """Binding (docs/03 §3.2) + two optional kwargs. Redacts, caches, calls the model, validates."""
    raise NotBuilt("llm.structured", owner="C", doc="docs/15 §3.2")


def stable_text(text: str, app_dir: str | None = None) -> str:
    """Make text run-independent before it goes into a prompt: absolute paths, uuids, addresses, durations."""
    raise NotBuilt("llm.stable_text", owner="C", doc="docs/15 §3.2")
