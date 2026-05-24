"""Tests for :mod:`mneme_core.bench.synth`."""

from __future__ import annotations

from mneme_core.bench.synth import build_synthetic_corpus


class TestSyntheticCorpusShape:
    def test_default_counts_match_plan(self) -> None:
        corpus = build_synthetic_corpus()
        # 10 topics * 50 docs = 500, plan's headline number for Bench A.
        assert len(corpus.docs) == 500
        # 10 topics * 5 queries = 50 queries.
        assert len(corpus.queries) == 50
        assert len(corpus.topics) == 10

    def test_docs_have_unique_ids(self) -> None:
        corpus = build_synthetic_corpus()
        ids = [d.id for d in corpus.docs]
        assert len(set(ids)) == len(ids)

    def test_each_query_has_exactly_one_relevant_doc(self) -> None:
        corpus = build_synthetic_corpus()
        for q in corpus.queries:
            assert len(q.relevant_doc_ids) == 1

    def test_relevant_doc_is_same_topic(self) -> None:
        corpus = build_synthetic_corpus()
        by_id = {d.id: d for d in corpus.docs}
        for q in corpus.queries:
            target = by_id[q.relevant_doc_ids[0]]
            assert target.topic == q.topic

    def test_query_text_meets_min_length_gate(self) -> None:
        """``RetrievalConfig.min_query_length`` is 20 by default."""
        corpus = build_synthetic_corpus()
        for q in corpus.queries:
            assert len(q.text) >= 20, (q.qid, q.text)

    def test_query_contains_target_title_terms(self) -> None:
        """Each query must contain both title terms of its target doc.

        Without this property the bench would degenerate to a random
        baseline (no relationship between query tokens and the relevant
        doc).
        """
        corpus = build_synthetic_corpus()
        by_id = {d.id: d for d in corpus.docs}
        for q in corpus.queries:
            target = by_id[q.relevant_doc_ids[0]]
            term_a, term_b = target.title_terms
            assert term_a in q.text
            assert term_b in q.text


class TestSyntheticCorpusDeterminism:
    def test_same_seed_produces_same_corpus(self) -> None:
        a = build_synthetic_corpus(seed=42)
        b = build_synthetic_corpus(seed=42)
        assert [d.id for d in a.docs] == [d.id for d in b.docs]
        assert [d.title for d in a.docs] == [d.title for d in b.docs]
        assert [d.body for d in a.docs] == [d.body for d in b.docs]
        assert [q.text for q in a.queries] == [q.text for q in b.queries]

    def test_different_seed_changes_text(self) -> None:
        a = build_synthetic_corpus(seed=1)
        b = build_synthetic_corpus(seed=2)
        # IDs and titles are deterministic per topic + index, but bodies
        # are seeded RNG output so they must differ.
        bodies_a = [d.body for d in a.docs]
        bodies_b = [d.body for d in b.docs]
        assert bodies_a != bodies_b


class TestSyntheticCorpusScaling:
    def test_docs_per_topic_param(self) -> None:
        corpus = build_synthetic_corpus(docs_per_topic=3)
        assert len(corpus.docs) == 3 * len(corpus.topics)

    def test_queries_per_topic_param(self) -> None:
        corpus = build_synthetic_corpus(queries_per_topic=2)
        assert len(corpus.queries) == 2 * len(corpus.topics)
