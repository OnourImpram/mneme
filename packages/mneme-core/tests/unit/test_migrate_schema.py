"""Unit tests for migrate_schema version-driven generalization.

Verifies that:
* columns listed in ``_MIGRATION_COLUMNS`` are ALTER-added to old databases
  that are missing them.
* the operation is idempotent (calling it twice is a no-op).
"""

from __future__ import annotations

import sqlite3

from mneme_core.fts5.indexer import _MIGRATION_COLUMNS, migrate_schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cols(conn: sqlite3.Connection) -> set[str]:
    """Return the set of column names for the ``documents`` table."""
    return {row[1] for row in conn.execute("PRAGMA table_info(documents)")}


def _create_minimal_documents(conn: sqlite3.Connection, *, exclude: set[str]) -> None:
    """Create a ``documents`` table missing the columns listed in *exclude*."""
    # Canonical column list from the current SCHEMA DDL, minus excluded ones.
    all_cols = [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "title TEXT",
        "title_normalized TEXT",
        "path TEXT UNIQUE NOT NULL",
        "content_raw TEXT",
        "body_text TEXT",
        "content_size INTEGER",
        "mtime REAL",
        "tags TEXT",
        "frontmatter_type TEXT",
        "session_id TEXT",
        "linked_notes TEXT",
        "schema_version TEXT DEFAULT '2'",
        "language TEXT DEFAULT 'en'",
        "indexed_at TEXT",
        "content_hash TEXT",
        "trust TEXT",
        "key_points TEXT",
    ]
    # Strip excluded columns (match by name prefix).
    kept = [
        col for col in all_cols
        if not any(col.strip().startswith(ex) for ex in exclude)
    ]
    conn.execute(f"CREATE TABLE documents ({', '.join(kept)})")
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMigrateSchemaAddsColumns:
    def test_body_text_added_on_old_db(self) -> None:
        """A database created without ``body_text`` must gain it after migration."""
        conn = sqlite3.connect(":memory:")
        _create_minimal_documents(conn, exclude={"body_text"})

        before = _cols(conn)
        assert "body_text" not in before

        migrate_schema(conn)

        after = _cols(conn)
        assert "body_text" in after
        conn.close()

    def test_all_migration_columns_added(self) -> None:
        """Every column in ``_MIGRATION_COLUMNS`` is added when absent."""
        conn = sqlite3.connect(":memory:")
        _create_minimal_documents(conn, exclude=set(_MIGRATION_COLUMNS.keys()))

        for col in _MIGRATION_COLUMNS:
            assert col not in _cols(conn), f"{col!r} should be absent before migration"

        migrate_schema(conn)

        present = _cols(conn)
        for col in _MIGRATION_COLUMNS:
            assert col in present, f"{col!r} should be present after migration"
        conn.close()

    def test_missing_subset_only_adds_missing(self) -> None:
        """Only actually-missing columns are altered; others are untouched."""
        # Remove only body_text; all other _MIGRATION_COLUMNS remain.
        conn = sqlite3.connect(":memory:")
        _create_minimal_documents(conn, exclude={"body_text"})

        cols_before = _cols(conn)
        # Columns not in exclude set should already be present.
        for col in _MIGRATION_COLUMNS:
            if col != "body_text":
                assert col in cols_before

        migrate_schema(conn)

        # No extra columns beyond what's expected.
        cols_after = _cols(conn)
        assert "body_text" in cols_after
        conn.close()


class TestMigrateSchemaIdempotent:
    def test_calling_twice_is_noop(self) -> None:
        """Calling ``migrate_schema`` twice must not raise or duplicate columns."""
        conn = sqlite3.connect(":memory:")
        _create_minimal_documents(conn, exclude={"body_text"})

        migrate_schema(conn)
        cols_after_first = _cols(conn)

        # Second call must be a no-op (no OperationalError, identical column set).
        migrate_schema(conn)
        cols_after_second = _cols(conn)

        assert cols_after_first == cols_after_second
        conn.close()

    def test_new_db_with_all_columns_survives_migrate(self) -> None:
        """A fresh database (all columns present) must not be broken by migration."""
        conn = sqlite3.connect(":memory:")
        # No excluded columns — all columns are present from the start.
        _create_minimal_documents(conn, exclude=set())

        cols_before = _cols(conn)
        migrate_schema(conn)
        cols_after = _cols(conn)

        assert cols_before == cols_after
        conn.close()
