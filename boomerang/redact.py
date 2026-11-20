"""Secret redaction before every LLM call (docs/13 S1 with the L3 refinement). Owner C, task C2.

Spec: docs/15 §3.1. Stub until C2.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt


def redact(text: str) -> str:
    """Binding use: called inside ``llm.structured()`` on system and user text."""
    raise NotBuilt("redact.redact", owner="C", doc="docs/15 §3.1")


def redact_with_map(text: str) -> tuple[str, dict[str, str]]:
    """Redacted text + placeholder → original (never logged)."""
    raise NotBuilt("redact.redact_with_map", owner="C", doc="docs/15 §3.1")


def secret_values() -> dict[str, str]:
    """Name → value, from `.env*` and every `runs/*/leaseweb/secrets.env` (L3 rules). Cached; ``cache_clear()``."""
    raise NotBuilt("redact.secret_values", owner="C", doc="docs/15 §3.1")


def find_secrets(text: str) -> list[str]:
    """Detector only (C7 scans the cache and the golden run before committing)."""
    raise NotBuilt("redact.find_secrets", owner="C", doc="docs/15 §3.1")
