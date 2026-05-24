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
    def test_schema_version_is_string_one(self) -> None:
        assert SCHEMA_VERSION == "1"

    def test_default_excludes_include_critical_dirs(self) -> None:
        assert "/.git/" in DEFAULT_EXCLUDE_PATTERNS
        assert "/.mneme/" in DEFAULT_EXCLUDE_PATTERNS
        assert "/node_modules/" in DEFAULT_EXCLUDE_PATTERNS
