"""Integration tests for the retrieval pipeline."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts
from mneme_core.retrieval.rrf import (
    DEFAULT_RRF_K,
    Hit,
    RetrievalConfig,
    build_fts5_query,
    fts5_search,
    retrieve,
    rrf_fuse,
)


@pytest.fixture
def indexed_db(tmp_path: Path) -> Iterator[Path]:
    """Build a populated FTS5 database on disk and yield its path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text(
        "---\nid: a\ntype: session\ntags: foo\n---\n"
        "# Vault Native Memory\n"
        "Markdown is ground truth. Hybrid retrieval beats single backend.\n",
        encoding="utf-8",
    )
    (vault / "b.md").write_text(
        "---\nid: b\ntype: topic\ntags: bar\n---\n"
        "# Reciprocal Rank Fusion\n"
        "RRF at k equal sixty fuses ranked lists. Robust across tasks.\n",
        encoding="utf-8",
    )
    (vault / "c.md").write_text(
        "---\nid: c\ntype: reference\ntags: baz\n---\n"
        "# Privacy Redaction\n"
        "Sensitive content is stripped at staging. Audit log records hashes.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "fts.db"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    cfg = IndexerConfig(
        vault_root=vault,
        db_path=db_path,
        normalize=normalize_tr,
        normalize_for_fts=normalize_tr_for_fts,
    )
    index_vault(conn, cfg)
    conn.close()
    yield db_path


class TestBuildFts5Query:
    def test_quotes_each_token(self) -> None:
        q = build_fts5_query("hello world")
        assert q == '"hello" OR "world"'

    def test_drops_short_tokens(self) -> None:
        q = build_fts5_query("a hello b world")
        assert q == '"hello" OR "world"'

    def test_drops_stopwords(self) -> None:
        q = build_fts5_query("the hello world", stopwords=frozenset({"the"}))
        assert q == '"hello" OR "world"'

    def test_strips_fts5_reserved_chars(self) -> None:
        q = build_fts5_query("foo-bar baz:qux")
        assert q == '"foobar" OR "bazqux"'

    def test_returns_empty_on_all_dropped(self) -> None:
        q = build_fts5_query("a b c")
        assert q == ""

    def test_returns_empty_on_empty_input(self) -> None:
        assert build_fts5_query("") == ""


class TestFts5Search:
    def test_returns_hits_for_known_terms(self, indexed_db: Path) -> None:
        hits = fts5_search("rank fusion", indexed_db, limit=10)
        assert isinstance(hits, list)
        assert any(h.title.startswith("Reciprocal") for h in hits)
        for h in hits:
            assert isinstance(h, Hit)
            assert h.source == "fts5"

    def test_returns_empty_on_missing_db(self, tmp_path: Path) -> None:
        hits = fts5_search("anything", tmp_path / "nope.db")
        assert hits == []

    def test_returns_empty_on_empty_query(self, indexed_db: Path) -> None:
        assert fts5_search("", indexed_db) == []

    def test_respects_limit(self, indexed_db: Path) -> None:
        hits = fts5_search("retrieval OR rank OR memory OR privacy", indexed_db, limit=1)
        assert len(hits) <= 1


class TestRrfFuse:
    def _hit(self, hit_id: str, source: str = "fts5") -> Hit:
        return Hit(id=hit_id, path=f"{hit_id}.md", title=hit_id.upper(), score=0.0, source=source)

    def test_single_ranking_passthrough(self) -> None:
        ranking = [self._hit("a"), self._hit("b"), self._hit("c")]
        fused = rrf_fuse([ranking])
        assert [h.id for h in fused] == ["a", "b", "c"]
        # First hit gets highest rrf score.
        assert fused[0].rrf_score > fused[1].rrf_score > fused[2].rrf_score

    def test_two_rankings_promote_intersection(self) -> None:
        r1 = [self._hit("x"), self._hit("y"), self._hit("z")]
        r2 = [self._hit("y", source="dense"), self._hit("x", source="dense")]
        fused = rrf_fuse([r1, r2])
        # 'x' and 'y' appear in both, should outscore 'z' which is in one.
        ids = [h.id for h in fused]
        assert ids.index("x") < ids.index("z")
        assert ids.index("y") < ids.index("z")

    def test_sources_deduplicated_and_sorted(self) -> None:
        r1 = [self._hit("a", source="fts5")]
        r2 = [self._hit("a", source="dense")]
        r3 = [self._hit("a", source="dense")]
        fused = rrf_fuse([r1, r2, r3])
        assert fused[0].sources == ["dense", "fts5"]

    def test_rrf_score_formula(self) -> None:
        """First-position hit in a single ranking yields score 1 / (k + 1)."""
        ranking = [self._hit("a")]
        fused = rrf_fuse([ranking], k=60)
        assert abs(fused[0].rrf_score - 1.0 / 61.0) < 1e-9

    def test_custom_k(self) -> None:
        ranking = [self._hit("a"), self._hit("b")]
        fused_k10 = rrf_fuse([ranking], k=10)
        fused_k100 = rrf_fuse([ranking], k=100)
        # Larger k yields smaller score gap between adjacent ranks.
        gap_10 = fused_k10[0].rrf_score - fused_k10[1].rrf_score
        gap_100 = fused_k100[0].rrf_score - fused_k100[1].rrf_score
        assert gap_10 > gap_100


class TestRetrieve:
    def test_below_gate_returns_empty(self, indexed_db: Path) -> None:
        cfg = RetrievalConfig(
            fts5_db=indexed_db,
            min_query_length=20,
            normalize=normalize_tr,
        )
        # Short query falls below the gate.
        assert retrieve("short", cfg) == []

    def test_above_gate_returns_hits(self, indexed_db: Path) -> None:
        cfg = RetrievalConfig(
            fts5_db=indexed_db,
            min_query_length=5,
            normalize=normalize_tr,
            top_n_final=5,
        )
        hits = retrieve("reciprocal rank fusion robust", cfg)
        assert len(hits) >= 1
        assert all(isinstance(h, Hit) for h in hits)
        assert all(h.rrf_score > 0 for h in hits)

    def test_dense_backend_is_called(self, indexed_db: Path) -> None:
        calls: list[tuple[str, int]] = []

        def fake_dense(q: str, limit: int) -> list[Hit]:
            calls.append((q, limit))
            return [Hit(id="dense-1", path="d.md", title="Dense", score=0.9, source="dense")]

        cfg = RetrievalConfig(
            fts5_db=indexed_db,
            min_query_length=5,
            normalize=normalize_tr,
            top_n_final=10,
        )
        hits = retrieve("rank fusion robust", cfg, dense_backend=fake_dense)
        assert len(calls) == 1
        assert any(h.id == "dense-1" for h in hits)

    def test_kg_backend_failure_is_isolated(self, indexed_db: Path) -> None:
        def bad_kg(q: str, limit: int) -> list[Hit]:
            raise RuntimeError("kg backend down")

        cfg = RetrievalConfig(
            fts5_db=indexed_db,
            min_query_length=5,
            normalize=normalize_tr,
            top_n_final=10,
        )
        hits = retrieve("rank fusion robust", cfg, kg_backend=bad_kg)
        # Pipeline degrades gracefully; FTS5 results still flow through.
        assert isinstance(hits, list)

    def test_reranker_invoked(self, indexed_db: Path) -> None:
        seen: list[int] = []

        def reverser(q: str, candidates: list[Hit], top_n: int) -> list[Hit]:
            seen.append(len(candidates))
            return list(reversed(candidates))[:top_n]

        cfg = RetrievalConfig(
            fts5_db=indexed_db,
            min_query_length=5,
            normalize=normalize_tr,
            top_n_final=3,
        )
        retrieve("rank fusion robust memory", cfg, reranker=reverser)
        assert seen and seen[0] >= 1

    def test_default_rrf_k_constant(self) -> None:
        assert DEFAULT_RRF_K == 60
