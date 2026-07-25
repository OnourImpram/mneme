"""Unit tests for mneme_core.temporal.query (as_of, current, find_contradictions)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mneme_core.temporal.backend import ambiguous_claim_ids, make_temporal_backend
from mneme_core.temporal.claim import ConfidenceLabel
from mneme_core.temporal.index import ensure_temporal_schema
from mneme_core.temporal.query import (
    as_of,
    current,
    find_contradictions,
    find_contradictions_scoped,
)

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
    statement: str | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    observed_at: datetime | None = None,
    supersedes: str | None = None,
    claim_key: str | None = None,
    scope: str = "default",
) -> None:
    statement_value = statement if statement is not None else f"test claim {claim_id}"
    obs = (observed_at or T0).isoformat()
    conn.execute(
        """
        INSERT INTO claims
            (claim_id, path, statement, statement_normalized,
             valid_from, valid_to, observed_at,
             supersedes, superseded_by, claim_key,
             confidence_label, trust, content_hash, scope, indexed_at)
        VALUES (?, 'test.md', ?, ?, ?, ?, ?, ?, NULL, ?, 'EXTRACTED', 'user',
                'hash', ?, ?)
        """,
        (
            claim_id,
            statement_value,
            statement_value.lower(),
            valid_from.isoformat() if valid_from else None,
            valid_to.isoformat() if valid_to else None,
            obs,
            supersedes,
            claim_key,
            scope,
            T0.isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# as_of: validity window
# ---------------------------------------------------------------------------


class TestAsOfWindow:
    def test_future_observation_is_not_visible_in_past_snapshot(self) -> None:
        conn = _conn()
        _insert(conn, "retroactive", valid_from=T0, observed_at=T3)

        assert as_of(conn, T1) == []
        assert [claim.claim_id for claim in as_of(conn, T4)] == ["retroactive"]

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

    def test_invalid_stored_temporal_metadata_is_not_surfaced(self) -> None:
        conn = _conn()
        _insert(conn, "valid")
        _insert(conn, "bad-observed")
        _insert(conn, "bad-valid-to")
        conn.execute(
            "UPDATE claims SET observed_at = '0' WHERE claim_id = 'bad-observed'"
        )
        conn.execute(
            "UPDATE claims SET valid_to = 'not-a-date' WHERE claim_id = 'bad-valid-to'"
        )
        conn.commit()

        assert [claim.claim_id for claim in as_of(conn, T1)] == ["valid"]

    def test_concrete_scope_is_isolated(self) -> None:
        conn = _conn()
        _insert(conn, "clinical-1", scope="clinical")
        _insert(conn, "research-1", scope="research")
        assert {claim.claim_id for claim in as_of(conn, T1, scope="clinical")} == {"clinical-1"}

    def test_explicit_wildcard_reads_all_scopes(self) -> None:
        conn = _conn()
        _insert(conn, "clinical-1", scope="clinical")
        _insert(conn, "research-1", scope="research")
        assert {claim.claim_id for claim in as_of(conn, T1, scope="*")} == {
            "clinical-1",
            "research-1",
        }

    def test_identical_claim_ids_coexist_across_scopes(self) -> None:
        conn = _conn()
        _insert(conn, "shared", statement="clinical", scope="clinical")
        _insert(conn, "shared", statement="research", scope="research")
        assert as_of(conn, T1, scope="clinical")[0].statement == "clinical"
        assert as_of(conn, T1, scope="research")[0].statement == "research"

    def test_legacy_schema_rows_migrate_to_default_scope(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY, path TEXT NOT NULL,
                statement TEXT NOT NULL, statement_normalized TEXT NOT NULL,
                valid_from TEXT, valid_to TEXT, observed_at TEXT NOT NULL,
                supersedes TEXT, superseded_by TEXT, claim_key TEXT,
                confidence_label TEXT NOT NULL, trust TEXT NOT NULL,
                content_hash TEXT NOT NULL, indexed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO claims VALUES (
                'legacy-1', 'notes/<private>path-canary</private>.md',
                'Status is <private>statement-canary</private> stable.',
                'status is <private>normalized-canary</private> stable.',
                NULL, NULL, '2026-01-01T00:00:00+00:00', NULL, NULL,
                '<private>key-canary</private>', 'EXTRACTED',
                '<private>trust-canary</private>', 'hash',
                '2026-01-01T00:00:00+00:00'
            )
            """
        )
        ensure_temporal_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(claims)")}
        assert "scope" in columns
        info = conn.execute("PRAGMA table_info(claims)").fetchall()
        default_sql = next(row[4] for row in info if row[1] == "scope")
        assert default_sql == "'default'"
        primary_key = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        assert primary_key == ["scope", "claim_id"]
        migrated = conn.execute(
            "SELECT path, statement, statement_normalized, claim_key, trust, scope "
            "FROM claims WHERE claim_id='legacy-1'"
        ).fetchone()
        assert migrated is not None
        assert migrated[-1] == "default"
        assert all("canary" not in str(value) for value in migrated)
        assert "[REDACTED]" in str(migrated)


# ---------------------------------------------------------------------------
# as_of: dynamic supersession
# ---------------------------------------------------------------------------


class TestAsOfSupersession:
    def test_future_observed_superseder_does_not_shadow_past_snapshot(self) -> None:
        conn = _conn()
        _insert(conn, "c1", valid_from=T0, observed_at=T0)
        _insert(
            conn,
            "c2",
            valid_from=T0,
            observed_at=T3,
            supersedes="c1",
        )

        assert {claim.claim_id for claim in as_of(conn, T1)} == {"c1"}
        assert {claim.claim_id for claim in as_of(conn, T4)} == {"c2"}

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
        row = conn.execute("SELECT claim_id FROM claims WHERE claim_id=?", ("c1",)).fetchone()
        assert row is not None, "Superseded claim row must not be deleted"

    def test_supersession_never_crosses_scope(self) -> None:
        conn = _conn()
        _insert(conn, "target", valid_from=T0, scope="clinical")
        _insert(
            conn,
            "foreign-superseder",
            valid_from=T0,
            supersedes="target",
            scope="research",
        )
        ids = {claim.claim_id for claim in as_of(conn, T1, scope="clinical")}
        assert "target" in ids

    def test_expired_superseder_no_longer_shadows_target(self) -> None:
        conn = _conn()
        _insert(conn, "target", valid_from=T0)
        _insert(
            conn,
            "temporary-replacement",
            valid_from=T1,
            valid_to=T2,
            supersedes="target",
        )

        assert {claim.claim_id for claim in as_of(conn, T1)} == {
            "temporary-replacement"
        }
        assert {claim.claim_id for claim in as_of(conn, T3)} == {"target"}


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

    def test_identical_statements_are_not_contradictions(self) -> None:
        conn = _conn()
        _insert(conn, "c1", statement="User lives in Istanbul", claim_key="user.city")
        _insert(conn, "c2", statement="User lives in Istanbul", claim_key="user.city")

        assert find_contradictions(conn) == []

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

    def test_equal_keys_in_different_scopes_are_not_contradictions(self) -> None:
        conn = _conn()
        _insert(conn, "clinical", claim_key="user.city", scope="clinical")
        _insert(conn, "research", claim_key="user.city", scope="research")
        assert find_contradictions(conn, scope="*") == []

    def test_contradictions_are_visible_within_requested_scope(self) -> None:
        conn = _conn()
        _insert(conn, "c1", claim_key="user.city", scope="clinical")
        _insert(conn, "c2", claim_key="user.city", scope="clinical")
        _insert(conn, "r1", claim_key="user.city", scope="research")
        assert find_contradictions(conn, scope="clinical") == [("c1", "c2")]

    def test_scoped_wildcard_identity_does_not_collapse_equal_pairs(self) -> None:
        conn = _conn()
        for scope in ("clinical", "research"):
            _insert(conn, "c1", claim_key="user.city", scope=scope)
            _insert(conn, "c2", claim_key="user.city", scope=scope)

        assert find_contradictions_scoped(conn, scope="*") == [
            ("clinical", "c1", "c2"),
            ("research", "c1", "c2"),
        ]


class TestWildcardBackendIdentity:
    def test_duplicate_claim_ids_remain_distinct_and_ambiguity_is_scoped(self) -> None:
        conn = _conn()
        for scope in ("clinical", "research"):
            _insert(
                conn,
                "c1",
                statement="shared memory one",
                claim_key="shared.memory",
                scope=scope,
            )
            _insert(
                conn,
                "c2",
                statement="shared memory two",
                claim_key="shared.memory",
                scope=scope,
            )

        ambiguous = ambiguous_claim_ids(conn, scope="*")
        backend = make_temporal_backend(conn, scope="*")
        hits = backend("shared memory", 10)

        assert len(ambiguous) == 4
        assert len(hits) == 4
        assert len({hit.id for hit in hits}) == 4
        assert all(
            hit.confidence_label == ConfidenceLabel.AMBIGUOUS.value for hit in hits
        )
        assert all(not hit.title.startswith("[AMBIGUOUS]") for hit in hits)
        assert all(hit.content_hash == "hash" for hit in hits)
        assert all(hit.trust == "user" for hit in hits)


class TestTemporalBackendSnapshot:
    def test_default_backend_uses_one_current_snapshot(self) -> None:
        conn = _conn()
        now = datetime.now(UTC)
        _insert(
            conn,
            "current",
            statement="shared current memory",
            observed_at=now - timedelta(days=2),
        )
        _insert(
            conn,
            "expired",
            statement="shared expired memory",
            observed_at=now - timedelta(days=3),
            valid_to=now - timedelta(days=1),
        )
        _insert(
            conn,
            "future",
            statement="shared future memory",
            observed_at=now + timedelta(days=1),
        )

        hits = make_temporal_backend(conn)("shared memory", 10)

        assert [hit.id for hit in hits] == ["current"]
        assert hits[0].confidence_label == ConfidenceLabel.EXTRACTED.value
        assert hits[0].content_hash == "hash"
        assert hits[0].trust == "user"

    def test_automatic_contradiction_failure_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = _conn()
        _insert(conn, "current", statement="shared current memory")

        def _raise(*_args: object, **_kwargs: object) -> frozenset[str]:
            raise sqlite3.DatabaseError("contradiction query failed")

        monkeypatch.setattr("mneme_core.temporal.backend.ambiguous_claim_ids", _raise)

        assert make_temporal_backend(conn)("shared memory", 10) == []
