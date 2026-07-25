"""Integration tests for P6 (locale profile persistence) and prune-overflow fix.

P6 tests:
- index_meta table is created by ensure_schema.
- The normalization_profile key is written with the correct value for each
  configured normalizer (identity, tr-cldr, tr-ascii-fold).
- read_index_meta returns the stored value and returns None for missing keys.
- read_index_meta soft-degrades (returns None) on old databases without the
  index_meta table.

prune-overflow tests:
- A full-pass prune over a vault with > 999 files does not raise
  'too many SQL variables' and still removes ghost rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mneme_core.fts5.indexer import (
    IndexerConfig,
    _identity,
    _profile_for_normalizer,
    ensure_schema,
    index_vault,
    read_index_meta,
)
from mneme_core.fts5.locale.tr import (
    normalize_tr,
    normalize_tr_ascii_fold,
    normalize_tr_ascii_fold_for_fts,
    normalize_tr_for_fts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(root: Path, n: int) -> None:
    """Write *n* minimal markdown files under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / f"note_{i:05d}.md").write_text(
            f"---\nid: note-{i}\ntype: topic\n---\n# Note {i}\nbody {i}\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# P6: index_meta table creation
# ---------------------------------------------------------------------------

class TestIndexMetaTableCreation:
    def test_ensure_schema_creates_index_meta_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "index_meta" in tables
        conn.close()

    def test_index_meta_has_key_and_value_columns(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        col_names = {
            row[1] for row in conn.execute("PRAGMA table_info(index_meta)")
        }
        assert "key" in col_names
        assert "value" in col_names
        conn.close()


# ---------------------------------------------------------------------------
# P6: profile written during index_vault
# ---------------------------------------------------------------------------

class TestNormalizationProfileWritten:
    """index_vault must persist the normalization_profile into index_meta."""

    def _index_empty_vault(
        self,
        tmp_path: Path,
        normalizer: object,
    ) -> sqlite3.Connection:
        vault = tmp_path / "vault"
        vault.mkdir()
        # One file so the pass is non-trivial.
        (vault / "a.md").write_text(
            "---\nid: a\ntype: topic\n---\n# A\nbody\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(
            vault_root=vault,
            db_path=tmp_path / "fts.db",
            normalize=normalizer,  # type: ignore[arg-type]
        )
        index_vault(conn, cfg)
        return conn

    def test_identity_normalizer_stores_identity_profile(
        self, tmp_path: Path
    ) -> None:
        conn = self._index_empty_vault(tmp_path, _identity)
        value = read_index_meta(conn, "normalization_profile")
        assert value == "identity"
        assert read_index_meta(conn, "ascii_normalization_profile") == "disabled"
        conn.close()

    def test_tr_normalizer_stores_tr_cldr_profile(
        self, tmp_path: Path
    ) -> None:
        conn = self._index_empty_vault(tmp_path, normalize_tr)
        value = read_index_meta(conn, "normalization_profile")
        assert value == "tr-cldr"
        conn.close()

    def test_ascii_fold_normalizer_stores_tr_ascii_fold_profile(
        self, tmp_path: Path
    ) -> None:
        conn = self._index_empty_vault(tmp_path, normalize_tr_ascii_fold)
        value = read_index_meta(conn, "normalization_profile")
        assert value == "tr-ascii-fold"
        conn.close()

    def test_dual_key_index_stores_ascii_profile(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "izmir.md").write_text(
            "# \u0130zmir\n\u0130zmir liman\u0131 a\u00e7\u0131k.\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        index_vault(
            conn,
            IndexerConfig(
                vault_root=vault,
                db_path=tmp_path / "fts.db",
                normalize=normalize_tr,
                normalize_for_fts=normalize_tr_for_fts,
                normalize_ascii=normalize_tr_ascii_fold,
                normalize_ascii_for_fts=normalize_tr_ascii_fold_for_fts,
            ),
        )

        assert read_index_meta(conn, "normalization_profile") == "tr-cldr"
        assert (
            read_index_meta(conn, "ascii_normalization_profile")
            == "tr-ascii-fold"
        )
        assert conn.execute("SELECT count(*) FROM documents_ascii_fts").fetchone() == (
            1,
        )
        conn.close()

    def test_profile_is_overwritten_on_second_index(
        self, tmp_path: Path
    ) -> None:
        """Re-indexing with a different locale must update the stored profile."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text(
            "---\nid: a\ntype: topic\n---\n# A\nbody\n", encoding="utf-8"
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        db = tmp_path / "fts.db"

        # First pass: identity
        cfg_en = IndexerConfig(vault_root=vault, db_path=db, normalize=_identity)
        index_vault(conn, cfg_en)
        assert read_index_meta(conn, "normalization_profile") == "identity"

        # Second pass: Turkish
        cfg_tr = IndexerConfig(vault_root=vault, db_path=db, normalize=normalize_tr)
        index_vault(conn, cfg_tr)
        assert read_index_meta(conn, "normalization_profile") == "tr-cldr"

        conn.close()


# ---------------------------------------------------------------------------
# P6: read_index_meta helper
# ---------------------------------------------------------------------------

class TestReadIndexMeta:
    def test_returns_stored_value(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO index_meta(key, value) VALUES('normalization_profile', 'tr-cldr')"
        )
        conn.commit()
        assert read_index_meta(conn, "normalization_profile") == "tr-cldr"
        conn.close()

    def test_returns_none_for_missing_key(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
        assert read_index_meta(conn, "normalization_profile") is None
        conn.close()

    def test_soft_degrades_on_old_db_without_table(self) -> None:
        """read_index_meta must return None (not raise) when index_meta is absent."""
        conn = sqlite3.connect(":memory:")
        # No index_meta table — simulate a pre-P6 database.
        result = read_index_meta(conn, "normalization_profile")
        assert result is None
        conn.close()


# ---------------------------------------------------------------------------
# P6: _profile_for_normalizer unit
# ---------------------------------------------------------------------------

class TestProfileForNormalizer:
    def test_identity_maps_to_identity(self) -> None:
        assert _profile_for_normalizer(_identity) == "identity"

    def test_normalize_tr_maps_to_tr_cldr(self) -> None:
        assert _profile_for_normalizer(normalize_tr) == "tr-cldr"

    def test_normalize_tr_ascii_fold_maps_to_tr_ascii_fold(self) -> None:
        assert _profile_for_normalizer(normalize_tr_ascii_fold) == "tr-ascii-fold"

    def test_unknown_callable_falls_back_to_identity(self) -> None:
        def my_custom_normalizer(s: str) -> str:
            return s.lower()

        assert _profile_for_normalizer(my_custom_normalizer) == "identity"


# ---------------------------------------------------------------------------
# prune-overflow: > 999 files does not hit SQLITE_MAX_VARIABLE_NUMBER
# ---------------------------------------------------------------------------

class TestPruneOverflow:
    """Full-pass prune must work for vaults with > 999 markdown files.

    The original NOT IN (?, ?, ...) clause with one bind per live path would
    hit SQLite's SQLITE_MAX_VARIABLE_NUMBER limit (~999 by default) and raise
    sqlite3.OperationalError: too many SQL variables. The TEMP table strategy
    avoids this entirely.
    """

    def test_prune_large_vault_no_sql_variable_error(
        self, tmp_path: Path
    ) -> None:
        """Index 1100 files, delete 100, re-index: no error, ghost rows removed."""
        vault = tmp_path / "vault"
        _make_vault(vault, 1100)

        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")

        # First full pass — indexes all 1100 files.
        stats1 = index_vault(conn, cfg)
        assert stats1.indexed == 1100

        # Delete the first 100 files (note_00000.md … note_00099.md).
        ghost_rels = [f"note_{i:05d}.md" for i in range(100)]
        for rel in ghost_rels:
            (vault / rel).unlink()

        # Second full pass — must not raise and must prune the 100 deleted files.
        stats2 = index_vault(conn, cfg)
        assert stats2.indexed == 1000

        remaining = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert remaining == 1000

        # All 100 pruned paths must be absent from documents.
        for rel in ghost_rels:
            row = conn.execute(
                "SELECT id FROM documents WHERE path=?", (rel,)
            ).fetchone()
            assert row is None, f"Ghost row {rel!r} was not pruned"

        conn.close()

    def test_prune_exactly_999_files_no_error(self, tmp_path: Path) -> None:
        """Edge case: exactly 999 live files (old bound would pass; TEMP must too)."""
        vault = tmp_path / "vault"
        _make_vault(vault, 1000)

        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")

        stats1 = index_vault(conn, cfg)
        assert stats1.indexed == 1000

        # Delete one to leave exactly 999 live.
        (vault / "note_00999.md").unlink()
        stats2 = index_vault(conn, cfg)
        assert stats2.indexed == 999
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 999

        conn.close()

    def test_prune_removes_ghost_from_fts_on_large_vault(
        self, tmp_path: Path
    ) -> None:
        """Ghost rows must be removed from documents_fts, not just documents."""
        vault = tmp_path / "vault"
        _make_vault(vault, 1005)

        # Plant a uniquely searchable token in the file we will delete.
        ghost_file = vault / "ghost_unique.md"
        ghost_file.write_text(
            "---\nid: ghost\ntype: topic\n---\n# Ghost\nbody PRUNE_OVERFLOW_TOKEN\n",
            encoding="utf-8",
        )

        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")

        index_vault(conn, cfg)

        # Confirm the unique token is searchable before deletion.
        hits_before = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("PRUNE_OVERFLOW_TOKEN",),
        ).fetchall()
        assert len(hits_before) == 1

        ghost_file.unlink()
        index_vault(conn, cfg)

        hits_after = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("PRUNE_OVERFLOW_TOKEN",),
        ).fetchall()
        assert hits_after == [], "Ghost FTS row was not pruned on large vault"

        conn.close()
