"""Tab: Leaseweb setup (owner: D, task D5)."""
from __future__ import annotations

from ui.data import Source
from ui.theme import empty_state


def render(src: Source) -> None:
    data = src.json("verification")
    if data is None:
        empty_state(
            "Coming in D5",
            "Generated Leaseweb files, the live verification checklist and a zip download.",
        )
        return
    # D5: replace this placeholder with the real view.
    empty_state("Data loaded", "verification.json found; view lands in D5.")
