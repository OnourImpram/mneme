"""Reusable action-pattern memory.

A pattern is a small markdown document the operator captures when a
particular approach worked. The pattern records the **signal** that
made the approach applicable, the **action** the operator took, and
the **outcome** that confirmed the approach. Future sessions can
search the vault for relevant patterns instead of rediscovering the
same approach from scratch.

Patterns live as vault-native markdown under ``vault/patterns/``.
Each pattern is one file with YAML frontmatter and three sections:
signal, action, outcome. The FTS5 indexer picks them up like any
other vault document, so retrieval works the day after they are
written without a separate index.

Public API:

* ``Pattern`` dataclass plus markdown <-> dataclass converters.
* ``store_pattern`` writes one pattern to the vault and returns the
  file path. Idempotent under the same name.
* ``load_pattern`` reads one pattern by name.
* ``list_patterns`` enumerates every pattern in the directory.
* ``delete_pattern`` removes one pattern.
* ``search_patterns`` is a thin in-memory substring search; the
  Phase H+ FTS5-backed search uses the same input shape so callers
  can swap implementations transparently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .vault.atomic_write import atomic_write_text

PATTERN_TYPE = "pattern"
PATTERN_SCHEMA_VERSION = "1.0.0"


@dataclass
class Pattern:
    """One reusable action pattern recorded by the operator."""

    name: str
    signal: str
    action: str
    outcome: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    schema_version: str = PATTERN_SCHEMA_VERSION


def _slug(name: str) -> str:
    """Stable filename slug. Disallows traversal and unsafe characters."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    s = s.strip("-._") or "unnamed"
    return s.lower()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _pattern_path(vault: Any, name: str) -> Path:
    return Path(vault.patterns_dir / f"{_slug(name)}.md")


def to_markdown(pattern: Pattern) -> str:
    """Serialize a Pattern to the canonical markdown layout."""
    fm: dict[str, Any] = {
        "type": PATTERN_TYPE,
        "name": pattern.name,
        "tags": list(pattern.tags),
        "created_at": pattern.created_at or _now_iso(),
        "schema_version": pattern.schema_version,
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = (
        f"## Signal\n\n{pattern.signal.strip()}\n\n"
        f"## Action\n\n{pattern.action.strip()}\n\n"
        f"## Outcome\n\n{pattern.outcome.strip()}\n"
        if pattern.outcome.strip()
        else (
            f"## Signal\n\n{pattern.signal.strip()}\n\n"
            f"## Action\n\n{pattern.action.strip()}\n"
        )
    )
    return f"---\n{fm_text}\n---\n\n{body}"


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def from_markdown(text: str) -> Pattern | None:
    """Parse markdown back to a Pattern. Returns None on malformed input."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict) or fm.get("type") != PATTERN_TYPE:
        return None
    body = match.group(2)
    sections = _parse_sections(body)
    return Pattern(
        name=str(fm.get("name") or ""),
        signal=sections.get("signal", "").strip(),
        action=sections.get("action", "").strip(),
        outcome=sections.get("outcome", "").strip(),
        tags=list(fm.get("tags") or []),
        created_at=str(fm.get("created_at") or ""),
        schema_version=str(fm.get("schema_version") or PATTERN_SCHEMA_VERSION),
    )


_SECTION_RE = re.compile(r"^##\s+(.*?)\s*$", re.MULTILINE)


def _parse_sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return out
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[heading] = body[start:end].strip()
    return out


def store_pattern(vault: Any, pattern: Pattern) -> Path:
    """Write the pattern to the vault atomically. Returns the file path."""
    vault.patterns_dir.mkdir(parents=True, exist_ok=True)
    if not pattern.created_at:
        pattern.created_at = _now_iso()
    path = _pattern_path(vault, pattern.name)
    atomic_write_text(path, to_markdown(pattern))
    return path


def load_pattern(vault: Any, name: str) -> Pattern | None:
    path = _pattern_path(vault, name)
    if not path.is_file():
        return None
    try:
        return from_markdown(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def list_patterns(vault: Any) -> list[Pattern]:
    if not vault.patterns_dir.is_dir():
        return []
    out: list[Pattern] = []
    for md_path in sorted(vault.patterns_dir.glob("*.md")):
        try:
            pattern = from_markdown(md_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if pattern is not None:
            out.append(pattern)
    return out


def delete_pattern(vault: Any, name: str) -> bool:
    path = _pattern_path(vault, name)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


@dataclass
class PatternHit:
    """One match returned by ``search_patterns``."""

    pattern: Pattern
    score: float


def search_patterns(
    vault: Any, query: str, *, top_k: int = 10
) -> list[PatternHit]:
    """Substring + tag search. Returns up to ``top_k`` hits by score.

    The score is the count of distinct query tokens that match
    anywhere in name + signal + action + outcome + tags. Ties broken
    by ``created_at`` desc.

    Production deployments swap this for an FTS5-backed implementation
    once Phase H benchmarks confirm the FTS5 index covers the
    ``patterns/`` directory.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    tokens = [t for t in re.split(r"\s+", q) if len(t) >= 2]
    if not tokens:
        return []
    patterns = list_patterns(vault)
    scored: list[PatternHit] = []
    for p in patterns:
        haystack = " ".join(
            [
                p.name.lower(),
                p.signal.lower(),
                p.action.lower(),
                p.outcome.lower(),
                " ".join(p.tags).lower(),
            ]
        )
        matched = sum(1 for t in tokens if t in haystack)
        if matched == 0:
            continue
        scored.append(PatternHit(pattern=p, score=matched))
    scored.sort(
        key=lambda h: (-h.score, -_parse_ts(h.pattern.created_at)),
    )
    return scored[:top_k]


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
