"""Integration tests for the retrieval pipeline."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.fts5.locale.tr import (
    normalize_tr,
    normalize_tr_ascii_fold,
    normalize_tr_ascii_fold_for_fts,
    normalize_tr_for_fts,
)
from mneme_core.retrieval.rrf import (
    DEFAULT_RRF_K,
    Hit,
    RetrievalBackend,
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


@pytest.fixture
def tr_indexed_db(tmp_path: Path) -> Iterator[Path]:
    """Build a dual-key (CLDR + ASCII-fold) Turkish FTS5 database.

    Stores two notes whose proper titles use the dotted Turkish ``İ``
    (``İzmir``, ``İstanbul``) so the recall matrix can prove an ASCII-capital
    query recovers them.
    """
    vault = tmp_path / "tr_vault"
    vault.mkdir()
    (vault / "izmir.md").write_text(
        "---\nid: izmir\ntype: note\ntags: sehir\n---\n"
        "# İzmir\n"
        "İzmir Ege bolgesinde bir liman sehridir.\n",
        encoding="utf-8",
    )
    (vault / "istanbul.md").write_text(
        "---\nid: istanbul\ntype: note\ntags: sehir\n---\n"
        "# İstanbul\n"
        "İstanbul iki kitayi birlestiren buyuk bir sehirdir.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "tr_fts.db"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    cfg = IndexerConfig(
        vault_root=vault,
        db_path=db_path,
        normalize=normalize_tr,
        normalize_for_fts=normalize_tr_for_fts,
        normalize_ascii=normalize_tr_ascii_fold,
        normalize_ascii_for_fts=normalize_tr_ascii_fold_for_fts,
    )
    index_vault(conn, cfg)
    conn.close()
    yield db_path


class TestTurkishDualKeyRecall:
    """Single-token recall across every casing of dotted Turkish city names.

    Pre-fix, a CLDR-only index missed ASCII-capital queries: ``Izmir`` folds
    to dotless ``ızmir`` and never matches a stored dotted ``izmir``. The
    dual-key ASCII-fold table recovers those without regressing the
    Turkish-keyboard (``İzmir``) path.
    """

    def _cfg(self, db_path: Path) -> RetrievalConfig:
        return RetrievalConfig(
            fts5_db=db_path,
            min_query_length=1,
            normalize=normalize_tr,
            normalize_ascii=normalize_tr_ascii_fold,
            top_n_final=5,
        )

    @pytest.mark.parametrize(
        "query,expected_title",
        [
            ("İzmir", "İzmir"),
            ("izmir", "İzmir"),
            ("Izmir", "İzmir"),
            ("IZMIR", "İzmir"),
            ("İstanbul", "İstanbul"),
            ("istanbul", "İstanbul"),
            ("Istanbul", "İstanbul"),
            ("ISTANBUL", "İstanbul"),
        ],
    )
    def test_single_token_recall(
        self, tr_indexed_db: Path, query: str, expected_title: str
    ) -> None:
        hits = retrieve(query, self._cfg(tr_indexed_db))
        titles = [h.title for h in hits]
        assert expected_title in titles, (
            f"{query!r} did not recall {expected_title!r}; got {titles}"
        )

    def test_cldr_only_misses_ascii_capital_then_dual_key_recovers(
        self, tr_indexed_db: Path
    ) -> None:
        # The dual-key fix is load-bearing: CLDR-only (no ascii normalizer)
        # misses the ASCII-capital spelling...
        cldr_only = RetrievalConfig(
            fts5_db=tr_indexed_db, min_query_length=1, normalize=normalize_tr
        )
        cldr_hits = retrieve("Izmir", cldr_only)
        assert not any(h.title == "İzmir" for h in cldr_hits)
        # ...and enabling the ascii key recovers it.
        recovered = retrieve("Izmir", self._cfg(tr_indexed_db))
        assert any(h.title == "İzmir" for h in recovered)

    def test_turkish_keyboard_spelling_not_regressed(
        self, tr_indexed_db: Path
    ) -> None:
        # A Turkish-keyboard user typing the proper dotted spelling must still
        # hit via the CLDR key even with the ascii key enabled.
        hits = retrieve("İstanbul", self._cfg(tr_indexed_db))
        assert any(h.title == "İstanbul" for h in hits)


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

    def test_splits_reserved_chars_into_phrases(self) -> None:
        # Reserved/separator chars split a word into a phrase rather than
        # fusing it, so the query matches the adjacent tokens unicode61
        # indexed instead of the unmatchable fused form.
        q = build_fts5_query("foo-bar baz:qux")
        assert q == '"foo bar" OR "baz qux"'

    def test_hyphenated_identifier_becomes_phrase(self) -> None:
        assert build_fts5_query("claude-mem") == '"claude mem"'

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
            min_query_length=10,
            normalize=normalize_tr,
        )
        # "short" (5 chars) falls below the explicit 10-char gate.
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


class TestQueryGate:
    """Truth-table acceptance tests for the word-aware query gate.

    Defaults: min_query_length=3, min_query_words=1, stopwords=frozenset().
    The gate fires (returns []) when the stripped length is below
    min_query_length OR meaningful-token count is below min_query_words.

    Strategy: point fts5_db at a nonexistent path so fts5_search always
    returns [].  Inject a recording stub as dense_backend so we can
    distinguish "gate fired" (stub never called) from "gate passed"
    (stub called, returns [] because no real DB, but the call happened).
    """

    def _stub(self, calls: list[str]) -> RetrievalBackend:
        def _backend(q: str, limit: int) -> list[Hit]:
            calls.append(q)
            return []

        return _backend  # type: ignore[return-value]

    def _cfg(self, missing_db: Path) -> RetrievalConfig:
        return RetrievalConfig(
            fts5_db=missing_db,
            min_query_length=3,
            min_query_words=1,
        )

    # ------------------------------------------------------------------ gated
    @pytest.mark.parametrize("query", ["", "   ", "hi", "ok"])
    def test_gated_queries_return_empty_and_skip_backend(
        self, tmp_path: Path, query: str
    ) -> None:
        calls: list[str] = []
        result = retrieve(
            query,
            self._cfg(tmp_path / "no.db"),
            dense_backend=self._stub(calls),
        )
        assert result == [], f"{query!r} should be gated"
        assert calls == [], f"{query!r} should not reach backend; calls={calls}"

    # --------------------------------------------------------------- not gated
    @pytest.mark.parametrize(
        "query",
        ["auth flow bug", "kg drain", "rrf k", "migration"],
    )
    def test_non_gated_queries_reach_backend(
        self, tmp_path: Path, query: str
    ) -> None:
        calls: list[str] = []
        retrieve(
            query,
            self._cfg(tmp_path / "no.db"),
            dense_backend=self._stub(calls),
        )
        assert calls, f"{query!r} should reach backend but stub was never called"
