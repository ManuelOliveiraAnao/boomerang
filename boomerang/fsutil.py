"""Windows-safe file helpers used across the engine (docs/15 §5.2).

- ``atomic_write_text``: tmp file + ``os.replace``, retried, because on Windows a reader holding the
  target open (the UI polling ``run.json``) makes the replace fail with ``PermissionError``.
- ``write_text_lf``: UTF-8 with LF line endings. Plain ``write_text`` writes CRLF on Windows, and a
  CRLF ``.sh`` file fails inside a Linux container.
- ``hash_tree``: sha256 per file, skipping caches that pytest/Python create next to the sources.
- ``copy_ignore``: one ignore list for every copy of an app.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

CACHE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
CACHE_SUFFIXES = {".pyc", ".pyo"}
COPY_IGNORE = ("__pycache__", ".pytest_cache", ".venv", "venv", "*.pyc", ".git", ".mypy_cache", ".ruff_cache")

REPLACE_RETRIES = 8
REPLACE_DELAY_S = 0.05


def _replace_with_retry(src: str, dst: Path) -> None:
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_DELAY_S * (attempt + 1))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _replace_with_retry(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8, LF, atomic."""
    atomic_write_bytes(path, _lf(text).encode("utf-8"))


def write_text_lf(path: Path, text: str) -> None:
    """UTF-8 with LF line endings, whatever the platform (generated deploy files)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_lf(text).encode("utf-8"))


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _is_cache(rel: Path) -> bool:
    return bool(CACHE_PARTS.intersection(rel.parts)) or rel.suffix in CACHE_SUFFIXES


def hash_tree(root: Path) -> dict[str, str]:
    """``{relative posix path: sha256}`` for every file under ``root``, caches excluded."""
    root = Path(root)
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if _is_cache(rel):
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def copy_ignore(extra: Iterable[str] = ()) -> Callable[[str, list[str]], set[str]]:
    """``shutil.copytree(ignore=...)`` callback: caches, venvs, git, and ``.env*`` except ``.env.example``."""
    patterns = (*COPY_IGNORE, *extra)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if any(fnmatch.fnmatch(n, p) for p in patterns)}
        skipped |= {n for n in names if n.startswith(".env") and n != ".env.example"}
        return skipped

    return ignore
