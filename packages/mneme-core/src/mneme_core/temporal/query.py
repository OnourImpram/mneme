"""Temporal claim query functions.

All queries operate directly on the SQLite ``claims`` table.  The key design
invariant is that supersession is computed **dynamically** against ``t``; the
denormalized ``superseded_by`` column is informational only and is never used
here.

Invariants
----------
* :func:`as_of` and :func:`current` never call ``datetime.now`` internally;
  callers must pass the timestamp so behavior is injectable and deterministic
  in tests.
* :func:`find_contradictions` returns sorted unique ``(a, b)`` pairs with
  ``a < b`` (lexicographic on claim_id).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .claim import Claim, ConfidenceLabel, _to_utc_iso


def _row_to_claim(row: tuple[Any, ...]) -> Claim:
    """Convert a raw SQLite row (all 14 columns in schema order) to a Claim."""
    (
        claim_id,
        path,
        statement,
        statement_normalized,
        valid_from_s,
        valid_to_s,
        observed_at_s,
        supersedes,
        _superseded_by,
        claim_key,
        confidence_label_s,
        trust,
        content_hash,
        _indexed_at,
    ) = row

    from ..vault.frontmatter import _parse_dt

    def _dt(s: str | None) -> datetime | None:
        return _parse_dt(s) if s is not None else None

    obs = _dt(observed_at_s)
    from datetime import UTC

    observed_at = obs if obs is not None else datetime.fromtimestamp(0, tz=UTC)

    try:
        label = ConfidenceLabel(confidence_label_s)
    except ValueError:
        label = ConfidenceLabel.EXTRACTED

    return Claim(
        claim_id=claim_id,
        path=path,
        statement=statement,
        statement_normalized=statement_normalized,
        valid_from=_dt(valid_from_s),
        valid_to=_dt(valid_to_s),
        observed_at=observed_at,
        supersedes=supersedes,
        claim_key=claim_key,
        confidence_label=label,
        trust=trust,
        content_hash=content_hash,
    )


def _all_columns() -> str:
    return (
        "claim_id, path, statement, statement_normalized, "
        "valid_from, valid_to, observed_at, supersedes, superseded_by, "
        "claim_key, confidence_label, trust, content_hash, indexed_at"
    )


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'"
    ).fetchone()
    return row is not None


def as_of(conn: sqlite3.Connection, t: datetime) -> list[Claim]:
    """Return all claims whose validity window contains ``t`` and are not shadowed.

    Validity window: ``(valid_from IS NULL OR valid_from <= t) AND
    (valid_to IS NULL OR t < valid_to)``.

    Shadowed: claim C is shadowed at t iff there exists C2 such that
    ``C2.supersedes == C.claim_id`` AND ``(C2.valid_from IS NULL OR
    C2.valid_from <= t)``.

    Supersession is computed dynamically against ``t``; the static
    ``superseded_by`` column is not used.
    """
    if not _table_exists(conn):
        return []

    t_iso = _to_utc_iso(t)
    cols = _all_columns()

    # Fetch all claims in the validity window.
    rows = conn.execute(
        f"""
        SELECT {cols}
        FROM claims
        WHERE (valid_from IS NULL OR valid_from <= ?)
          AND (valid_to   IS NULL OR ? < valid_to)
        """,
        (t_iso, t_iso),
    ).fetchall()

    if not rows:
        return []

    # Build set of shadowed claim_ids at time t.
    shadowed: set[str] = set()
    superseder_rows = conn.execute(
        """
        SELECT supersedes
        FROM claims
        WHERE supersedes IS NOT NULL
          AND (valid_from IS NULL OR valid_from <= ?)
        """,
        (t_iso,),
    ).fetchall()
    for (target_id,) in superseder_rows:
        shadowed.add(target_id)

    return [_row_to_claim(r) for r in rows if r[0] not in shadowed]


def current(conn: sqlite3.Connection, now: datetime) -> list[Claim]:
    """Return all claims live at ``now``.

    Equivalent to :func:`as_of` with ``t=now``. The caller must pass
    ``datetime.now(UTC)``; this function never calls ``datetime.now`` itself.
    """
    return as_of(conn, now)


def find_contradictions(
    conn: sqlite3.Connection,
    *,
    at: datetime | None = None,
) -> list[tuple[str, str]]:
    """Find pairs of live claims sharing a non-null ``claim_key`` with overlapping windows.

    Two claims A and B with the same non-null ``claim_key`` overlap when their
    validity windows intersect.  Standard interval overlap (treating None as open):
    ``A.valid_from < B.valid_to AND B.valid_from < A.valid_to``
    where None on the left of ``<`` is -infinity and None on the right is +infinity.

    When ``at`` is provided, only claims live (non-shadowed) at ``at`` are
    considered.  When ``at`` is ``None``, all claims with a non-null
    ``claim_key`` are checked regardless of time.

    Returns a sorted list of unique ``(claim_id_a, claim_id_b)`` pairs with
    ``claim_id_a < claim_id_b`` (lexicographic).
    """
    if not _table_exists(conn):
        return []

    # Collect candidate claims.
    if at is not None:
        candidates = as_of(conn, at)
        candidate_map = {c.claim_id: c for c in candidates if c.claim_key is not None}
    else:
        cols = _all_columns()
        rows = conn.execute(
            f"SELECT {cols} FROM claims WHERE claim_key IS NOT NULL"
        ).fetchall()
        candidate_map = {r[0]: _row_to_claim(r) for r in rows}

    # Group by claim_key.
    by_key: dict[str, list[Claim]] = {}
    for claim in candidate_map.values():
        if claim.claim_key is not None:
            by_key.setdefault(claim.claim_key, []).append(claim)

    # Find overlapping pairs within each key group.
    pairs: set[tuple[str, str]] = set()

    def _overlaps(a: Claim, b: Claim) -> bool:
        """Standard interval overlap with None = open."""
        # [a.valid_from, a.valid_to) overlaps [b.valid_from, b.valid_to)
        # iff NOT (a.valid_to <= b.valid_from OR b.valid_to <= a.valid_from)
        # treating None valid_from as -inf, None valid_to as +inf.
        a_before_b = (
            a.valid_to is not None and b.valid_from is not None and a.valid_to <= b.valid_from
        )
        b_before_a = (
            b.valid_to is not None and a.valid_from is not None and b.valid_to <= a.valid_from
        )
        return not (a_before_b or b_before_a)

    for group in by_key.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if _overlaps(a, b):
                    lo, hi = (
                        (a.claim_id, b.claim_id)
                        if a.claim_id < b.claim_id
                        else (b.claim_id, a.claim_id)
                    )
                    pairs.add((lo, hi))

    return sorted(pairs)
