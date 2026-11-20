"""Cost model: published prices, usage, ops hours, egress rules. Owner D, task D4.

Spec: docs/12, docs/03 §3.13. Stub created by C1: D replaces the body and defines ``CostReport``
here (it is not part of models.py).
"""

from __future__ import annotations

from typing import Any

from boomerang._stub import NotBuilt


def estimate(prices: dict[str, Any], usage: dict[str, Any], ops: dict[str, Any]) -> Any:
    """Binding (docs/03 §3.13): pure function → ``CostReport``. Inputs are the parsed
    pricing/prices.yaml, the usage YAML and pricing/ops.yaml."""
    raise NotBuilt("cost.estimate", owner="D", doc="docs/12 §4")
