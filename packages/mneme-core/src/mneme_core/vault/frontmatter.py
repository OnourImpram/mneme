"""YAML frontmatter parser and serializer with schema validation.

Every vault markdown file begins with a YAML frontmatter block delimited
by ``---`` lines. This module enforces the minimum required schema and
preserves any extra fields the user adds.

Required fields:

- ``id`` (string): a unique identifier within the vault.
- ``type`` (string): one of ``session``, ``topic``, ``reference``,
  or any custom string.
- ``created`` (ISO 8601 datetime): when the record was first written.
- ``schema_version`` (integer): defaults to ``SCHEMA_VERSION``.

Optional fields: ``modified``, ``tags``, ``session_id``, ``source``.
Any other key the user supplies is preserved verbatim in the ``extra``
dict and round-trips through ``serialize``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

KNOWN_TYPES: frozenset[str] = frozenset(
    {
        "session",
        "topic",
        "reference",
        "pattern",
        "trajectory",
        "compressed",
        "observation",
        "session_summary",
        "user_prompt",
    }
)


def is_known_type(type_value: str) -> bool:
    """Return True when ``type_value`` is one of the nine canonical types.

    Phase J Day 6 audit established this helper to defend against the
    Day 1 P1 regression class (migration tool emitted ``type: session``
    for observations). Callers that want soft validation can warn or
    log when this returns False without raising. The parser itself
    never refuses unknown types so vendored content and forward-
    compatible schemas keep working.
    """
    return type_value in KNOWN_TYPES

FRONTMATTER_DELIM = "---"
SCHEMA_VERSION = 1


@dataclass
class Frontmatter:
    """Validated frontmatter record."""

    id: str
    type: str
    created: datetime
    schema_version: int = SCHEMA_VERSION
    modified: datetime | None = None
    tags: list[str] = field(default_factory=list)
    session_id: str | None = None
    source: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Frontmatter":
        required = {"id", "type", "created"}
        missing = required - data.keys()
        if missing:
            raise ValueError(
                f"Missing required frontmatter fields: {sorted(missing)}"
            )
        known = {
            "id",
            "type",
            "created",
            "schema_version",
            "modified",
            "tags",
            "session_id",
            "source",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            created=_parse_dt(data["created"]),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            modified=_parse_dt(data["modified"]) if data.get("modified") else None,
            tags=list(data.get("tags") or []),
            session_id=data.get("session_id"),
            source=data.get("source"),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "created": self.created.isoformat(),
            "schema_version": self.schema_version,
        }
        if self.modified is not None:
            out["modified"] = self.modified.isoformat()
        if self.tags:
            out["tags"] = list(self.tags)
        if self.session_id is not None:
            out["session_id"] = self.session_id
        if self.source is not None:
            out["source"] = self.source
        out.update(self.extra)
        return out


def parse(text: str) -> tuple[Frontmatter | None, str]:
    """Parse a markdown document into ``(frontmatter, body)``.

    Returns ``(None, text)`` if the document has no frontmatter block.
    Raises ``ValueError`` if the frontmatter block exists but is missing
    required fields or fails YAML parsing.
    """
    if not text.startswith(FRONTMATTER_DELIM):
        return None, text
    lines = text.split("\n")
    if len(lines) < 2 or lines[0].strip() != FRONTMATTER_DELIM:
        return None, text
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            closing_idx = i
            break
    if closing_idx == -1:
        return None, text
    yaml_block = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    data = yaml.safe_load(yaml_block) or {}
    fm = Frontmatter.from_dict(data)
    return fm, body


def serialize(fm: Frontmatter, body: str) -> str:
    """Combine a Frontmatter and a body string into a single markdown document."""
    yaml_block = yaml.safe_dump(
        fm.to_dict(),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"{FRONTMATTER_DELIM}\n{yaml_block}\n{FRONTMATTER_DELIM}\n{body}"


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
