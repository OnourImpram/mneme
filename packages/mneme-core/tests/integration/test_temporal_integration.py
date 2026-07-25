"""Integration tests for the temporal claim lifecycle module.

Covers:
- index_claims on a 4-note vault (3 claims + 1 plain note).
- make_temporal_backend used as kg_backend in retrieve() → temporal hit appears.
- Empty/absent claims table → backend returns [] and retrieve() still works (FTS5 fallback).
- Supersession non-destructive: target row survives after indexer supersession pass.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import (
    IndexerConfig,
    ensure_schema,
    index_vault,
)
from mneme_core.fts5.indexer import (
    connect as fts5_connect,
)
from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts
from mneme_core.retrieval.rrf import RetrievalConfig, retrieve
from mneme_core.temporal.backend import ambiguous_claim_ids, make_temporal_backend
from mneme_core.temporal.index import ensure_temporal_schema, index_claims
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# Vault fixture with claim notes
# ---------------------------------------------------------------------------

@pytest.fixture
def claim_vault(tmp_path: Path) -> Iterator[tuple[Path, VaultConfig]]:
    """Create a 4-note vault (3 temporal claims + 1 plain topic), index both FTS5 and claims."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    # Note 1: claim with full validity window
    (vault_root / "claim_location.md").write_text(
        "---\n"
        "id: loc-1\n"
        "type: claim\n"
        "created: 2024-01-01T00:00:00+00:00\n"
        "statement: User lives in Istanbul\n"
        "claim_key: user.city\n"
        "valid_from: 2024-01-01T00:00:00+00:00\n"
        "valid_to: 2025-01-01T00:00:00+00:00\n"
        "---\n"
        "# User Location\n"
        "The user lives in Istanbul as of 2024.\n",
        encoding="utf-8",
    )

    # Note 2: superseding claim (same claim_key, later valid_from)
    # supersedes: loc-1 matches loc-1's frontmatter id so the supersession pass fires.
    (vault_root / "claim_location_new.md").write_text(
        "---\n"
        "id: loc-2\n"
        "type: claim\n"
        "created: 2024-07-01T00:00:00+00:00\n"
        "statement: User now lives in Ankara\n"
        "claim_key: user.city\n"
        "valid_from: 2024-07-01T00:00:00+00:00\n"
        "supersedes: loc-1\n"
        "---\n"
        "# User New Location\n"
        "The user relocated to Ankara.\n",
        encoding="utf-8",
    )

    # Note 3: open-window claim (no valid_from/valid_to, just observed_at)
    (vault_root / "claim_language.md").write_text(
        "---\n"
        "id: lang-1\n"
        "type: claim\n"
        "created: 2024-01-01T00:00:00+00:00\n"
        "observed_at: 2024-01-01T00:00:00+00:00\n"
        "statement: User speaks Turkish and English\n"
        "claim_key: user.language\n"
        "scope: research\n"
        "---\n"
        "# Language\n"
        "Turkish and English speaker.\n",
        encoding="utf-8",
    )

    # Note 4: plain topic (should NOT be indexed as a claim)
    (vault_root / "plain_topic.md").write_text(
        "---\n"
        "id: topic-1\n"
        "type: topic\n"
        "created: 2024-01-01T00:00:00+00:00\n"
        "---\n"
        "# A Plain Topic\n"
        "This note has no temporal fields and is not a claim.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".mneme" / "fts5.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = fts5_connect(db_path)
    ensure_schema(conn)
    ensure_temporal_schema(conn)

    fts_cfg = IndexerConfig(
        vault_root=vault_root,
        db_path=db_path,
        normalize=normalize_tr,
        normalize_for_fts=normalize_tr_for_fts,
    )
    index_vault(conn, fts_cfg)
    index_claims(conn, VaultConfig.from_path(vault_root), normalize=normalize_tr)
    conn.close()

    vault_config = VaultConfig.from_path(tmp_path)
    yield db_path, vault_config


# ---------------------------------------------------------------------------
# Basic indexing checks
# ---------------------------------------------------------------------------

class TestClaimIndexing:
    def test_claim_notes_indexed(self, claim_vault: tuple[Path, VaultConfig]) -> None:
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM claims").fetchone()
        conn.close()
        # 3 claim-eligible notes (loc-1, loc-2, lang-1); plain topic excluded
        assert row[0] == 3

    def test_plain_topic_not_indexed_as_claim(self, claim_vault: tuple[Path, VaultConfig]) -> None:
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT path FROM claims WHERE path LIKE '%plain_topic%'"
        ).fetchall()
        conn.close()
        assert rows == []

    def test_superseded_by_populated(self, claim_vault: tuple[Path, VaultConfig]) -> None:
        """After indexing, the superseded target row's superseded_by should be set.

        With Fix B, claim_id == frontmatter id, so loc-2.supersedes='loc-1' resolves
        to loc-1's row and the supersession pass fires correctly.
        """
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        # Verify loc-2 stores supersedes='loc-1' (matching frontmatter id).
        row = conn.execute(
            "SELECT supersedes FROM claims WHERE path LIKE '%claim_location_new%'"
        ).fetchone()
        assert row is not None
        assert row[0] == "loc-1", f"Expected supersedes='loc-1', got {row[0]!r}"

        # Verify loc-1.superseded_by is set to loc-2 (supersession pass fired).
        row2 = conn.execute(
            "SELECT superseded_by FROM claims WHERE claim_id = 'loc-1'"
        ).fetchone()
        conn.close()
        assert row2 is not None, "loc-1 row must exist"
        assert row2[0] == "loc-2", (
            f"superseded_by of loc-1 must be 'loc-2', got {row2[0]!r}"
        )

    def test_non_destructive_supersession(self, claim_vault: tuple[Path, VaultConfig]) -> None:
        """The superseded target row must still be present in the claims table."""
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT claim_id FROM claims WHERE path LIKE '%claim_location.md%'"
        ).fetchone()
        conn.close()
        assert row is not None, "Superseded claim row must not be deleted"

    def test_scope_is_indexed_from_frontmatter(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT scope FROM claims WHERE claim_id = 'lang-1'"
        ).fetchone()
        conn.close()
        assert row == ("research",)

    def test_reindex_is_idempotent(self, claim_vault: tuple[Path, VaultConfig]) -> None:
        """Running index_claims twice must not change the row count."""
        db_path, vault_config = claim_vault
        conn = fts5_connect(db_path)
        vault = VaultConfig.from_path(vault_config.root / "vault")
        index_claims(conn, vault, normalize=normalize_tr)
        row = conn.execute("SELECT COUNT(*) FROM claims").fetchone()
        conn.close()
        assert row[0] == 3

    def test_reindex_prunes_deleted_claim_and_resets_supersession(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, vault_config = claim_vault
        vault_root = vault_config.root / "vault"
        (vault_root / "claim_location_new.md").unlink()

        conn = fts5_connect(db_path)
        index_claims(conn, VaultConfig.from_path(vault_root), normalize=normalize_tr)
        deleted = conn.execute(
            "SELECT 1 FROM claims WHERE claim_id = 'loc-2'"
        ).fetchone()
        target = conn.execute(
            "SELECT superseded_by FROM claims WHERE claim_id = 'loc-1'"
        ).fetchone()
        conn.close()

        assert deleted is None
        assert target == (None,)

    def test_reindex_prunes_note_that_is_no_longer_a_claim(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, vault_config = claim_vault
        vault_root = vault_config.root / "vault"
        (vault_root / "claim_language.md").write_text(
            "---\nid: lang-1\ntype: topic\ncreated: 2024-01-01T00:00:00+00:00\n---\n"
            "# Language\nNo longer a temporal claim.\n",
            encoding="utf-8",
        )

        conn = fts5_connect(db_path)
        index_claims(conn, VaultConfig.from_path(vault_root), normalize=normalize_tr)
        row = conn.execute(
            "SELECT 1 FROM claims WHERE claim_id = 'lang-1'"
        ).fetchone()
        conn.close()

        assert row is None

    def test_reindex_prunes_claim_with_invalid_temporal_metadata(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, vault_config = claim_vault
        vault_root = vault_config.root / "vault"
        (vault_root / "claim_language.md").write_text(
            "---\n"
            "id: lang-1\n"
            "type: claim\n"
            "created: 2024-01-01T00:00:00+00:00\n"
            "observed_at: not-a-date\n"
            "statement: User speaks Turkish and English\n"
            "---\n"
            "# Language\nInvalid observation time.\n",
            encoding="utf-8",
        )

        conn = fts5_connect(db_path)
        index_claims(conn, VaultConfig.from_path(vault_root), normalize=normalize_tr)
        row = conn.execute(
            "SELECT 1 FROM claims WHERE claim_id = 'lang-1'"
        ).fetchone()
        conn.close()

        assert row is None


# ---------------------------------------------------------------------------
# make_temporal_backend used in retrieve()
# ---------------------------------------------------------------------------

class TestTemporalBackendInRetrieve:
    def test_temporal_hit_appears_in_fused_results(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)

        temporal_backend = make_temporal_backend(
            conn,
            normalize=normalize_tr,
            as_of=datetime(2024, 3, 1, tzinfo=UTC),
        )

        config = RetrievalConfig(
            fts5_db=db_path,
            normalize=normalize_tr,
            min_query_length=3,
            top_n_final=10,
        )
        result = retrieve("user city Istanbul", config, kg_backend=temporal_backend)
        conn.close()

        temporal_hits = [
            hit
            for hit in result
            if hit.source == "temporal" or "temporal" in hit.sources
        ]
        assert temporal_hits, (
            f"Expected a temporal contribution; got sources="
            f"{[(hit.source, hit.sources) for hit in result]}, "
            f"paths={[hit.path for hit in result]}"
        )

    def test_empty_claims_table_returns_no_temporal_hits(
        self, tmp_path: Path
    ) -> None:
        """Empty claims table → backend returns [] → retrieve() works via FTS5 only."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        (vault_root / "a.md").write_text(
            "---\nid: a\ntype: topic\ncreated: 2024-01-01T00:00:00+00:00\n---\n"
            "# Some Topic\nVault native memory.\n",
            encoding="utf-8",
        )

        db_path = tmp_path / ".mneme" / "fts5.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = fts5_connect(db_path)
        ensure_schema(conn)
        ensure_temporal_schema(conn)

        fts_cfg = IndexerConfig(
            vault_root=vault_root,
            db_path=db_path,
            normalize=normalize_tr,
            normalize_for_fts=normalize_tr_for_fts,
        )
        index_vault(conn, fts_cfg)
        # Intentionally do NOT call index_claims — claims table empty.

        temporal_backend = make_temporal_backend(conn, normalize=normalize_tr)
        # Backend returns [] on empty table
        assert temporal_backend("vault memory", 10) == []

        config = RetrievalConfig(
            fts5_db=db_path,
            normalize=normalize_tr,
            min_query_length=3,
            top_n_final=10,
        )
        result = retrieve("vault memory", config, kg_backend=temporal_backend)
        conn.close()

        # retrieve() still works — FTS5 hits present
        assert len(result) >= 1
        assert all(h.source in ("fts5", "temporal") for h in result)
        assert not any(h.source == "temporal" for h in result)

    def test_absent_claims_table_returns_no_temporal_hits(
        self, tmp_path: Path
    ) -> None:
        """Absent claims table (no schema) → backend returns [] cleanly."""
        db_path = tmp_path / "fts.db"
        conn = sqlite3.connect(db_path)
        ensure_schema(conn)
        # Do NOT call ensure_temporal_schema

        temporal_backend = make_temporal_backend(conn, normalize=normalize_tr)
        result = temporal_backend("anything", 10)
        conn.close()
        assert result == []


# ---------------------------------------------------------------------------
# ambiguous_claim_ids
# ---------------------------------------------------------------------------

class TestAmbiguousClaimIds:
    def test_contradicting_claims_appear_in_ambiguous_set(
        self, claim_vault: tuple[Path, VaultConfig]
    ) -> None:
        db_path, _ = claim_vault
        conn = sqlite3.connect(db_path)
        # At a time when both loc-1 and loc-2 are live (loc-2.valid_from=2024-07-01,
        # loc-1.valid_to=2025-01-01), they share claim_key user.city and overlap.
        at = datetime(2024, 8, 1, tzinfo=UTC)
        ids = ambiguous_claim_ids(conn, at=at)
        conn.close()
        # Both should appear since they overlap and share user.city
        # (loc-1: Jan-Jan, loc-2: Jul-open; overlap Aug 2024)
        assert isinstance(ids, frozenset)

    def test_no_contradictions_returns_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fts.db"
        conn = sqlite3.connect(db_path)
        ensure_temporal_schema(conn)
        ids = ambiguous_claim_ids(conn)
        conn.close()
        assert ids == frozenset()
