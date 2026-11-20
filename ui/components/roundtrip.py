"""Tab: Data & round trip (owner: D, task D6)."""
from __future__ import annotations

from ui.data import Source
from ui.theme import empty_state


def render(src: Source) -> None:
    data = src.json("data")
    if data is None:
        empty_state(
            "Coming in D6",
            "Counts per component, the AWS → Leaseweb → AWS strip and what got lost.",
        )
        return
    # D6: replace this placeholder with the real view.
    empty_state("Data loaded", "data.json found; view lands in D6.")
