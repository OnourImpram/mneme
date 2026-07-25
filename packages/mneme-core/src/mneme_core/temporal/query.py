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
* :func:`find_contradictions_scoped` preserves scope in contradiction identity.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .claim import Claim, ConfidenceLabel, _to_utc_iso


def _row_to_claim(row: tuple[Any, ...]) -> Claim:
    """Convert a raw SQLite row selected by :func:`_all_columns` to a Claim."""
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
        scope,
        _indexed_at,
    ) = row

    from ..vault.frontmatter import _parse_dt

    def _dt(s: str | None, field: str, *, required: bool = False) -> datetime | None:
        if s is None:
            if required:
                raise ValueError(f"missing required temporal field: {field}")
            return None
        parsed = _parse_dt(s)
        if parsed is None:
            raise ValueError(f"invalid temporal field: {field}")
        return parsed

    valid_from = _dt(valid_from_s, "valid_from")
    valid_to = _dt(valid_to_s, "valid_to")
    if (
        valid_from is not None
        and valid_to is not None
        and _to_utc_iso(valid_from) >= _to_utc_iso(valid_to)
    ):
        raise ValueError("invalid temporal validity window")

    observed_at = _dt(observed_at_s, "observed_at", required=True)
    assert observed_at is not None

    try:
        label = ConfidenceLabel(confidence_label_s)
    except ValueError:
        label = ConfidenceLabel.EXTRACTED

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
        confidence_label=label,
        trust=trust,
        content_hash=content_hash,
        scope=scope,
    )


def _all_columns() -> str:
    return (
        "claim_id, path, statement, statement_normalized, "
        "valid_from, valid_to, observed_at, supersedes, superseded_by, "
        "claim_key, confidence_label, trust, content_hash, scope, indexed_at"
    )


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'"
    ).fetchone()
    return row is not None


def as_of(
    conn: sqlite3.Connection,
    t: datetime,
    *,
    scope: str = "default",
) -> list[Claim]:
    """Return all claims whose validity window contains ``t`` and are not shadowed.

    Transaction time: ``observed_at <= t``. Validity window:
    ``(valid_from IS NULL OR valid_from <= t) AND
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

    scope_sql = "" if scope == "*" else "AND scope = ?"
    scope_params: tuple[str, ...] = () if scope == "*" else (scope,)

    # Fetch all claims in the validity window and requested scope.
    rows = conn.execute(
        f"""
        SELECT {cols}
        FROM claims
        WHERE observed_at <= ?
          AND (valid_from IS NULL OR valid_from <= ?)
          AND (valid_to   IS NULL OR ? < valid_to)
          {scope_sql}
        ORDER BY scope, observed_at, claim_id
        """,
        (t_iso, t_iso, t_iso, *scope_params),
    ).fetchall()

    if not rows:
        return []

    live_claims: list[Claim] = []
    for row in rows:
        try:
            live_claims.append(_row_to_claim(row))
        except ValueError:
            continue

    shadowed = {
        (claim.supersedes, claim.scope)
        for claim in live_claims
        if claim.supersedes is not None
    }
    return [
        claim for claim in live_claims if (claim.claim_id, claim.scope) not in shadowed
    ]


def current(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    scope: str = "default",
) -> list[Claim]:
    """Return all claims live at ``now``.

    Equivalent to :func:`as_of` with ``t=now``. The caller must pass
    ``datetime.now(UTC)``; this function never calls ``datetime.now`` itself.
    """
    return as_of(conn, now, scope=scope)


def find_contradictions(
    conn: sqlite3.Connection,
    *,
    at: datetime | None = None,
    scope: str = "default",
) -> list[tuple[str, str]]:
    """Find pairs of live claims sharing a non-null ``claim_key`` with overlapping windows.

    Two claims A and B with different statements and the same non-null
    ``claim_key`` overlap when their validity windows intersect. Standard
    interval overlap (treating None as open):
    ``A.valid_from < B.valid_to AND B.valid_from < A.valid_to``
    where None on the left of ``<`` is -infinity and None on the right is +infinity.

    When ``at`` is provided, only claims live (non-shadowed) at ``at`` are
    considered.  When ``at`` is ``None``, all claims with a non-null
    ``claim_key`` are checked regardless of time.

    Returns a sorted list of unique ``(claim_id_a, claim_id_b)`` pairs with
    ``claim_id_a < claim_id_b`` (lexicographic).
    """
    scoped = find_contradictions_scoped(conn, at=at, scope=scope)
    return sorted({(a_id, b_id) for _, a_id, b_id in scoped})


def find_contradictions_scoped(
    conn: sqlite3.Connection,
    *,
    at: datetime | None = None,
    scope: str = "default",
) -> list[tuple[str, str, str]]:
    """Return ``(scope, claim_id_a, claim_id_b)`` contradiction identities.

    This scope-preserving form is required for wildcard reads because identical
    deterministic claim IDs may legitimately coexist in independent scopes.
    """
    if not _table_exists(conn):
        return []

    # Collect candidate claims.
    if at is not None:
        candidates = as_of(conn, at, scope=scope)
    else:
        cols = _all_columns()
        scope_sql = "" if scope == "*" else "AND scope = ?"
        scope_params: tuple[str, ...] = () if scope == "*" else (scope,)
        rows = conn.execute(
            f"SELECT {cols} FROM claims WHERE claim_key IS NOT NULL {scope_sql}",
            scope_params,
        ).fetchall()
        candidates = []
        for row in rows:
            try:
                candidates.append(_row_to_claim(row))
            except ValueError:
                continue

    # Scope remains part of the contradiction identity even for an explicit
    # wildcard query. Equal keys in independent scopes do not conflict.
    by_key: dict[tuple[str, str], list[Claim]] = {}
    for claim in candidates:
        if claim.claim_key is not None:
            by_key.setdefault((claim.scope, claim.claim_key), []).append(claim)

    # Find overlapping pairs within each key group.
    pairs: set[tuple[str, str, str]] = set()

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

    for (group_scope, _claim_key), group in by_key.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.statement != b.statement and _overlaps(a, b):
                    lo, hi = (
                        (a.claim_id, b.claim_id)
                        if a.claim_id < b.claim_id
                        else (b.claim_id, a.claim_id)
                    )
                    pairs.add((group_scope, lo, hi))

    return sorted(pairs)
