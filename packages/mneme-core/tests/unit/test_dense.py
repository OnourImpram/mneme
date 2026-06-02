"""Unit and integration tests for mneme_core.retrieval.dense.

Covers:
- hashing_embed: determinism, length, L2 norm, empty/whitespace, distinctness.
- cosine: identical vectors, orthogonal, zero vector, mismatched length.
- DenseIndex.search: ranking, limit, deterministic tiebreak.
- DenseIndex json round-trip (to_json/from_json, save/load), absent file.
- DenseBackend.__call__: source=="dense", id==doc_id, best-first, limit,
  empty index, cross-dim mismatch (graceful), never raises.
- Integration: build_dense_index over a real FTS5 db, inject DenseBackend
  into retrieve(), assert "dense" in sources; RRF dedup over shared doc_id
  produces a single entry with both sources.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.retrieval.dense import (
    DenseBackend,
    DenseDoc,
    DenseIndex,
    build_dense_index,
    cosine,
    hashing_embed,
)
from mneme_core.retrieval.rrf import Hit, RetrievalConfig, fts5_search, retrieve, rrf_fuse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit(values: list[float]) -> tuple[float, ...]:
    """Manually L2-normalise a list and return a tuple."""
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0.0:
        return tuple(values)
    return tuple(x / norm for x in values)


def _make_doc(doc_id: int, vec: list[float]) -> DenseDoc:
    return DenseDoc(
        doc_id=doc_id,
        path=f"doc{doc_id}.md",
        title=f"Doc {doc_id}",
        vector=tuple(vec),
    )


# ---------------------------------------------------------------------------
# hashing_embed tests
# ---------------------------------------------------------------------------


class TestHashingEmbed:
    def test_deterministic(self) -> None:
        v1 = hashing_embed("hello world")
        v2 = hashing_embed("hello world")
        assert v1 == v2

    def test_length_equals_dim_default(self) -> None:
        v = hashing_embed("some text")
        assert len(v) == 256

    def test_length_equals_custom_dim(self) -> None:
        v = hashing_embed("some text", dim=64)
        assert len(v) == 64

    def test_l2_norm_approx_one_nonempty(self) -> None:
        v = hashing_embed("the quick brown fox jumps over the lazy dog")
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_empty_string_returns_all_zeros(self) -> None:
        v = hashing_embed("")
        assert all(x == 0.0 for x in v)
        assert len(v) == 256

    def test_whitespace_only_returns_all_zeros(self) -> None:
        v = hashing_embed("   \t\n  ")
        assert all(x == 0.0 for x in v)

    def test_different_texts_give_different_vectors(self) -> None:
        v1 = hashing_embed("apple pie recipe")
        v2 = hashing_embed("quantum field theory")
        assert v1 != v2

    def test_single_token_deterministic(self) -> None:
        v = hashing_embed("token")
        assert v == hashing_embed("token")
        assert len(v) == 256


# ---------------------------------------------------------------------------
# cosine tests
# ---------------------------------------------------------------------------


class TestCosine:
    def test_identical_unit_vectors(self) -> None:
        v = _unit([1.0, 0.0, 0.0])
        assert cosine(v, v) == pytest.approx(1.0, abs=1e-9)

    def test_orthogonal_vectors(self) -> None:
        a = _unit([1.0, 0.0, 0.0])
        b = _unit([0.0, 1.0, 0.0])
        assert cosine(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_zero_vector_returns_zero(self) -> None:
        v = _unit([1.0, 0.0])
        z = (0.0, 0.0)
        assert cosine(v, z) == 0.0
        assert cosine(z, v) == 0.0
        assert cosine(z, z) == 0.0

    def test_mismatched_length_returns_zero_no_raise(self) -> None:
        a = (1.0, 0.0)
        b = (1.0, 0.0, 0.0)
        assert cosine(a, b) == 0.0  # no exception

    def test_opposite_vectors(self) -> None:
        v = _unit([1.0, 0.0])
        neg_v = tuple(-x for x in v)
        assert cosine(v, neg_v) == pytest.approx(-1.0, abs=1e-9)

    def test_empty_sequences_return_zero(self) -> None:
        assert cosine((), ()) == 0.0


# ---------------------------------------------------------------------------
# DenseIndex.search tests
# ---------------------------------------------------------------------------


class TestDenseIndexSearch:
    def _build_index(self) -> DenseIndex:
        # Three docs with known 2-D unit vectors
        docs = [
            _make_doc(1, list(_unit([1.0, 0.0]))),  # points right
            _make_doc(2, list(_unit([0.0, 1.0]))),  # points up
            _make_doc(3, list(_unit([1.0, 1.0]))),  # diagonal
        ]
        return DenseIndex(dim=2, docs=docs)

    def test_closest_ranked_first(self) -> None:
        idx = self._build_index()
        # Query pointing almost right: doc 1 should be closest
        q = _unit([0.99, 0.01])
        results = idx.search(q, limit=3)
        assert results[0][0].doc_id == 1

    def test_limit_respected(self) -> None:
        idx = self._build_index()
        q = _unit([1.0, 0.0])
        results = idx.search(q, limit=2)
        assert len(results) == 2

    def test_limit_zero_returns_empty(self) -> None:
        idx = self._build_index()
        q = _unit([1.0, 0.0])
        assert idx.search(q, limit=0) == []

    def test_empty_index_returns_empty(self) -> None:
        idx = DenseIndex(dim=2, docs=[])
        assert idx.search(_unit([1.0, 0.0]), limit=5) == []

    def test_deterministic_tiebreak_by_doc_id(self) -> None:
        # Two docs with identical vectors → lower doc_id comes first
        v = list(_unit([1.0, 1.0]))
        docs = [_make_doc(10, v), _make_doc(5, v)]
        idx = DenseIndex(dim=2, docs=docs)
        results = idx.search(_unit([1.0, 1.0]), limit=2)
        assert results[0][0].doc_id == 5
        assert results[1][0].doc_id == 10

    def test_scores_returned(self) -> None:
        idx = self._build_index()
        q = _unit([1.0, 0.0])
        results = idx.search(q, limit=3)
        for _, score in results:
            assert isinstance(score, float)


# ---------------------------------------------------------------------------
# DenseIndex JSON round-trip tests
# ---------------------------------------------------------------------------


class TestDenseIndexPersistence:
    def _sample_index(self) -> DenseIndex:
        docs = [
            DenseDoc(
                doc_id=1,
                path="a.md",
                title="Alpha",
                vector=tuple(hashing_embed("alpha", dim=32)),
            ),
            DenseDoc(
                doc_id=2,
                path="b.md",
                title="Beta",
                vector=tuple(hashing_embed("beta", dim=32)),
            ),
        ]
        return DenseIndex(dim=32, docs=docs)

    def test_to_json_from_json_round_trip(self) -> None:
        idx = self._sample_index()
        restored = DenseIndex.from_json(idx.to_json())
        assert restored.dim == idx.dim
        assert len(restored.docs) == len(idx.docs)
        for orig, rest in zip(idx.docs, restored.docs, strict=False):
            assert rest.doc_id == orig.doc_id
            assert rest.path == orig.path
            assert rest.title == orig.title
            assert rest.vector == pytest.approx(orig.vector, abs=1e-9)

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        idx = self._sample_index()
        target = tmp_path / ".mneme" / "dense.json"
        idx.save(target)
        assert target.exists()
        restored = DenseIndex.load(target)
        assert restored.dim == idx.dim
        assert len(restored.docs) == 2

    def test_load_absent_file_returns_empty_index(self, tmp_path: Path) -> None:
        idx = DenseIndex.load(tmp_path / "nonexistent.json")
        assert idx.docs == []
        assert idx.dim == 256  # default

    def test_load_malformed_json_returns_empty_index(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("NOT JSON {{{{", encoding="utf-8")
        idx = DenseIndex.load(bad)
        assert idx.docs == []

    def test_from_json_malformed_returns_empty(self) -> None:
        idx = DenseIndex.from_json("this is not json")
        assert idx.docs == []

    def test_save_never_raises_on_unwritable(self, tmp_path: Path) -> None:
        idx = self._sample_index()
        # Point at an impossible path (file as directory parent)
        impossible = tmp_path / "file_not_dir.txt" / "deep" / "dense.json"
        (tmp_path / "file_not_dir.txt").write_text("block", encoding="utf-8")
        # Must not raise
        idx.save(impossible)


# ---------------------------------------------------------------------------
# DenseBackend.__call__ tests
# ---------------------------------------------------------------------------


class TestDenseBackend:
    def _index_with_docs(self) -> DenseIndex:
        docs = [
            DenseDoc(
                doc_id=i,
                path=f"doc{i}.md",
                title=f"Title {i}",
                vector=hashing_embed(f"document content about topic {i}", dim=64),
            )
            for i in range(1, 5)
        ]
        return DenseIndex(dim=64, docs=docs)

    def test_source_is_dense(self) -> None:
        backend = DenseBackend(self._index_with_docs())
        hits = backend("some query about topics", limit=4)
        assert all(h.source == "dense" for h in hits)

    def test_id_equals_doc_id(self) -> None:
        backend = DenseBackend(self._index_with_docs())
        hits = backend("topic query", limit=4)
        doc_ids = {doc.doc_id for doc in self._index_with_docs().docs}
        for h in hits:
            assert isinstance(h.id, int)
            assert h.id in doc_ids

    def test_best_first_order(self) -> None:
        backend = DenseBackend(self._index_with_docs())
        hits = backend("document content about topic 2", limit=4)
        assert len(hits) >= 2
        # Scores should be non-increasing
        for i in range(len(hits) - 1):
            assert hits[i].score >= hits[i + 1].score

    def test_limit_respected(self) -> None:
        backend = DenseBackend(self._index_with_docs())
        hits = backend("query", limit=2)
        assert len(hits) <= 2

    def test_empty_index_returns_empty(self) -> None:
        backend = DenseBackend(DenseIndex(dim=64, docs=[]))
        assert backend("any query", limit=5) == []

    def test_never_raises_on_cross_dim_mismatch(self) -> None:
        # Index built with dim=64 but we inject an embed_fn returning dim=8
        idx = self._index_with_docs()  # dim=64

        def tiny_embed(text: str) -> tuple[float, ...]:
            return hashing_embed(text, dim=8)

        backend = DenseBackend(idx, embed_fn=tiny_embed)
        # cosine returns 0.0 for mismatched lengths → all scores 0.0 → [] or sorted safely
        result = backend("query text", limit=5)
        assert isinstance(result, list)  # must not raise

    def test_never_raises_on_broken_embed_fn(self) -> None:
        idx = self._index_with_docs()

        def boom(text: str) -> tuple[float, ...]:
            raise RuntimeError("embed crashed")

        backend = DenseBackend(idx, embed_fn=boom)
        result = backend("anything", limit=5)
        assert result == []

    def test_hit_fields_populated(self) -> None:
        backend = DenseBackend(self._index_with_docs())
        hits = backend("topic", limit=1)
        if hits:
            h = hits[0]
            assert isinstance(h.path, str)
            assert isinstance(h.title, str)
            assert isinstance(h.score, float)
            assert h.rrf_score == 0.0


# ---------------------------------------------------------------------------
# Integration: build_dense_index + inject into retrieve()
# ---------------------------------------------------------------------------


def _make_fts5_db(tmp_path: Path) -> Path:
    """Create a 3-doc vault, index it, and return the db path.

    Reuses the exact same pattern as test_retrieval.py / test_dense_rrf_integration.py
    so fixture conventions stay consistent across the test suite.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text(
        "---\nid: a\ntype: session\ntags: memory\n---\n"
        "# Vault Native Memory\n"
        "Markdown is ground truth. Hybrid retrieval beats single backend.\n",
        encoding="utf-8",
    )
    (vault / "b.md").write_text(
        "---\nid: b\ntype: topic\ntags: retrieval\n---\n"
        "# Reciprocal Rank Fusion\n"
        "RRF at k equal sixty fuses ranked lists from multiple backends.\n",
        encoding="utf-8",
    )
    (vault / "c.md").write_text(
        "---\nid: c\ntype: reference\ntags: privacy\n---\n"
        "# Privacy Redaction\n"
        "Sensitive content stripped at staging. Audit log records hashes.\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "fts.db"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    cfg = IndexerConfig(vault_root=vault, db_path=db_path)
    index_vault(conn, cfg)
    conn.close()
    return db_path


class TestBuildDenseIndex:
    def test_returns_correct_doc_count(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path)
        assert len(idx.docs) == 3

    def test_doc_ids_are_integers(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path)
        for doc in idx.docs:
            assert isinstance(doc.doc_id, int)

    def test_doc_ids_match_fts5_rowids(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path)

        conn = sqlite3.connect(db_path)
        fts_ids = {row[0] for row in conn.execute("SELECT id FROM documents").fetchall()}
        conn.close()

        dense_ids = {doc.doc_id for doc in idx.docs}
        assert dense_ids == fts_ids, (
            f"Dense doc_ids {dense_ids} must match documents.id rowids {fts_ids}"
        )

    def test_absent_db_returns_empty_index(self, tmp_path: Path) -> None:
        idx = build_dense_index(tmp_path / "no.db")
        assert idx.docs == []

    def test_custom_dim_applied(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path, dim=128)
        assert idx.dim == 128
        for doc in idx.docs:
            assert len(doc.vector) == 128

    def test_vectors_unit_norm(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path)
        for doc in idx.docs:
            v = doc.vector
            norm = math.sqrt(sum(x * x for x in v))
            # Title+path fall-back is non-empty so norm should be ~1.0
            # (may be 0 only if text happened to be all-stop chars, unlikely)
            if norm > 0.0:
                assert norm == pytest.approx(1.0, abs=1e-6)


class TestDenseRrfIntegration:
    """Full pipeline: build_dense_index → DenseBackend → retrieve() → fused hits."""

    def test_retrieve_includes_dense_source(self, tmp_path: Path) -> None:
        db_path = _make_fts5_db(tmp_path)
        idx = build_dense_index(db_path)
        backend = DenseBackend(idx)

        cfg = RetrievalConfig(
            fts5_db=db_path,
            min_query_length=3,
            top_n_final=10,
        )
        hits = retrieve("vault native memory retrieval", cfg, dense_backend=backend)
        assert hits, "Expected at least one hit from fused pipeline"
        has_dense = any("dense" in h.sources for h in hits)
        assert has_dense, f"Expected a hit with 'dense' in sources; got {[h.sources for h in hits]}"

    def test_rrf_dedup_shared_doc_id(self, tmp_path: Path) -> None:
        """A doc found by both FTS5 and dense must appear ONCE with both sources."""
        db_path = _make_fts5_db(tmp_path)

        # Get FTS5 hits for the query to know which doc_ids it returns
        fts_hits = fts5_search("vault native memory", db_path, limit=10)
        assert fts_hits, "Need at least one FTS5 hit for this test"

        # Pick the first FTS5 hit's id and fabricate a matching dense hit
        shared_id = fts_hits[0].id
        shared_path = fts_hits[0].path

        fake_dense_hit = Hit(
            id=shared_id,
            path=shared_path,
            title=fts_hits[0].title,
            score=0.9,
            source="dense",
        )

        fused = rrf_fuse([fts_hits, [fake_dense_hit]])

        # The shared document must appear exactly once
        matching = [h for h in fused if h.id == shared_id]
        assert len(matching) == 1, (
            f"Expected exactly one fused entry for id={shared_id}; got {len(matching)}"
        )
        # That entry must carry both sources
        assert "dense" in matching[0].sources, (
            f"Expected 'dense' in sources; got {matching[0].sources}"
        )
        assert "fts5" in matching[0].sources, (
            f"Expected 'fts5' in sources; got {matching[0].sources}"
        )

    def test_rrf_dedup_via_rrf_fuse_directly(self) -> None:
        """Unit-level: rrf_fuse over [fts5_hits, dense_hits] with shared id deduplicates."""
        fts5_hits = [
            Hit(id=1, path="a.md", title="A", score=-1.0, source="fts5"),
            Hit(id=2, path="b.md", title="B", score=-2.0, source="fts5"),
        ]
        dense_hits = [
            Hit(id=1, path="a.md", title="A", score=0.95, source="dense"),
            Hit(id=3, path="c.md", title="C", score=0.80, source="dense"),
        ]
        fused = rrf_fuse([fts5_hits, dense_hits])

        ids = [h.id for h in fused]
        # id=1 appears in both rankings → must be present exactly once
        assert ids.count(1) == 1, f"id=1 deduped to {ids.count(1)} entries; full ids={ids}"

        # The deduped entry for id=1 must carry both sources
        hit1 = next(h for h in fused if h.id == 1)
        assert "dense" in hit1.sources
        assert "fts5" in hit1.sources
