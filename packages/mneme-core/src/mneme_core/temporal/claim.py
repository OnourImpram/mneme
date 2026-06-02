"""Temporal claim dataclass and factory.

A ``Claim`` is a single temporal fact extracted verbatim from vault markdown
frontmatter.  Every claim is EXTRACTED (verbatim) at index time.  INFERRED is
reserved for a future producer; AMBIGUOUS is a query-time overlay applied only
when a claim participates in a contradiction set (same ``claim_key``, overlapping
validity window).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..privacy import redact
from ..vault.frontmatter import _parse_dt


class ConfidenceLabel(str, Enum):  # noqa: UP042
    """Confidence label for a surfaced claim.

    * ``EXTRACTED`` — verbatim from user frontmatter (all index-time claims).
    * ``INFERRED``  — derived by reasoning (reserved; no producer yet).
    * ``AMBIGUOUS`` — query-time overlay for claims in a contradiction set.

    Inherits ``str`` so instances serialise directly as JSON strings and
    compare equal to their string values without an extra ``.value`` call.
    The ``UP042`` noqa suppresses the Ruff suggestion to use
    ``StrEnum`` (Python 3.11+) which would break the ``str`` + ``Enum``
    pattern already used elsewhere in the codebase.
    """

    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Claim:
    """A single temporal claim row surfaced from the vault index.

    All string fields that carry user-provided text have already been passed
    through :func:`mneme_core.privacy.redact` before construction.

    Attributes
    ----------
    claim_id:
        Stable UUID-based identifier derived from ``path`` and ``statement``
        at factory time.  Collision-free within a vault.
    path:
        Vault-relative path to the source markdown file (forward slashes).
    statement:
        Redacted claim text (frontmatter ``statement`` field or note title).
    statement_normalized:
        Caller-supplied normalized form of ``statement`` (used for LIKE
        matching in the temporal backend).
    valid_from:
        Inclusive start of the claim's validity window; ``None`` = open left.
    valid_to:
        Exclusive end of the claim's validity window; ``None`` = open right.
    observed_at:
        When the claim was first recorded (defaults to frontmatter ``created``).
    supersedes:
        ``claim_id`` of the claim this one supersedes, or ``None``.
    claim_key:
        Semantic deduplication key (e.g. ``"user.location"``). Claims sharing
        the same non-null ``claim_key`` with overlapping windows are
        contradictions.
    confidence_label:
        ``EXTRACTED`` at index time; ``AMBIGUOUS`` overlaid by query layer for
        contradiction-set members.
    trust:
        Provenance trust level (``"user"`` for vault-authored notes).
    content_hash:
        SHA-256 hex digest of the source file content (after redaction).
    """

    claim_id: str
    """The note's frontmatter ``id`` (fallback: deterministic uuid5 of path+statement).

    Using the frontmatter id as the primary key means ``supersedes: <other-note-id>``
    references in frontmatter resolve correctly during the supersession pass and
    dynamic shadow computation in :func:`~mneme_core.temporal.query.as_of`.
    """
    path: str
    statement: str
    statement_normalized: str
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime
    supersedes: str | None
    claim_key: str | None
    confidence_label: ConfidenceLabel
    trust: str
    content_hash: str


def _extract_title(body: str, fm: dict[str, Any]) -> str:
    """Return the note title from body headings or the frontmatter ``title`` field."""
    if fm.get("title"):
        return str(fm["title"])
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()
    return ""


def _to_utc_iso(dt: datetime) -> str:
    """Normalise *dt* to UTC and return a fixed-precision ISO 8601 string.

    Always emits ``+00:00`` with microsecond precision so that TEXT comparisons
    in SQLite are chronologically correct regardless of the original tz offset.

    * Naive datetime (no tzinfo) → treated as UTC.
    * Aware datetime with any offset → converted to UTC.
    """
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat(timespec="microseconds")


def _make_claim_id(path: str, statement: str) -> str:
    """Derive a stable, well-formed UUID5 claim_id from path + statement."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}\x00{statement}"))


def claim_from_note(
    frontmatter: dict[str, Any],
    body: str,
    *,
    path: str,
    content_hash: str,
    normalize: Any = None,
) -> Claim | None:
    """Build a :class:`Claim` from a parsed frontmatter dict and body text.

    Returns ``None`` when the note carries none of ``valid_from``, ``valid_to``,
    ``observed_at`` in frontmatter AND its ``type`` is not ``"claim"``.

    Malformed date fields are silently treated as ``None`` (never raise).

    Parameters
    ----------
    frontmatter:
        Raw frontmatter dict (as parsed by the indexer).
    body:
        Markdown body text (already redacted by the caller).
    path:
        Vault-relative path, forward slashes.
    content_hash:
        SHA-256 hex of the already-redacted file content.
    normalize:
        Optional callable ``(str) -> str`` for ``statement_normalized``.
        Defaults to identity when ``None``.
    """
    _normalize = normalize if normalize is not None else (lambda s: s)

    # --- Eligibility gate ---
    has_temporal = any(
        frontmatter.get(k) is not None for k in ("valid_from", "valid_to", "observed_at")
    )
    is_claim_type = str(frontmatter.get("type", "")).strip() == "claim"
    if not has_temporal and not is_claim_type:
        return None

    # --- Parse temporal fields gracefully (malformed -> None, never raise) ---
    valid_from: datetime | None = None
    raw_vf = frontmatter.get("valid_from")
    if raw_vf is not None:
        valid_from = _parse_dt(raw_vf)  # returns None on malformed

    valid_to: datetime | None = None
    raw_vt = frontmatter.get("valid_to")
    if raw_vt is not None:
        valid_to = _parse_dt(raw_vt)

    observed_at: datetime | None = None
    raw_oa = frontmatter.get("observed_at")
    if raw_oa is not None:
        observed_at = _parse_dt(raw_oa)

    # Fall back to frontmatter ``created`` when observed_at absent or malformed
    if observed_at is None:
        raw_created = frontmatter.get("created")
        if raw_created is not None:
            observed_at = _parse_dt(raw_created)

    # If still None, use epoch sentinel (mirrors _mtime_to_dt(None))
    if observed_at is None:
        from datetime import UTC

        observed_at = datetime.fromtimestamp(0, tz=UTC)

    # --- Statement extraction + redaction ---
    raw_statement = frontmatter.get("statement")
    if raw_statement is not None:
        statement_raw = str(raw_statement)
    else:
        title = _extract_title(body, frontmatter)
        statement_raw = title if title else path

    statement = redact(statement_raw)
    statement_normalized = _normalize(statement)

    # --- Optional fields ---
    supersedes_raw = frontmatter.get("supersedes")
    supersedes: str | None = str(supersedes_raw) if supersedes_raw is not None else None

    claim_key_raw = frontmatter.get("claim_key")
    claim_key: str | None = str(claim_key_raw) if claim_key_raw is not None else None

    # Fix B: use the note's stable frontmatter id as claim_id so that
    # `supersedes: <other-note-id>` references resolve correctly.
    # Fallback: deterministic uuid5 of path+statement (no 64-bit/malformed-UUID issue).
    fm_id = frontmatter.get("id")
    if fm_id is not None and str(fm_id).strip():
        claim_id = str(fm_id).strip()
    else:
        claim_id = _make_claim_id(path, statement)

    return Claim(
        claim_id=claim_id,
        path=path,
        statement=statement,
        statement_normalized=statement_normalized,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
        supersedes=supersedes,
        claim_key=claim_key,
        confidence_label=ConfidenceLabel.EXTRACTED,
        trust="user",
        content_hash=content_hash,
    )
