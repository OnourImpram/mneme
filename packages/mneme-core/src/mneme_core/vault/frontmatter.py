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

Malformed timestamp handling (bi-temporal degradation, ADR-010):
When a ``created`` or ``modified`` value cannot be parsed as ISO 8601,
the parser degrades gracefully: it logs a structured warning naming the
offending file and field, then falls back to the file's mtime so that
one hand-typed bad date does not abort a whole-vault walk.  Missing
*structural* fields (``id``, ``type``, ``created`` key presence) still
raise immediately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)


class _StringDateLoader(yaml.SafeLoader):
    """SafeLoader that does not auto-construct YAML timestamps.

    Frontmatter dates are validated by :func:`_parse_dt`, which degrades a
    malformed value to the file mtime. If PyYAML resolved timestamps itself,
    an out-of-range value such as ``2026-13-99`` would raise inside the YAML
    timestamp constructor (before reaching ``_parse_dt``) and abort a whole-
    vault walk. Dropping the implicit timestamp resolver makes every date-like
    scalar load as a plain string so it flows through ``_parse_dt`` uniformly.
    """


_StringDateLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml_block(yaml_text: str) -> Any:
    """Load a frontmatter YAML block with mneme's date-safe loader.

    This is the single canonical way to load frontmatter YAML across the
    codebase. It uses :class:`_StringDateLoader`, so every date-like scalar
    loads as a plain string instead of an auto-constructed ``datetime``: an
    out-of-range value such as ``2026-13-99`` loads as a string for downstream
    validation (see :func:`_parse_dt`) rather than raising inside PyYAML's
    timestamp constructor and aborting a whole-vault walk. Returns the parsed
    object (typically a ``dict``) or ``None`` for an empty document; callers
    guard with ``or {}``. Raises ``yaml.YAMLError`` on genuinely invalid YAML.
    """
    return yaml.load(yaml_text, Loader=_StringDateLoader)  # noqa: S506


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
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        file_path: Path | None = None,
        mtime: float | None = None,
    ) -> Frontmatter:
        """Parse a frontmatter dict into a ``Frontmatter`` instance.

        Args:
            data: raw YAML key/value mapping.
            file_path: optional path to the source file, used only in
                warning messages when a timestamp field is malformed.
            mtime: optional file modification time (``stat().st_mtime``
                epoch seconds).  When a ``created`` or ``modified`` value
                cannot be parsed, this is used as the fallback datetime
                so one bad note does not abort a whole-vault walk.
        """
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

        created = _parse_dt(data["created"])
        if created is None:
            _log.warning(
                "Malformed timestamp in frontmatter field 'created': "
                "value=%r file=%s — falling back to mtime",
                data["created"],
                file_path,
            )
            created = _mtime_to_dt(mtime)

        modified: datetime | None = None
        if data.get("modified"):
            modified = _parse_dt(data["modified"])
            if modified is None:
                _log.warning(
                    "Malformed timestamp in frontmatter field 'modified': "
                    "value=%r file=%s — falling back to mtime",
                    data["modified"],
                    file_path,
                )
                modified = _mtime_to_dt(mtime)

        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            created=created,
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            modified=modified,
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


def parse(
    text: str,
    *,
    file_path: Path | None = None,
    mtime: float | None = None,
) -> tuple[Frontmatter | None, str]:
    """Parse a markdown document into ``(frontmatter, body)``.

    Returns ``(None, text)`` if the document has no frontmatter block.
    Raises ``ValueError`` if the frontmatter block exists but is missing
    required *structural* fields (``id``, ``type``, ``created`` key) or
    fails YAML parsing.

    Malformed timestamp values (``created``, ``modified``) are handled
    gracefully: a structured warning is emitted and the file's mtime is
    substituted so that one bad note does not abort a whole-vault walk.

    Args:
        text: full markdown document text including the frontmatter block.
        file_path: optional path to the source file, forwarded to
            ``Frontmatter.from_dict`` for use in warning messages.
        mtime: optional file modification time (``stat().st_mtime``
            epoch seconds), used as the fallback when a timestamp field
            cannot be parsed.
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
    data = load_yaml_block(yaml_block) or {}
    fm = Frontmatter.from_dict(data, file_path=file_path, mtime=mtime)
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


def _parse_dt(value: Any) -> datetime | None:
    """Attempt to parse *value* as an ISO 8601 datetime.

    Returns the parsed ``datetime`` on success, or ``None`` when the value
    is malformed.  The caller is responsible for emitting a warning and
    substituting a fallback (e.g. the file's mtime) when ``None`` is
    returned.  This function is intentionally path-agnostic; it never
    logs because it has no file context.
    """
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _mtime_to_dt(mtime: float | None) -> datetime:
    """Convert a ``stat().st_mtime`` epoch float to a UTC ``datetime``.

    When *mtime* is ``None`` (caller did not provide it), the Unix epoch
    is returned as a safe sentinel so the fallback path always produces a
    valid ``datetime`` rather than raising.
    """
    if mtime is None:
        return datetime.fromtimestamp(0, tz=UTC)
    return datetime.fromtimestamp(mtime, tz=UTC)
