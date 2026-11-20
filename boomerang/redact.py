"""Secret redaction before every LLM call (docs/13 S1 with the L3 refinement). Owner C, task C2.

Spec: docs/15 §3.1. Rules, applied in this order (most specific first):

1. AWS access key ids (``AKIA…`` / ``ASIA…``).
2. A 40-char AWS secret right after ``aws_secret_access_key`` (any case).
3. Env-style lines: ``DB_PASSWORD=…``, ``export API_TOKEN=…``, ``- POSTGRES_PASSWORD=…`` (compose lists).
   The name must be UPPER_CASE, the value must end the line and hold a non-digit, so code such as
   ``API_TOKEN = get_token()``, ``MAX_TOKENS = 4096`` or ``password = password`` is never touched.
4. Quoted literals in code or config: ``password="…"``, ``"api_key": "…"``. Unquoted values are code
   (``password=password``, ``secret: bytes``) and stay: redacting them would make rules check 6 reject
   a function that holds no secret.
5. Credentials in URLs: ``postgresql://user:pass@host`` → ``postgresql://<REDACTED:userinfo>@host``.
6. Known values (L3): from ``.env*`` only values whose *key* looks secret; from every
   ``runs/*/leaseweb/secrets.env`` every value (it holds only secrets, L7); from any source only values
   of 8+ chars, so names like ``orders``, ``test`` or ``us-east-1`` are never redacted.

Placeholders look like ``<REDACTED:name>``. No rule matches text containing ``<``, so ``redact`` is
idempotent and placeholders are stable from run to run (the LLM cache key uses the redacted text).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

from boomerang.config import get_settings

PLACEHOLDER = "<REDACTED:{}>"
MIN_KNOWN_LEN = 8                                   # L3: shorter known values are names, not secrets
SECRET_KEY_RE = re.compile(r"KEY|SECRET|TOKEN|PASS|CREDENTIAL", re.IGNORECASE)   # L3: which .env keys count
_EXISTING = re.compile(r"<REDACTED:[^<>\s]*>")

Replacement = tuple[str, str]                       # (placeholder, original)
Repl = Callable[[re.Match[str]], tuple[str, str, str]]   # match → (text before the secret, name, secret)


def _keep_prefix(group: int, name: str | int, secret: int) -> Repl:
    def repl(m: re.Match[str]) -> tuple[str, str, str]:
        label = m.group(name) if isinstance(name, int) else name
        return m.group(group) if group else "", label, m.group(secret)
    return repl


# (pattern, how to split a match into kept prefix / placeholder name / secret)
_RULES: list[tuple[re.Pattern[str], Repl]] = [
    # 1. AWS access key id
    (re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"),
     _keep_prefix(0, "aws_access_key", 1)),
    # 2. AWS secret after its key (env files write AWS_SECRET_ACCESS_KEY, code writes aws_secret_access_key)
    (re.compile(r"(?i)(aws_secret_access_key[\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"),
     _keep_prefix(1, "aws_secret", 2)),
    # 3. env-style line: UPPER_CASE name, `=`, a value that ends the line (optionally quoted, optional comment)
    (re.compile(r"(?m)^([ \t]*(?:-[ \t]*)?(?:export[ \t]+)?"
                r"([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)"
                r"[ \t]*=[ \t]*[\"']?)"
                # a value with at least one non-digit: `MAX_TOKENS = 4096` is a constant, not a secret
                r"((?=[^\s\"'<>()\[\]{},;]*[^\s\"'<>()\[\]{},;\d.])[^\s\"'<>()\[\]{},;]{4,})"
                r"(?=[\"']?[ \t]*(?:#[^\r\n]*)?\r?$)"),
     _keep_prefix(1, 2, 3)),
    # 4. quoted literal after a secret-looking name, in code (`=`) or config/JSON/YAML (`:`)
    (re.compile(r"(?i)(\b(\w*(?:password|passwd|secret|token|api_key|private_key)\w*)[\"']?\s*[=:]\s*([\"']))"
                r"([^\"'<>\s]{4,})(?=\3)"),
     _keep_prefix(1, 2, 4)),
    # 5. credentials inside a URL; a templated password (`${DB_PASSWORD}`) is not a secret
    (re.compile(r"(\b[A-Za-z][\w+.-]*://)([^/\s:@<>${}]+:(?![$<{])[^/\s@<>]+)(?=@)"),
     _keep_prefix(1, "userinfo", 2)),
]


# ---------- known values ----------

def _env_files(dotenv_path: Path | None) -> list[Path]:
    """The configured `.env` and its `.env*` siblings (`.env.local`, `.env.production`, ...)."""
    if dotenv_path is None:
        return []
    files = {dotenv_path} if dotenv_path.is_file() else set()
    if dotenv_path.parent.is_dir():
        files |= {p for p in dotenv_path.parent.glob(".env*") if p.is_file()}
    return sorted(files)


def _read_env(path: Path) -> dict[str, str]:
    try:
        return {k: v for k, v in dotenv_values(path, encoding="utf-8").items() if k and v}
    except (OSError, UnicodeDecodeError):
        return {}


@lru_cache(maxsize=8)
def _known(dotenv_path: Path | None, runs_dir: Path) -> tuple[tuple[str, str], ...]:
    """``((name, value), ...)`` with unique values, longest first."""
    found: list[tuple[str, str]] = []
    for path in _env_files(dotenv_path):
        found += [(k, v) for k, v in _read_env(path).items() if SECRET_KEY_RE.search(k)]
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*/leaseweb/secrets.env")):
            found += list(_read_env(path).items())
    by_value: dict[str, str] = {}
    for name, value in found:
        value = value.strip()
        if len(value) >= MIN_KNOWN_LEN and value not in by_value:
            by_value[value] = name
    return tuple(sorted(((n, v) for v, n in by_value.items()), key=lambda nv: (-len(nv[1]), nv[0])))


def secret_values() -> dict[str, str]:
    """Name → value, from `.env*` and every `runs/*/leaseweb/secrets.env` (L3 rules).

    Cached per (`.env` path, runs dir); call ``secret_values.cache_clear()`` after writing a new
    ``secrets.env`` (the pipeline does, right after generating). Two files may use the same name for
    different values: the second one becomes ``NAME_2`` (and so on).
    """
    s = get_settings()
    out: dict[str, str] = {}
    for name, value in _known(s.dotenv_path, s.runs_dir):
        key, n = name, 1
        while key in out:
            n += 1
            key = f"{name}_{n}"
        out[key] = value
    return out


secret_values.cache_clear = _known.cache_clear  # type: ignore[attr-defined]


def _known_pattern(values: dict[str, str]) -> re.Pattern[str] | None:
    if not values:
        return None
    alternatives = "|".join(re.escape(v) for v in sorted(values.values(), key=len, reverse=True))
    # Existing placeholders are matched first and kept, so a value that happens to occur inside a
    # placeholder never breaks idempotency.
    return re.compile(rf"(?P<keep><REDACTED:[^<>\s]*>)|(?P<secret>{alternatives})")


# ---------- the engine ----------

def _apply(text: str, record: list[Replacement]) -> str:
    for pattern, split in _RULES:
        def sub(m: re.Match[str], split: Repl = split) -> str:
            prefix, name, secret = split(m)
            placeholder = PLACEHOLDER.format(name)
            record.append((placeholder, secret))
            return prefix + placeholder
        text = pattern.sub(sub, text)

    values = secret_values()
    pattern = _known_pattern(values)
    if pattern is not None:
        name_of = {v: k for k, v in values.items()}

        def sub_known(m: re.Match[str]) -> str:
            if m.group("keep"):
                return m.group("keep")
            placeholder = PLACEHOLDER.format(name_of[m.group("secret")])
            record.append((placeholder, m.group("secret")))
            return placeholder
        text = pattern.sub(sub_known, text)
    return text


def redact(text: str) -> str:
    """Binding use: called inside ``llm.structured()`` on system and user text."""
    return _apply(text, [])


def redact_with_map(text: str) -> tuple[str, dict[str, str]]:
    """Redacted text + placeholder → original. **Never log the map.**

    Two different secrets can get the same placeholder (two ``password="…"`` literals); the map keeps
    the first. Nothing restores secrets automatically (rules check 6 sends such functions to a human).
    """
    record: list[Replacement] = []
    redacted = _apply(text, record)
    mapping: dict[str, str] = {}
    for placeholder, original in record:
        mapping.setdefault(placeholder, original)
    return redacted, mapping


def find_secrets(text: str) -> list[str]:
    """Detector only (C7 scans the cache and the golden run before committing).

    Returns the **names** of what ``redact`` would replace (``aws_access_key``, ``DB_PASSWORD``,
    ``userinfo``...), unique and in order, never the secret itself, so the result is safe to print.
    Empty list = nothing to redact.
    """
    record: list[Replacement] = []
    _apply(text, record)
    return list(dict.fromkeys(placeholder[len("<REDACTED:"):-1] for placeholder, _ in record))


def placeholders(text: str) -> Iterator[str]:
    """Placeholders already in ``text`` (rules check 6 rejects functions that contain one)."""
    return (m.group(0) for m in _EXISTING.finditer(text))
