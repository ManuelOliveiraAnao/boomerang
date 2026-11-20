"""Tab: Code (owner: D, task D5)."""
from __future__ import annotations

from ui.data import Source
from ui.theme import empty_state


def render(src: Source) -> None:
    data = src.json("delock")
    if data is None:
        empty_state(
            "Coming in D5",
            "Per-function diffs labelled AI or config change, unsupported items and fixer attempts.",
        )
        return
    # D5: replace this placeholder with the real view.
    empty_state("Data loaded", "delock.json found; view lands in D5.")
