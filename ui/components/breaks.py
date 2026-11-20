"""Tab: What breaks (owner: D, task D3)."""
from __future__ import annotations

from ui.data import Source
from ui.theme import empty_state


def render(src: Source) -> None:
    data = src.json("assessment")
    if data is None:
        empty_state(
            "Coming in D3",
            "Traffic-light table, lock-in gauge, counters and the Approve plan button.",
        )
        return
    # D3: replace this placeholder with the real view.
    empty_state("Data loaded", "assessment.json found; view lands in D3.")
