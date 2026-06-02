"""Regression tests for temporal datetime UTC normalisation (Fix A), end-to-end
supersession via frontmatter id (Fix B), and ambiguous overlay exclusion of
resolved supersessions (Fix C).

These tests write real markdown files to a tmp vault so the full pipeline
(YAML parse → _parse_dt → _to_utc_iso → SQLite TEXT → query) is exercised.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mneme_core.temporal.backend import ambiguous_claim_ids
from mneme_core.temporal.index import ensure_temporal_schema, index_claims
from mneme_core.temporal.query import as_of
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vault(tmp_path: Path) -> tuple[sqlite3.Connection, VaultConfig]:
    """Create a tmp vault directory, open an in-memory-backed SQLite db."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    conn = sqlite3.connect(":memory:")
    ensure_temporal_schema(conn)
    return conn, VaultConfig.from_path(vault_root)


def _write(vault: VaultConfig, filename: str, content: str) -> None:
    (vault.root / filename).write_text(content, encoding="utf-8")


def _index(conn: sqlite3.Connection, vault: VaultConfig) -> None:
    index_claims(conn, vault)


# ---------------------------------------------------------------------------
# Fix A — Real-disk non-UTC round-trip
# ---------------------------------------------------------------------------

class TestNonUTCRoundTrip:
    """Regression for Fix A: non-UTC offsets must be stored and queried in the
    same canonical UTC form so TEXT comparison == chronological order."""

    def test_valid_from_plus3_included_at_utc_boundary(self, tmp_path: Path) -> None:
        """2024-06-01T00:00:00+03:00 == 2024-05-31T21:00:00Z.

        as_of at the exact UTC instant (inclusive) must return the claim.
        """
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_tz.md",
            "---\n"
            "id: tz-claim-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "statement: Non-UTC valid_from test\n"
            "valid_from: 2024-06-01T00:00:00+03:00\n"
            "---\n"
            "# TZ test\n",
        )
        _index(conn, vault)

        # 2024-06-01T00:00:00+03:00 == 2024-05-31T21:00:00+00:00
        at_boundary = datetime(2024, 5, 31, 21, 0, 0, tzinfo=UTC)
        result = as_of(conn, at_boundary)
        ids = {c.claim_id for c in result}
        assert "tz-claim-a" in ids, (
            f"Claim must be live at its UTC-equivalent start; got ids={ids}"
        )

    def test_valid_from_plus3_excluded_one_second_before(self, tmp_path: Path) -> None:
        """One second before the UTC-equivalent valid_from must exclude the claim."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_tz.md",
            "---\n"
            "id: tz-claim-b\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "statement: Non-UTC exclusion test\n"
            "valid_from: 2024-06-01T00:00:00+03:00\n"
            "---\n"
            "# TZ test\n",
        )
        _index(conn, vault)

        # One second before 2024-05-31T21:00:00Z
        before_boundary = datetime(2024, 5, 31, 20, 59, 59, tzinfo=UTC)
        result = as_of(conn, before_boundary)
        ids = {c.claim_id for c in result}
        assert "tz-claim-b" not in ids, (
            f"Claim must NOT be live before its UTC-equivalent start; got ids={ids}"
        )

    def test_valid_to_plus3_exclusive_at_utc_boundary(self, tmp_path: Path) -> None:
        """valid_to: 2024-06-02T00:00:00+03:00 == 2024-06-01T21:00:00Z.

        as_of at the exact UTC-equivalent valid_to must EXCLUDE the claim (exclusive upper bound).
        as_of one microsecond before must INCLUDE it.
        """
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_tz_to.md",
            "---\n"
            "id: tz-claim-c\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "statement: Non-UTC valid_to test\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "valid_to: 2024-06-02T00:00:00+03:00\n"
            "---\n"
            "# TZ test\n",
        )
        _index(conn, vault)

        # valid_to == 2024-06-01T21:00:00Z — exclusive
        at_upper = datetime(2024, 6, 1, 21, 0, 0, tzinfo=UTC)
        result_at = as_of(conn, at_upper)
        assert "tz-claim-c" not in {c.claim_id for c in result_at}, (
            "Claim must be EXCLUDED at exact UTC-equivalent valid_to (exclusive)"
        )

        # One microsecond before must include
        just_before = datetime(2024, 6, 1, 20, 59, 59, 999999, tzinfo=UTC)
        result_before = as_of(conn, just_before)
        assert "tz-claim-c" in {c.claim_id for c in result_before}, (
            "Claim must be INCLUDED one microsecond before UTC-equivalent valid_to"
        )

    def test_naive_datetime_treated_as_utc(self, tmp_path: Path) -> None:
        """A naive datetime in frontmatter (no offset) must be treated as UTC.

        valid_from: 2024-06-01T00:00:00  (no offset) == 2024-06-01T00:00:00Z
        """
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_naive.md",
            "---\n"
            "id: naive-claim\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "statement: Naive datetime test\n"
            "valid_from: 2024-06-01T00:00:00\n"
            "---\n"
            "# Naive TZ test\n",
        )
        _index(conn, vault)

        at_utc = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
        result = as_of(conn, at_utc)
        assert "naive-claim" in {c.claim_id for c in result}, (
            "Naive datetime valid_from must be treated as UTC and included at its instant"
        )

        before_utc = datetime(2024, 5, 31, 23, 59, 59, tzinfo=UTC)
        result_before = as_of(conn, before_utc)
        assert "naive-claim" not in {c.claim_id for c in result_before}, (
            "Naive datetime valid_from must exclude claim before its UTC-equivalent instant"
        )


# ---------------------------------------------------------------------------
# Fix B — End-to-end supersession via frontmatter id
# ---------------------------------------------------------------------------

class TestEndToEndSupersession:
    """Regression for Fix B: claim_id == frontmatter id so supersedes references resolve."""

    def test_superseded_by_populated_after_index(self, tmp_path: Path) -> None:
        """After indexing, claim-a's superseded_by must be set to claim-b's id."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_a.md",
            "---\n"
            "id: claim-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Istanbul\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Claim A\n",
        )
        _write(
            vault,
            "claim_b.md",
            "---\n"
            "id: claim-b\n"
            "type: claim\n"
            "created: 2025-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Ankara\n"
            "valid_from: 2025-01-01T00:00:00+00:00\n"
            "supersedes: claim-a\n"
            "---\n"
            "# Claim B\n",
        )
        _index(conn, vault)

        row = conn.execute(
            "SELECT superseded_by FROM claims WHERE claim_id = 'claim-a'"
        ).fetchone()
        assert row is not None, "claim-a must exist in the claims table"
        assert row[0] == "claim-b", (
            f"superseded_by of claim-a must be 'claim-b', got {row[0]!r}"
        )

    def test_as_of_after_superseder_valid_from_returns_superseder_only(
        self, tmp_path: Path
    ) -> None:
        """at 2025-06-01: only claim-b is live; claim-a is dynamically shadowed."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_a.md",
            "---\n"
            "id: claim-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Istanbul\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Claim A\n",
        )
        _write(
            vault,
            "claim_b.md",
            "---\n"
            "id: claim-b\n"
            "type: claim\n"
            "created: 2025-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Ankara\n"
            "valid_from: 2025-01-01T00:00:00+00:00\n"
            "supersedes: claim-a\n"
            "---\n"
            "# Claim B\n",
        )
        _index(conn, vault)

        at_2025 = datetime(2025, 6, 1, tzinfo=UTC)
        result = as_of(conn, at_2025)
        ids = {c.claim_id for c in result}
        assert "claim-b" in ids, "claim-b must be live at 2025-06-01"
        assert "claim-a" not in ids, "claim-a must be shadowed by claim-b at 2025-06-01"

    def test_as_of_before_superseder_valid_from_returns_original_only(
        self, tmp_path: Path
    ) -> None:
        """at 2024-06-01 (before claim-b's valid_from 2025-01-01): only claim-a is live."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_a.md",
            "---\n"
            "id: claim-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Istanbul\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Claim A\n",
        )
        _write(
            vault,
            "claim_b.md",
            "---\n"
            "id: claim-b\n"
            "type: claim\n"
            "created: 2025-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Ankara\n"
            "valid_from: 2025-01-01T00:00:00+00:00\n"
            "supersedes: claim-a\n"
            "---\n"
            "# Claim B\n",
        )
        _index(conn, vault)

        at_2024 = datetime(2024, 6, 1, tzinfo=UTC)
        result = as_of(conn, at_2024)
        ids = {c.claim_id for c in result}
        assert "claim-a" in ids, "claim-a must be live at 2024-06-01 (before superseder)"
        assert "claim-b" not in ids, (
            "claim-b must NOT be live at 2024-06-01 (before its valid_from)"
        )


# ---------------------------------------------------------------------------
# Fix C — Ambiguous overlay must not flag resolved supersessions
# ---------------------------------------------------------------------------

class TestAmbiguousOverlayExcludesResolvedSupersessions:
    """Regression for Fix C: ambiguous_claim_ids at a post-supersession time must
    not flag the superseded/superseder pair as a live contradiction."""

    def test_resolved_supersession_not_in_ambiguous_set(self, tmp_path: Path) -> None:
        """After claim-b supersedes claim-a (same claim_key, later valid_from),
        at a time when claim-b is fully active, the pair must NOT appear in
        ambiguous_claim_ids because claim-a is dynamically shadowed."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "claim_a.md",
            "---\n"
            "id: sup-claim-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Istanbul\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Claim A\n",
        )
        _write(
            vault,
            "claim_b.md",
            "---\n"
            "id: sup-claim-b\n"
            "type: claim\n"
            "created: 2025-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Ankara\n"
            "valid_from: 2025-01-01T00:00:00+00:00\n"
            "supersedes: sup-claim-a\n"
            "---\n"
            "# Claim B\n",
        )
        _index(conn, vault)

        # At a time when claim-b is fully active, claim-a is shadowed.
        # The pair must NOT be flagged as an AMBIGUOUS contradiction.
        at_resolved = datetime(2025, 6, 1, tzinfo=UTC)
        ids = ambiguous_claim_ids(conn, at=at_resolved)
        assert "sup-claim-a" not in ids, (
            "Superseded claim-a must NOT appear in ambiguous set at post-supersession time"
        )
        assert "sup-claim-b" not in ids, (
            "Superseder claim-b must NOT appear in ambiguous set when it is the sole live claim"
        )

    def test_genuine_contradiction_is_flagged(self, tmp_path: Path) -> None:
        """Two claims with overlapping validity windows and the same claim_key
        but NO supersedes relationship must still be flagged as AMBIGUOUS."""
        conn, vault = _vault(tmp_path)
        _write(
            vault,
            "conflict_a.md",
            "---\n"
            "id: conflict-a\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Conflict A\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Conflict A\n",
        )
        _write(
            vault,
            "conflict_b.md",
            "---\n"
            "id: conflict-b\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "claim_key: user.city\n"
            "statement: Conflict B\n"
            "valid_from: 2024-01-01T00:00:00+00:00\n"
            "---\n"
            "# Conflict B\n",
        )
        _index(conn, vault)

        # Both are live and overlapping at any time — no supersedes link.
        at = datetime(2024, 6, 1, tzinfo=UTC)
        ids = ambiguous_claim_ids(conn, at=at)
        assert "conflict-a" in ids, "conflict-a must be in ambiguous set (genuine contradiction)"
        assert "conflict-b" in ids, "conflict-b must be in ambiguous set (genuine contradiction)"
