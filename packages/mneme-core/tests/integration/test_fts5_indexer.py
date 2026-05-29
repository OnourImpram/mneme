"""Integration tests for the FTS5 indexer.

These tests use ``sqlite3.connect(":memory:")`` so they need no
production database. A synthetic vault is built in ``tmp_path`` per
test, indexed, and queried end-to-end.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import (
    DEFAULT_EXCLUDE_PATTERNS,
    SCHEMA_VERSION,
    BenchmarkResult,
    IndexerConfig,
    benchmark_queries,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts


@pytest.fixture
def in_memory_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def populated_vault(tmp_path: Path) -> Path:
    """Build a small synthetic vault with frontmatter, wikilinks, and tags."""
    (tmp_path / "sessions" / "2026-05-19").mkdir(parents=True)

    file_a = tmp_path / "sessions" / "2026-05-19" / "a.md"
    file_a.write_text(
        "---\n"
        "id: a\n"
        "type: session\n"
        "tags: foo bar\n"
        "session_id: sess-1\n"
        "---\n"
        "# First Document\n\n"
        "Content about retrieval. See [[b]] for more.\n",
        encoding="utf-8",
    )

    file_b = tmp_path / "b.md"
    file_b.write_text(
        "---\n"
        "id: b\n"
        "type: topic\n"
        "tags: baz\n"
        "---\n"
        "# Second Document\n\nSecond body about indexing.\n",
        encoding="utf-8",
    )

    # Excluded by default through the /.git/ pattern.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.md").write_text("ignored", encoding="utf-8")

    return tmp_path


class TestSchemaEnsure:
    def test_creates_documents_table(self, in_memory_conn: sqlite3.Connection) -> None:
        names = {
            row[0]
            for row in in_memory_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "documents" in names

    def test_creates_fts_virtual_table(
        self, in_memory_conn: sqlite3.Connection
    ) -> None:
        names = {
            row[0]
            for row in in_memory_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "documents_fts" in names


class TestIndexVault:
    def test_indexes_two_files(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(
            vault_root=populated_vault,
            db_path=tmp_path / "fts.db",
            normalize=normalize_tr,
            normalize_for_fts=normalize_tr_for_fts,
        )
        stats = index_vault(in_memory_conn, cfg)
        assert stats.indexed == 2

    def test_skips_excluded_git_dir(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        stats = index_vault(in_memory_conn, cfg)
        assert stats.skipped_excluded == 1

    def test_search_finds_indexed_content(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        rows = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("retrieval",),
        ).fetchall()
        assert len(rows) == 1

    def test_extracts_h1_as_title(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        titles = {
            r[0] for r in in_memory_conn.execute("SELECT title FROM documents")
        }
        assert titles == {"First Document", "Second Document"}

    def test_parses_frontmatter_type(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        types = {
            r[0]
            for r in in_memory_conn.execute("SELECT frontmatter_type FROM documents")
        }
        assert types == {"session", "topic"}

    def test_parses_session_id(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        sessions = {
            r[0]
            for r in in_memory_conn.execute(
                "SELECT session_id FROM documents WHERE session_id != ''"
            )
        }
        assert sessions == {"sess-1"}

    def test_idempotent_reindex(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        stats1 = index_vault(in_memory_conn, cfg)
        stats2 = index_vault(in_memory_conn, cfg)
        assert stats1.indexed == stats2.indexed
        count = in_memory_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 2

    def test_incremental_skips_future_since(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        future = time.time() + 60
        stats = index_vault(in_memory_conn, cfg, since_mtime=future)
        assert stats.indexed == 0
        assert stats.skipped_unchanged == 2

    def test_extracts_wikilinks(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        row = in_memory_conn.execute(
            "SELECT linked_notes FROM documents WHERE path='sessions/2026-05-19/a.md'"
        ).fetchone()
        assert row is not None
        assert "b" in row[0]

    def test_indexes_block_style_tags(
        self,
        in_memory_conn: sqlite3.Connection,
        tmp_path: Path,
    ) -> None:
        """Block-style YAML tag lists must be indexed, not silently dropped.

        The migration tool and ``yaml.safe_dump`` both emit tags as a
        block sequence (``tags:\\n  - a\\n  - b``). A naive line-splitter
        stored an empty string for such tags, so tag tokens never reached
        the FTS index. This is a regression guard for that gap.
        """
        vault = tmp_path / "v"
        vault.mkdir()
        (vault / "obs.md").write_text(
            "---\n"
            "id: cm-obs-1\n"
            "type: observation\n"
            "tags:\n"
            "  - psychology\n"
            "  - retrieval\n"
            "---\n"
            "# Doc\n\nbody\n",
            encoding="utf-8",
        )
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        row = in_memory_conn.execute(
            "SELECT tags, frontmatter_type FROM documents WHERE path='obs.md'"
        ).fetchone()
        assert row is not None
        assert "psychology" in row[0]
        assert "retrieval" in row[0]
        # The single-line key after the block list still parses correctly.
        assert row[1] == "observation"
        hits = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("psychology",),
        ).fetchall()
        assert len(hits) == 1


class TestTurkishNormalization:
    def test_case_insensitive_recall_with_dotted_capital_I(
        self, in_memory_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Canonical Turkish input (dotted capital I) recalls regardless of query case.

        Content uses the proper Turkish capital with a dot above (U+0130).
        Normalization folds it to plain Latin lowercase i (U+0069), and
        every reasonable case variant of the query produces the same key.
        """
        vault = tmp_path / "v"
        vault.mkdir()
        (vault / "tr.md").write_text(
            "---\nid: tr\ntype: session\n---\n"
            "# İstanbul Üniversitesi\n"
            "kıyaslama içerik\n",
            encoding="utf-8",
        )
        cfg = IndexerConfig(
            vault_root=vault,
            db_path=tmp_path / "fts.db",
            normalize=normalize_tr,
            normalize_for_fts=normalize_tr_for_fts,
        )
        index_vault(in_memory_conn, cfg)
        for q in ["istanbul", "İSTANBUL", "İstanbul"]:
            q_norm = normalize_tr(q)
            rows = in_memory_conn.execute(
                "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
                (q_norm,),
            ).fetchall()
            assert len(rows) == 1, f"query {q!r} (normalized {q_norm!r}) returned 0 rows"

    def test_dotless_and_dotted_i_remain_distinct(
        self, in_memory_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Dotted i (U+0069) and dotless i (U+0131) are semantically different.

        This is by design and matches the CLDR Turkish casing rules. A
        query for a dotted-i word must NOT match a document containing
        only the dotless-i form, and vice versa.
        """
        vault = tmp_path / "v"
        vault.mkdir()
        # Capital I in ASCII (no dot above) is the Turkish dotless capital,
        # which normalizes to dotless small i (U+0131).
        (vault / "dotless.md").write_text(
            "---\nid: x\ntype: session\n---\n# Istanbul\nbody\n",
            encoding="utf-8",
        )
        cfg = IndexerConfig(
            vault_root=vault,
            db_path=tmp_path / "fts.db",
            normalize=normalize_tr,
            normalize_for_fts=normalize_tr_for_fts,
        )
        index_vault(in_memory_conn, cfg)
        # The stored token is "ıstanbul" (dotless). A dotted-i query
        # should NOT match.
        q_dotted = normalize_tr("istanbul")
        rows_dotted = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            (q_dotted,),
        ).fetchall()
        assert len(rows_dotted) == 0, "dotted-i query unexpectedly matched dotless-i content"
        # A dotless-i query (or any capital I variant) should match.
        q_dotless = normalize_tr("İSTANBUL")  # İSTANBUL → "istanbul" (dotted)
        # That is the dotted form, also no match against dotless content.
        rows_caps_dotted = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            (q_dotless,),
        ).fetchall()
        assert len(rows_caps_dotted) == 0
        # Real dotless match: query has capital I (ASCII).
        q_dotless_real = normalize_tr("ISTANBUL")  # → "ıstanbul" (dotless)
        rows_dotless_match = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            (q_dotless_real,),
        ).fetchall()
        assert len(rows_dotless_match) == 1


class TestExcludePatterns:
    def test_default_excludes_dotgit(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        stats = index_vault(in_memory_conn, cfg)
        assert stats.skipped_excluded == 1

    def test_custom_pattern_replaces_defaults(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(
            vault_root=populated_vault,
            db_path=tmp_path / "fts.db",
            exclude_patterns=("/sessions/",),
        )
        stats = index_vault(in_memory_conn, cfg)
        assert stats.skipped_excluded == 1
        assert stats.indexed >= 1


class TestBenchmarkQueries:
    def test_returns_pass_on_in_memory(
        self,
        in_memory_conn: sqlite3.Connection,
        populated_vault: Path,
        tmp_path: Path,
    ) -> None:
        cfg = IndexerConfig(vault_root=populated_vault, db_path=tmp_path / "fts.db")
        index_vault(in_memory_conn, cfg)
        result = benchmark_queries(
            in_memory_conn,
            ["retrieval", "second", "topic"],
            pass_threshold_ms=1000.0,
        )
        assert isinstance(result, BenchmarkResult)
        assert result.queries == 3
        assert result.failed == 0
        assert result.pass_criterion_met

    def test_raises_on_empty_queries(
        self, in_memory_conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(RuntimeError):
            benchmark_queries(in_memory_conn, [])

    def test_with_turkish_normalizer(
        self, in_memory_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        vault = tmp_path / "v"
        vault.mkdir()
        (vault / "tr.md").write_text(
            "---\nid: tr\ntype: session\n---\n# İstanbul\nbody\n",
            encoding="utf-8",
        )
        cfg = IndexerConfig(
            vault_root=vault,
            db_path=tmp_path / "fts.db",
            normalize=normalize_tr,
            normalize_for_fts=normalize_tr_for_fts,
        )
        index_vault(in_memory_conn, cfg)
        result = benchmark_queries(
            in_memory_conn,
            ["istanbul", "İSTANBUL"],
            normalize=normalize_tr,
            pass_threshold_ms=1000.0,
        )
        assert result.queries == 2
        assert result.failed == 0


class TestFrontmatterDequote:
    """Phase J Day 3 regression: indexer must dequote YAML scalars.

    The migration tool emits ``type: 'observation'`` (YAML
    single-quoted). Prior to the fix, the indexer stored the literal
    including the quotes, so subsequent type-based filters did not
    match the canonical bare-word value the docs/VAULT.md spec
    promises.
    """

    def _index_vault_with_quoted_type(
        self,
        tmp_path: Path,
        type_literal: str,
    ) -> str | None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text(
            f"""---\nid: 'cm-obs-1'\ntype: {type_literal}\nschema_version: 1\n---\n# title\nbody""",
            encoding="utf-8",
        )
        db = tmp_path / "fts5.sqlite"
        conn = connect(db)
        try:
            ensure_schema(conn)
            index_vault(conn, IndexerConfig(vault_root=vault, db_path=db))
            row = conn.execute(
                "SELECT frontmatter_type FROM documents"
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def test_single_quoted_observation_stripped(self, tmp_path: Path) -> None:
        result = self._index_vault_with_quoted_type(tmp_path, "'observation'")
        assert result == "observation"

    def test_double_quoted_stripped(self, tmp_path: Path) -> None:
        result = self._index_vault_with_quoted_type(tmp_path, '"reference"')
        assert result == "reference"

    def test_unquoted_unchanged(self, tmp_path: Path) -> None:
        result = self._index_vault_with_quoted_type(tmp_path, "session_summary")
        assert result == "session_summary"

    def test_yaml_escaped_apostrophe_inside_single_quotes(
        self, tmp_path: Path
    ) -> None:
        # YAML single-quoted scalars escape the surrounding quote by
        # doubling: 'it''s fine' -> "it's fine".
        result = self._index_vault_with_quoted_type(tmp_path, "'it''s fine'")
        assert result == "it's fine"


class TestConstants:
    def test_schema_version_is_string_two(self) -> None:
        assert SCHEMA_VERSION == "2"

    def test_default_excludes_include_critical_dirs(self) -> None:
        assert "/.git/" in DEFAULT_EXCLUDE_PATTERNS
        assert "/.mneme/" in DEFAULT_EXCLUDE_PATTERNS
        assert "/node_modules/" in DEFAULT_EXCLUDE_PATTERNS


class TestPruneOnDelete:
    """P0-2: full-pass pruning removes rows for deleted markdown files."""

    def test_deleted_file_not_searchable_after_full_reindex(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        keep = vault / "keep.md"
        keep.write_text(
            "---\nid: keep\ntype: topic\n---\n# Keeper\nbody keeper\n",
            encoding="utf-8",
        )
        gone = vault / "gone.md"
        gone.write_text(
            "---\nid: gone\ntype: topic\n---\n# Gone\nbody uniquegonetoken\n",
            encoding="utf-8",
        )

        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")

        stats1 = index_vault(conn, cfg)
        assert stats1.indexed == 2

        # Verify the about-to-be-deleted file is searchable before deletion.
        hits_before = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("uniquegonetoken",),
        ).fetchall()
        assert len(hits_before) == 1

        # Delete the file from disk and re-index (full pass).
        gone.unlink()
        stats2 = index_vault(conn, cfg)
        assert stats2.indexed == 1

        # Row must be gone from documents.
        row = conn.execute(
            "SELECT id FROM documents WHERE path='gone.md'"
        ).fetchone()
        assert row is None

        # Content must no longer be searchable via FTS.
        hits_after = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("uniquegonetoken",),
        ).fetchall()
        assert len(hits_after) == 0

        conn.close()

    def test_incremental_pass_does_not_prune(self, tmp_path: Path) -> None:
        """An incremental (since_mtime > 0) pass must NOT prune stale rows."""
        vault = tmp_path / "vault"
        vault.mkdir()
        keep = vault / "keep.md"
        keep.write_text("---\nid: k\ntype: topic\n---\n# K\nbody\n", encoding="utf-8")
        gone = vault / "gone.md"
        gone.write_text(
            "---\nid: g\ntype: topic\n---\n# G\nbody uniqueincrtoken\n",
            encoding="utf-8",
        )

        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        index_vault(conn, cfg)

        gone.unlink()
        # Run incremental pass with since_mtime in the future so nothing
        # gets re-indexed; deleted file must remain in the index.
        import time as _time
        future = _time.time() + 60
        index_vault(conn, cfg, since_mtime=future)

        row = conn.execute(
            "SELECT id FROM documents WHERE path='gone.md'"
        ).fetchone()
        assert row is not None, "incremental pass must not prune deleted rows"

        conn.close()


class TestBodyText:
    """P1-7: body_text column stores document body without YAML frontmatter."""

    def test_body_text_excludes_frontmatter_keys(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text(
            "---\n"
            "id: secret-id\n"
            "session_id: sess-private\n"
            "type: session\n"
            "tags: alpha\n"
            "---\n"
            "# Real Title\n\n"
            "This is the actual body content.\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        index_vault(conn, cfg)

        row = conn.execute(
            "SELECT body_text FROM documents WHERE path='doc.md'"
        ).fetchone()
        assert row is not None
        body = row[0]

        # Frontmatter keys and values must not appear in body_text.
        assert "secret-id" not in body
        assert "sess-private" not in body
        assert "session_id" not in body

        # Body content must be present.
        assert "actual body content" in body

        conn.close()

    def test_body_text_equals_full_content_when_no_frontmatter(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "plain.md").write_text(
            "# Plain\n\nNo frontmatter here.\n", encoding="utf-8"
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        index_vault(conn, cfg)

        row = conn.execute(
            "SELECT body_text, content_raw FROM documents WHERE path='plain.md'"
        ).fetchone()
        assert row is not None
        assert row[0] == row[1], "body_text should equal content_raw when no frontmatter"

        conn.close()


class TestMigrateSchema:
    """G-5: old-schema databases (without body_text) are migrated in place."""

    def _build_old_schema_db(self, db_path: Path) -> None:
        """Create a v1-style database without the body_text column."""
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                title_normalized TEXT,
                path TEXT UNIQUE NOT NULL,
                content_raw TEXT,
                content_size INTEGER,
                mtime REAL,
                tags TEXT,
                frontmatter_type TEXT,
                session_id TEXT,
                linked_notes TEXT,
                schema_version TEXT DEFAULT '1',
                language TEXT DEFAULT 'en',
                indexed_at TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title, content, tags, linked_notes,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        conn.close()

    def test_old_schema_missing_body_text_gets_column_added(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "old.sqlite"
        self._build_old_schema_db(db_path)

        conn = sqlite3.connect(db_path)
        # Verify the column is absent before migration.
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "body_text" not in cols_before

        ensure_schema(conn)

        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "body_text" in cols_after
        conn.close()

    def test_old_schema_db_indexes_correctly_after_migration(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "old.sqlite"
        self._build_old_schema_db(db_path)

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text(
            "---\nid: x\ntype: topic\n---\n# Migrated\nbody migratedtoken\n",
            encoding="utf-8",
        )

        conn = sqlite3.connect(db_path)
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault, db_path=db_path)
        stats = index_vault(conn, cfg)
        assert stats.indexed == 1

        row = conn.execute(
            "SELECT body_text FROM documents WHERE path='doc.md'"
        ).fetchone()
        assert row is not None
        assert "migratedtoken" in row[0]
        assert "id: x" not in row[0]

        conn.close()


class TestCanonicalFrontmatterParity:
    """Phase A3: the indexer parses frontmatter through the canonical
    ``vault.frontmatter.parse`` so the index can never disagree with the
    reader about a document's type or tags. These guard the two real
    divergences the hand-rolled line parser had: flow-style YAML sequences
    and inline YAML comments.
    """

    def _index_one(
        self, tmp_path: Path, doc: str
    ) -> tuple[str, str] | None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text(doc, encoding="utf-8")
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        index_vault(
            conn, IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        )
        row = conn.execute(
            "SELECT tags, frontmatter_type FROM documents WHERE path='doc.md'"
        ).fetchone()
        conn.close()
        return row

    def test_flow_style_tags_stored_as_clean_tokens(self, tmp_path: Path) -> None:
        # Flow-style YAML sequence. The hand-rolled parser stored the literal
        # "[alpha, beta]"; the canonical YAML parser yields a real list that
        # the indexer joins to a clean token string.
        row = self._index_one(
            tmp_path,
            "---\nid: a\ntype: session\ntags: [alpha, beta]\n---\n# T\nbody\n",
        )
        assert row is not None
        assert row[0] == "alpha beta"
        assert "[" not in row[0]

    def test_flow_style_tags_are_searchable(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text(
            "---\nid: a\ntype: session\ntags: [alpha, beta]\n---\n# T\nbody\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        index_vault(
            conn, IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        )
        hits = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("alpha",),
        ).fetchall()
        conn.close()
        assert len(hits) == 1

    def test_inline_comment_on_type_is_stripped(self, tmp_path: Path) -> None:
        # A YAML inline comment must not leak into frontmatter_type, or a
        # ``WHERE frontmatter_type='session'`` query would miss the row.
        row = self._index_one(
            tmp_path,
            "---\nid: a\ntype: session  # primary\n---\n# T\nbody\n",
        )
        assert row is not None
        assert row[1] == "session"

    def test_malformed_frontmatter_still_indexed_for_recall(
        self, tmp_path: Path
    ) -> None:
        # A present-but-invalid frontmatter block (missing the required
        # id/type/created keys) must not drop the file from the index:
        # recall beats strictness for the indexer, which favors findability.
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(
            "---\nnote: just a stray key\n---\n# T\nbody uniquebadtoken\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        stats = index_vault(
            conn, IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db")
        )
        assert stats.indexed == 1
        row = conn.execute(
            "SELECT frontmatter_type FROM documents WHERE path='bad.md'"
        ).fetchone()
        assert row is not None
        assert row[0] == ""
        hits = conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("uniquebadtoken",),
        ).fetchall()
        conn.close()
        assert len(hits) == 1


class TestDeterministicWalkOrder:
    """Rowids must be assigned in sorted-path order, not filesystem order.

    ``rglob`` yields directory order, which differs across filesystems (ext4
    vs NTFS). Because FTS5 breaks equal-BM25 ties by rowid, an unsorted walk
    makes ranking depend on the host filesystem. The indexer sorts the walk so
    document rowids are assigned deterministically everywhere.
    """

    def test_rowids_follow_sorted_path_order_regardless_of_walk_order(
        self,
        in_memory_conn: sqlite3.Connection,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        names = ["delta.md", "alpha.md", "charlie.md", "bravo.md"]
        for name in names:
            (vault / name).write_text(
                f"---\nid: {name[:-3]}\ntype: note\n---\n# {name}\n\nBody.\n",
                encoding="utf-8",
            )

        # Simulate a filesystem that returns entries in a non-sorted order.
        real_rglob = Path.rglob
        shuffled = [vault / n for n in ("charlie.md", "delta.md", "alpha.md", "bravo.md")]

        def fake_rglob(self: Path, pattern: str) -> list[Path]:
            if self == vault and pattern == "*.md":
                return list(shuffled)
            return list(real_rglob(self, pattern))

        monkeypatch.setattr(Path, "rglob", fake_rglob)

        index_vault(
            in_memory_conn,
            IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db"),
        )

        rows = in_memory_conn.execute(
            "SELECT path FROM documents ORDER BY id"
        ).fetchall()
        paths_in_rowid_order = [r[0] for r in rows]
        assert paths_in_rowid_order == sorted(paths_in_rowid_order)
        assert paths_in_rowid_order == [
            "alpha.md",
            "bravo.md",
            "charlie.md",
            "delta.md",
        ]


class TestVaultEscapeContainment:
    """A symlink inside the vault pointing outside it must not be indexed.

    ``rglob`` follows symlinks and ``_should_index`` only checks the lexical
    relative path, so without a realpath containment guard a symlink planted in
    the vault (``vault/private.md`` -> ``~/.ssh/id_rsa``) would be read and its
    contents stored in the FTS5 index, leaking out-of-vault files through
    search.
    """

    def test_out_of_vault_symlink_is_skipped(
        self,
        in_memory_conn: sqlite3.Connection,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text(
            "# secret\n\nuniquesecrettoken contents that must never be indexed.\n",
            encoding="utf-8",
        )

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "real.md").write_text(
            "---\nid: real\ntype: note\n---\n# Real\n\nlegitvaulttoken body.\n",
            encoding="utf-8",
        )

        link = vault / "escape.md"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        stats = index_vault(
            in_memory_conn,
            IndexerConfig(vault_root=vault, db_path=tmp_path / "fts.db"),
        )

        # The legitimate in-vault file is indexed; the escaping symlink is not.
        assert stats.indexed == 1
        indexed_paths = {
            r[0] for r in in_memory_conn.execute("SELECT path FROM documents").fetchall()
        }
        assert indexed_paths == {"real.md"}

        # The out-of-vault content never reached the index.
        leak = in_memory_conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?",
            ("uniquesecrettoken",),
        ).fetchall()
        assert leak == []
