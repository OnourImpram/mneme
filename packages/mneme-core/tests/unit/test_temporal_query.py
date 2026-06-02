"""Unit tests for mneme_core.temporal.query (as_of, current, find_contradictions)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from mneme_core.temporal.index import ensure_temporal_schema
from mneme_core.temporal.query import as_of, current, find_contradictions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2024, 1, 1, tzinfo=UTC)
T1 = datetime(2024, 6, 1, tzinfo=UTC)
T2 = datetime(2024, 12, 1, tzinfo=UTC)
T3 = datetime(2025, 6, 1, tzinfo=UTC)
T4 = datetime(2025, 12, 1, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_temporal_schema(conn)
    return conn


def _insert(
    conn: sqlite3.Connection,
    claim_id: str,
    statement: str = "test claim",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    observed_at: datetime | None = None,
    supersedes: str | None = None,
    claim_key: str | None = None,
) -> None:
    obs = (observed_at or T0).isoformat()
    conn.execute(
        """
        INSERT INTO claims
            (claim_id, path, statement, statement_normalized,
             valid_from, valid_to, observed_at,
             supersedes, superseded_by, claim_key,
             confidence_label, trust, content_hash, indexed_at)
        VALUES (?, 'test.md', ?, ?, ?, ?, ?, ?, NULL, ?, 'EXTRACTED', 'user', 'hash', ?)
        """,
        (
            claim_id,
            statement,
            statement.lower(),
            valid_from.isoformat() if valid_from else None,
            valid_to.isoformat() if valid_to else None,
            obs,
            supersedes,
            claim_key,
            T0.isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# as_of: validity window
# ---------------------------------------------------------------------------

class TestAsOfWindow:
    def test_open_window_claim_always_live(self) -> None:
        conn = _conn()
        _insert(conn, "c1")  # no valid_from, no valid_to
        result = as_of(conn, T1)
        assert any(c.claim_id == "c1" for c in result)

    def test_claim_before_valid_from_excluded(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T2)  # starts at T2
        result = as_of(conn, T1)  # querying at T1 < T2
        assert not any(c.claim_id == "c1" for c in result)

    def test_claim_at_valid_from_included(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T1)
        result = as_of(conn, T1)  # exactly at boundary
        assert any(c.claim_id == "c1" for c in result)

    def test_claim_at_valid_to_excluded(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T2)
        result = as_of(conn, T2)  # valid_to is exclusive
        assert not any(c.claim_id == "c1" for c in result)

    def test_claim_before_valid_to_included(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T2)
        result = as_of(conn, T1)  # T0 <= T1 < T2
        assert any(c.claim_id == "c1" for c in result)

    def test_expired_claim_excluded(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T1)
        result = as_of(conn, T2)  # past valid_to
        assert not any(c.claim_id == "c1" for c in result)

    def test_empty_table_returns_empty(self) -> None:
        conn = _conn()
        assert as_of(conn, T1) == []

    def test_missing_table_returns_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        # No schema created — claims table absent
        assert as_of(conn, T1) == []


# ---------------------------------------------------------------------------
# as_of: dynamic supersession
# ---------------------------------------------------------------------------

class TestAsOfSupersession:
    def test_superseded_claim_excluded_after_superseder_valid_from(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0)
        _insert(conn, "c2", valid_from=T2, supersedes="c1")
        # At T3 (after c2.valid_from=T2), c1 is shadowed by c2
        result = as_of(conn, T3)
        ids = {c.claim_id for c in result}
        assert "c1" not in ids
        assert "c2" in ids

    def test_superseded_claim_still_live_before_superseder_valid_from(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0)
        _insert(conn, "c2", valid_from=T2, supersedes="c1")
        # At T1 (before c2.valid_from=T2), c1 is NOT shadowed
        result = as_of(conn, T1)
        ids = {c.claim_id for c in result}
        assert "c1" in ids

    def test_superseder_with_open_valid_from_shadows_immediately(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0)
        _insert(conn, "c2", supersedes="c1")  # no valid_from = open = always active
        result = as_of(conn, T1)
        ids = {c.claim_id for c in result}
        assert "c1" not in ids

    def test_non_destructive_supersession_target_row_still_present(self) -> None:
        """The target row must survive supersession (non-destructive invariant)."""
        conn = _conn()
        _insert(conn, "c1", valid_from=T0)
        _insert(conn, "c2", valid_from=T2, supersedes="c1")
        # Target row c1 still exists in the table after indexing/supersession pass
        row = conn.execute(
            "SELECT claim_id FROM claims WHERE claim_id=?", ("c1",)
        ).fetchone()
        assert row is not None, "Superseded claim row must not be deleted"


# ---------------------------------------------------------------------------
# current()
# ---------------------------------------------------------------------------

class TestCurrent:
    def test_current_calls_as_of_with_provided_now(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T3)
        now = T1
        result = current(conn, now)
        assert any(c.claim_id == "c1" for c in result)

    def test_current_excludes_expired(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T1)
        result = current(conn, T2)
        assert not any(c.claim_id == "c1" for c in result)


# ---------------------------------------------------------------------------
# find_contradictions()
# ---------------------------------------------------------------------------

class TestFindContradictions:
    def test_overlapping_same_key_detected(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T3, claim_key="user.city")
        _insert(conn, "c2", valid_from=T1, valid_to=T4, claim_key="user.city")
        pairs = find_contradictions(conn)
        assert len(pairs) == 1
        a, b = pairs[0]
        assert a < b  # sorted
        assert {a, b} == {"c1", "c2"}

    def test_disjoint_windows_same_key_not_contradictions(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, valid_to=T1, claim_key="user.city")
        _insert(conn, "c2", valid_from=T2, valid_to=T3, claim_key="user.city")
        pairs = find_contradictions(conn)
        assert pairs == []

    def test_different_keys_not_contradictions(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, claim_key="user.city")
        _insert(conn, "c2", valid_from=T0, claim_key="user.country")
        pairs = find_contradictions(conn)
        assert pairs == []

    def test_null_claim_key_ignored(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0)  # no claim_key
        _insert(conn, "c2", valid_from=T0)
        pairs = find_contradictions(conn)
        assert pairs == []

    def test_open_windows_overlap(self) -> None:
        conn = _conn()
        _insert(conn, "c1", claim_key="user.city")  # fully open
        _insert(conn, "c2", claim_key="user.city")  # fully open
        pairs = find_contradictions(conn)
        assert len(pairs) == 1

    def test_pairs_sorted_and_unique(self) -> None:
        conn = _conn()
        _insert(conn, "z1", valid_from=T0, claim_key="k")
        _insert(conn, "a1", valid_from=T0, claim_key="k")
        _insert(conn, "m1", valid_from=T0, claim_key="k")
        pairs = find_contradictions(conn)
        # All pairs, a1 < m1 < z1 lexicographically
        for a, b in pairs:
            assert a < b

    def test_at_filter_uses_live_claims_only(self) -> None:
        conn = _conn()
        # c1 lives T0..T1, c2 lives T2..T4 — same key but different times
        _insert(conn, "c1", valid_from=T0, valid_to=T1, claim_key="user.city")
        _insert(conn, "c2", valid_from=T2, valid_to=T4, claim_key="user.city")
        # At T0: only c1 is live — no contradiction
        pairs = find_contradictions(conn, at=T0)
        assert pairs == []

    def test_empty_table_returns_empty(self) -> None:
        conn = _conn()
        assert find_contradictions(conn) == []

    def test_missing_table_returns_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        assert find_contradictions(conn) == []
