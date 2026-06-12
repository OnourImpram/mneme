"""FailureMemory dataclass and vault-ready markdown serialization.

Provenance invariant: every ``FailureMemory`` carries:
    failure_id    — deterministic UUID5 derived from exc_type + first frame + source_label.
    content_hash  — sha256 hex of a canonical redacted text rendering.
    observed_at   — injected by caller; never produced inside this module.
    trust         — default ``"user"`` (operator-supplied traceback).
    confidence    — default ``"EXTRACTED"`` (directly observed, no inference).

All string content on the incoming ``ParsedTraceback`` has already been
redacted by ``mneme_code.stacktrace.parse_traceback``; this module does
not re-apply redaction but must not introduce new unredacted content.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mneme_core.privacy import redact

from mneme_code.stacktrace import Frame, ParsedTraceback


@dataclass(frozen=True)
class FailureMemory:
    """A provenance-labelled, redacted record of one Python failure.

    Attributes:
        failure_id    Deterministic UUID5 (namespace URL) derived from
                      ``exc_type``, first frame coordinates, and
                      ``source_label``.
        exc_type      Exception class name (already redacted upstream).
        exc_message   Redacted exception message.
        frames        Redacted stack frames, innermost last.
        observed_at   Tz-aware UTC datetime injected by the caller.
        source_label  Optional human label identifying the failure source
                      (e.g. ``"ci-run-42"``).
        content_hash  SHA-256 hex of a canonical redacted rendering.
        trust         Provenance tier; default ``"user"``.
        confidence    Confidence label; default ``"EXTRACTED"``.
    """

    failure_id: str
    exc_type: str
    exc_message: str
    frames: tuple[Frame, ...]
    observed_at: datetime
    source_label: str | None
    content_hash: str
    trust: str = field(default="user")
    confidence: str = field(default="EXTRACTED")


def failure_from_traceback(
    parsed: ParsedTraceback,
    *,
    observed_at: datetime,
    source_label: str | None = None,
) -> FailureMemory:
    """Construct a :class:`FailureMemory` from a parsed, redacted traceback.

    Args:
        parsed:       Already-redacted :class:`ParsedTraceback` from
                      ``parse_traceback``.
        observed_at:  Tz-aware UTC datetime for this failure observation.
                      Must be injected by the caller; this function never
                      calls ``datetime.now()``.
        source_label: Optional label for the failure source.

    Returns:
        A deterministic :class:`FailureMemory` — same inputs always
        produce the same ``failure_id`` and ``content_hash``.
    """
    first_frame = parsed.frames[0] if parsed.frames else Frame("", 0, "", None)
    label_part = source_label or ""

    # Deterministic UUID5: namespace URL + canonical key.
    key = f"{parsed.exc_type}\x00{first_frame.file_path}:{first_frame.line}\x00{label_part}"
    failure_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    # Canonical text for content_hash: all already-redacted fields.
    canonical = _canonical_text(parsed, source_label)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return FailureMemory(
        failure_id=failure_id,
        exc_type=parsed.exc_type,
        exc_message=parsed.exc_message,
        frames=parsed.frames,
        observed_at=observed_at,
        source_label=source_label,
        content_hash=content_hash,
    )


def failure_to_markdown(failure: FailureMemory, *, branch: str | None = None) -> str:
    """Render a :class:`FailureMemory` as a vault-ready markdown note.

    Frontmatter fields:
        id            failure.failure_id
        type          ``"failure"``
        created       failure.observed_at as tz-aware UTC ISO 8601 with microseconds
        tags          ``["failure"]``
        exc_type      failure.exc_type
        source_label  failure.source_label (omitted when ``None``)
        branch        caller-supplied git branch (omitted when ``None``);
                      metadata only — it never participates in failure_id
                      or content_hash, so determinism is preserved

    Body: redacted summary — exception message followed by frame list
    (``file:line in func`` format).

    All content is already redacted upstream; this function does not
    re-apply redaction.
    """
    # Build frontmatter manually to control field order and avoid yaml import.
    # Normalise observed_at to tz-aware UTC so `created` is consistent with the
    # rest of mneme regardless of the caller's tz (mirrors the temporal layer).
    observed = failure.observed_at
    observed = observed.replace(tzinfo=UTC) if observed.tzinfo is None else observed.astimezone(UTC)
    created_str = observed.isoformat(timespec="microseconds")
    fm_lines = [
        "---",
        f"id: {failure.failure_id}",
        "type: failure",
        f"created: {created_str}",
        "tags:",
        "  - failure",
        f"exc_type: {failure.exc_type}",
    ]
    if failure.source_label is not None:
        # JSON-encode so an arbitrary user label stays valid YAML.
        fm_lines.append(f"source_label: {json.dumps(failure.source_label)}")
    if branch is not None:
        # Branch-aware failure tracking: redact then JSON-encode so an
        # arbitrary branch name stays valid YAML and never leaks a
        # <private> span embedded in an exotic branch name.
        fm_lines.append(f"branch: {json.dumps(redact(branch))}")
    fm_lines.append("---")

    # Body
    body_lines: list[str] = []
    body_lines.append(f"# {failure.exc_type}")
    body_lines.append("")
    if failure.exc_message:
        body_lines.append(f"> {failure.exc_message}")
        body_lines.append("")

    body_lines.append("## Stack frames")
    body_lines.append("")
    for frame in failure.frames:
        body_lines.append(f"- `{frame.file_path}:{frame.line}` in `{frame.function}`")

    return "\n".join(fm_lines) + "\n" + "\n".join(body_lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _canonical_text(parsed: ParsedTraceback, source_label: str | None) -> str:
    """Produce the canonical deterministic text used for content_hash."""
    parts = [parsed.exc_type, parsed.exc_message]
    for frame in parsed.frames:
        parts.append(f"{frame.file_path}:{frame.line}:{frame.function}")
        if frame.code_context is not None:
            parts.append(frame.code_context)
    if source_label is not None:
        parts.append(source_label)
    return "\n".join(parts)
