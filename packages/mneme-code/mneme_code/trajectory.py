"""FixTrajectory dataclass and vault-ready markdown serialization.

A fix-trajectory links a :class:`~mneme_code.failure.FailureMemory` to its
resolution via temporal supersession.  The fix note is rendered with
``type: claim`` and ``supersedes: <failure_note_id>`` so the mneme-temporal
layer's ``as_of`` / ``current`` queries automatically treat the failure as
resolved after ``resolved_at`` — no new infrastructure required.

Provenance invariant: every ``FixTrajectory`` carries:
    trajectory_id   — deterministic UUID5 derived from failure_id + redacted fix_summary.
    failure_id      — the ``FailureMemory.failure_id`` being resolved.
    failure_note_id — the vault note id of the failure note (target of ``supersedes``).
    fix_summary     — redacted human summary of the fix.
    resolved_at     — injected by caller; never produced inside this module.
    trust           — default ``"user"`` (operator-supplied resolution).
    confidence      — default ``"EXTRACTED"`` (directly observed, no inference).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mneme_core.privacy import redact

from mneme_code.failure import FailureMemory


@dataclass(frozen=True)
class FixTrajectory:
    """A provenance-labelled, redacted record of one failure resolution.

    Attributes:
        trajectory_id    Deterministic UUID5 (namespace URL) derived from
                         ``failure_id`` and the redacted ``fix_summary``.
        failure_id       The ``FailureMemory.failure_id`` being resolved.
        failure_note_id  The vault note id of the failure note; used as the
                         ``supersedes`` target in the temporal claim frontmatter.
        fix_summary      Redacted human summary of the fix applied.
        resolved_at      Tz-aware UTC datetime injected by the caller.
        trust            Provenance tier; default ``"user"``.
        confidence       Confidence label; default ``"EXTRACTED"``.
    """

    trajectory_id: str
    failure_id: str
    failure_note_id: str
    fix_summary: str
    resolved_at: datetime
    trust: str = field(default="user")
    confidence: str = field(default="EXTRACTED")


def fix_from_failure(
    failure: FailureMemory,
    *,
    fix_summary: str,
    resolved_at: datetime,
    failure_note_id: str | None = None,
) -> FixTrajectory:
    """Construct a :class:`FixTrajectory` from a :class:`FailureMemory`.

    Args:
        failure:          The :class:`FailureMemory` being resolved.
        fix_summary:      Human summary of the fix; redacted via
                          :func:`mneme_core.privacy.redact` before storage.
        resolved_at:      Tz-aware UTC datetime for this resolution.
                          Must be injected by the caller; this function never
                          calls ``datetime.now()``.
        failure_note_id:  Vault note id of the failure note.  Defaults to
                          ``failure.failure_id`` when omitted.

    Returns:
        A deterministic :class:`FixTrajectory` — same inputs always produce
        the same ``trajectory_id``.
    """
    note_id = failure_note_id if failure_note_id is not None else failure.failure_id
    redacted_summary = redact(fix_summary)

    # Deterministic UUID5: namespace URL + failure_id + NUL separator + redacted summary.
    key = f"{failure.failure_id}\x00{redacted_summary}"
    trajectory_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    return FixTrajectory(
        trajectory_id=trajectory_id,
        failure_id=failure.failure_id,
        failure_note_id=note_id,
        fix_summary=redacted_summary,
        resolved_at=resolved_at,
    )


def fix_to_markdown(fix: FixTrajectory) -> str:
    """Render a :class:`FixTrajectory` as a vault-ready temporal claim note.

    Frontmatter fields:
        id              fix.trajectory_id
        type            ``"claim"``  — indexes the note into the temporal layer.
        created         fix.resolved_at as tz-aware UTC ISO 8601 with microseconds.
        tags            ``["fix", "trajectory"]``
        claim_key       ``"failure.<failure_id>"`` — ties fix and failure together.
        supersedes      fix.failure_note_id — temporal supersession target.
        valid_from      fix.resolved_at as tz-aware UTC ISO 8601 with microseconds.
        trust           fix.trust
        confidence      fix.confidence

    Body: heading ``# Fix for <failure_id>`` followed by the redacted fix_summary.

    All content has already been redacted at construction time; this function
    does not re-apply redaction.
    """
    # Normalise resolved_at to tz-aware UTC (mirrors failure_to_markdown and _to_utc_iso).
    resolved = fix.resolved_at
    resolved = resolved.replace(tzinfo=UTC) if resolved.tzinfo is None else resolved.astimezone(UTC)
    resolved_str = resolved.isoformat(timespec="microseconds")

    fm_lines = [
        "---",
        f"id: {fix.trajectory_id}",
        "type: claim",
        f"created: {resolved_str}",
        "tags:",
        "  - fix",
        "  - trajectory",
        f"claim_key: {json.dumps(f'failure.{fix.failure_id}')}",
        f"supersedes: {json.dumps(fix.failure_note_id)}",
        f"valid_from: {resolved_str}",
        f"trust: {fix.trust}",
        f"confidence: {fix.confidence}",
        "---",
    ]

    body_lines: list[str] = []
    body_lines.append(f"# Fix for {fix.failure_id}")
    body_lines.append("")
    body_lines.append(fix.fix_summary)

    return "\n".join(fm_lines) + "\n" + "\n".join(body_lines) + "\n"
