"""Marker for code that isn't written yet (docs/15 L9)."""


class NotBuilt(NotImplementedError):
    """Raised **only** by stub bodies created in C1.

    The fixture fallback (``BOOMERANG_ALLOW_FIXTURES=1``) catches this class and nothing else, so a real
    "not supported" ``NotImplementedError`` inside a finished module is never hidden behind a fixture.
    """

    def __init__(self, what: str, owner: str, doc: str = "") -> None:
        self.what, self.owner, self.doc = what, owner, doc
        where = f" (spec: {doc})" if doc else ""
        super().__init__(f"{what} is not built yet; owner {owner}{where}")
