"""Tab: Costs (owner: D, task D4)."""
from __future__ import annotations

from ui.data import Source
from ui.theme import empty_state


def render(src: Source) -> None:
    data = src.json("costs")
    if data is None:
        empty_state(
            "Coming in D4",
            "AWS vs Leaseweb monthly costs from published prices, ops hours and break-even.",
        )
        return
    # D4: replace this placeholder with the real view.
    empty_state("Data loaded", "costs.json found; view lands in D4.")
