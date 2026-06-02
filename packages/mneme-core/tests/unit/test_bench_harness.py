"""Tests for :mod:`mneme_core.bench.harness`."""

from __future__ import annotations

import math

import pytest

from mneme_core.bench.harness import (
    EvalCase,
    EvalReport,
    compare,
    head_to_head,
    load_locomo,
    load_longmemeval,
    run_eval,
)

# ---------------------------------------------------------------------------
# Deterministic retrieve helpers
# ---------------------------------------------------------------------------


def _retrieve_perfect(query: str) -> list[str | int]:
    """Always returns the two known relevant ids first."""
    return ["doc-a", "doc-b", "doc-c", "doc-d"]


def _retrieve_empty(query: str) -> list[str | int]:
    """Always returns no results."""
    return []


def _retrieve_miss(query: str) -> list[str | int]:
    """Returns ids that are never relevant."""
    return ["doc-x", "doc-y", "doc-z"]


# ---------------------------------------------------------------------------
# Shared case fixtures
# ---------------------------------------------------------------------------

_CASES_3: list[EvalCase] = [
    # Rank-1 hit: recall=1.0, RR=1.0, nDCG=1.0
    EvalCase(case_id="c1", query="q1", relevant_ids=("doc-a",)),
    # Rank-2 hit: recall=1.0, RR=0.5, nDCG=0.5 (only doc at pos 2)
    EvalCase(case_id="c2", query="q2", relevant_ids=("doc-b",)),
    # No hit: recall=0, RR=0, nDCG=0
    EvalCase(case_id="c3", query="q3", relevant_ids=("doc-z99",)),
]


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


class TestRunEval:
    def test_rank1_hit_scores_one(self) -> None:
        cases = [EvalCase(case_id="c1", query="q1", relevant_ids=("doc-a",))]
        report = run_eval(cases, _retrieve_perfect, system_name="sys", k=10)
        assert report.n_cases == 1
        assert report.per_case[0].recall_at_k == pytest.approx(1.0)
        assert report.per_case[0].reciprocal_rank == pytest.approx(1.0)
        assert report.per_case[0].ndcg_at_k == pytest.approx(1.0)

    def test_rank2_hit_rr_is_half(self) -> None:
        cases = [EvalCase(case_id="c2", query="q2", relevant_ids=("doc-b",))]
        report = run_eval(cases, _retrieve_perfect, system_name="sys", k=10)
        cr = report.per_case[0]
        # doc-b is at rank 2 -> RR = 0.5
        assert cr.reciprocal_rank == pytest.approx(0.5)
        assert cr.recall_at_k == pytest.approx(1.0)
        # DCG = 1/log2(3) ≈ 0.631, IDCG = 1/log2(2) = 1.0 -> nDCG ≈ 0.631
        expected_ndcg = 1.0 / math.log2(3)
        assert cr.ndcg_at_k == pytest.approx(expected_ndcg, rel=1e-9)

    def test_no_hit_scores_zero(self) -> None:
        cases = [EvalCase(case_id="c3", query="q3", relevant_ids=("doc-z99",))]
        report = run_eval(cases, _retrieve_perfect, system_name="sys", k=10)
        cr = report.per_case[0]
        assert cr.recall_at_k == pytest.approx(0.0)
        assert cr.reciprocal_rank == pytest.approx(0.0)
        assert cr.ndcg_at_k == pytest.approx(0.0)

    def test_means_aggregate_correctly(self) -> None:
        report = run_eval(_CASES_3, _retrieve_perfect, system_name="sys", k=10)
        assert report.n_cases == 3
        # recall: c1=1.0, c2=1.0, c3=0.0 -> mean=2/3
        assert report.mean_recall_at_k == pytest.approx(2.0 / 3.0)
        # RR: c1=1.0, c2=0.5, c3=0.0 -> mean=0.5
        assert report.mean_mrr == pytest.approx(0.5)

    def test_empty_cases_returns_zeros_no_raise(self) -> None:
        report = run_eval([], _retrieve_perfect, system_name="sys", k=10)
        assert report.n_cases == 0
        assert report.mean_recall_at_k == 0.0
        assert report.mean_mrr == 0.0
        assert report.mean_ndcg_at_k == 0.0
        assert report.per_case == ()

    def test_retrieve_returns_empty_case_scores_zero_no_raise(self) -> None:
        cases = [EvalCase(case_id="c1", query="q1", relevant_ids=("doc-a",))]
        report = run_eval(cases, _retrieve_empty, system_name="sys", k=10)
        assert report.n_cases == 1
        cr = report.per_case[0]
        assert cr.recall_at_k == 0.0
        assert cr.reciprocal_rank == 0.0
        assert cr.ndcg_at_k == 0.0

    def test_per_case_order_preserved(self) -> None:
        report = run_eval(_CASES_3, _retrieve_perfect, system_name="sys", k=10)
        ids = [c.case_id for c in report.per_case]
        assert ids == ["c1", "c2", "c3"]

    def test_system_name_propagated(self) -> None:
        report = run_eval([], _retrieve_perfect, system_name="my-system", k=5)
        assert report.system_name == "my-system"
        assert report.k == 5

    def test_report_is_frozen(self) -> None:
        report = run_eval([], _retrieve_perfect, system_name="sys", k=10)
        with pytest.raises((AttributeError, TypeError)):
            report.system_name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_longmemeval
# ---------------------------------------------------------------------------


class TestLoadLongmemeval:
    def test_canonical_keys(self) -> None:
        recs = [
            {
                "question_id": "q1",
                "question": "What happened?",
                "answer_session_ids": ["s1", "s2"],
            }
        ]
        cases = load_longmemeval(recs)
        assert len(cases) == 1
        assert cases[0].case_id == "q1"
        assert cases[0].query == "What happened?"
        assert cases[0].relevant_ids == ("s1", "s2")

    def test_id_key_variant(self) -> None:
        recs = [{"id": "x99", "query": "foo", "relevant_ids": ["r1"]}]
        cases = load_longmemeval(recs)
        assert cases[0].case_id == "x99"

    def test_case_id_key_variant(self) -> None:
        recs = [{"case_id": "ccc", "input": "bar", "evidence_ids": ["e1"]}]
        cases = load_longmemeval(recs)
        assert cases[0].case_id == "ccc"
        assert cases[0].query == "bar"
        assert cases[0].relevant_ids == ("e1",)

    def test_gold_ids_key_variant(self) -> None:
        recs = [{"question": "q?", "gold_ids": ["g1", "g2"]}]
        cases = load_longmemeval(recs)
        assert cases[0].relevant_ids == ("g1", "g2")

    def test_fallback_case_id_when_no_id_key(self) -> None:
        recs = [{"question": "q?"}]
        cases = load_longmemeval(recs)
        assert cases[0].case_id == "case-0"

    def test_skip_record_with_no_query(self) -> None:
        recs = [
            {"question_id": "q1"},  # no query key -> skip
            {"question": "valid", "question_id": "q2"},
        ]
        cases = load_longmemeval(recs)
        assert len(cases) == 1
        assert cases[0].case_id == "q2"

    def test_missing_relevant_ids_gives_empty_tuple(self) -> None:
        recs = [{"question": "q?", "question_id": "q1"}]
        cases = load_longmemeval(recs)
        assert cases[0].relevant_ids == ()

    def test_never_raises_on_malformed(self) -> None:
        recs: list[dict[str, object]] = [
            {},
            {"question": "ok"},
            {"question": "ok2", "answer_session_ids": None},
            {"question": "ok3", "answer_session_ids": [1, 2, 3]},
        ]
        result = load_longmemeval(recs)
        # First record: no query -> skipped
        assert len(result) == 3

    def test_integer_relevant_ids_preserved(self) -> None:
        recs = [{"question": "q?", "relevant_ids": [1, 2, 3]}]
        cases = load_longmemeval(recs)
        assert cases[0].relevant_ids == (1, 2, 3)


# ---------------------------------------------------------------------------
# load_locomo
# ---------------------------------------------------------------------------


class TestLoadLocomo:
    def test_canonical_keys(self) -> None:
        recs = [
            {
                "sample_id": "s1",
                "question": "What is X?",
                "evidence": ["e1", "e2"],
            }
        ]
        cases = load_locomo(recs)
        assert len(cases) == 1
        assert cases[0].case_id == "s1"
        assert cases[0].query == "What is X?"
        assert cases[0].relevant_ids == ("e1", "e2")

    def test_id_key_variant(self) -> None:
        recs = [{"id": "abc", "query": "foo", "relevant_ids": ["r1"]}]
        cases = load_locomo(recs)
        assert cases[0].case_id == "abc"

    def test_gold_ids_key_variant(self) -> None:
        recs = [{"query": "q?", "gold_ids": ["g1"]}]
        cases = load_locomo(recs)
        assert cases[0].relevant_ids == ("g1",)

    def test_fallback_case_id(self) -> None:
        recs = [{"question": "q?"}]
        cases = load_locomo(recs)
        assert cases[0].case_id == "case-0"

    def test_skip_record_with_no_query(self) -> None:
        recs = [
            {"sample_id": "s1"},  # no query -> skip
            {"sample_id": "s2", "question": "valid"},
        ]
        cases = load_locomo(recs)
        assert len(cases) == 1
        assert cases[0].case_id == "s2"

    def test_missing_relevant_ids_gives_empty_tuple(self) -> None:
        recs = [{"question": "q?"}]
        cases = load_locomo(recs)
        assert cases[0].relevant_ids == ()

    def test_never_raises_on_malformed(self) -> None:
        recs: list[dict[str, object]] = [
            {},
            {"question": "ok", "evidence": None},
            {"question": "ok2", "evidence": [1, 2]},
        ]
        result = load_locomo(recs)
        # First: no query -> skip; second + third have queries
        assert len(result) == 2


# ---------------------------------------------------------------------------
# head_to_head
# ---------------------------------------------------------------------------


class TestHeadToHead:
    def test_two_systems_over_same_cases(self) -> None:
        from mneme_core.bench.harness import RetrieveIds

        cases = list(_CASES_3)
        typed_systems: dict[str, RetrieveIds] = {
            "perfect": _retrieve_perfect,
            "empty": _retrieve_empty,
        }
        reports = head_to_head(cases, typed_systems, k=10)
        assert set(reports) == {"perfect", "empty"}
        assert reports["perfect"].n_cases == 3
        assert reports["empty"].n_cases == 3

    def test_perfect_system_beats_empty(self) -> None:
        from mneme_core.bench.harness import RetrieveIds

        typed_systems: dict[str, RetrieveIds] = {
            "perfect": _retrieve_perfect,
            "empty": _retrieve_empty,
        }
        reports = head_to_head(list(_CASES_3), typed_systems, k=10)
        assert reports["perfect"].mean_recall_at_k > reports["empty"].mean_recall_at_k
        assert reports["perfect"].mean_mrr > reports["empty"].mean_mrr
        assert reports["perfect"].mean_ndcg_at_k > reports["empty"].mean_ndcg_at_k

    def test_empty_system_scores_zero(self) -> None:
        from mneme_core.bench.harness import RetrieveIds

        typed_systems: dict[str, RetrieveIds] = {"empty": _retrieve_empty}
        reports = head_to_head(list(_CASES_3), typed_systems, k=10)
        r = reports["empty"]
        assert r.mean_recall_at_k == 0.0
        assert r.mean_mrr == 0.0
        assert r.mean_ndcg_at_k == 0.0

    def test_cases_materialised_once_both_systems_see_same_n(self) -> None:
        from mneme_core.bench.harness import RetrieveIds

        typed_systems: dict[str, RetrieveIds] = {
            "a": _retrieve_perfect,
            "b": _retrieve_miss,
        }
        reports = head_to_head(list(_CASES_3), typed_systems, k=10)
        assert reports["a"].n_cases == reports["b"].n_cases == 3


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


class TestCompare:
    def _make_report(self, name: str, r: float, mrr: float, n: float) -> EvalReport:
        return EvalReport(
            system_name=name,
            k=10,
            n_cases=1,
            mean_recall_at_k=r,
            mean_mrr=mrr,
            mean_ndcg_at_k=n,
            per_case=(),
        )

    def test_structure_keys_present(self) -> None:
        r1 = self._make_report("sys-a", 0.8, 0.7, 0.75)
        r2 = self._make_report("sys-b", 0.6, 0.9, 0.65)
        result = compare({"sys-a": r1, "sys-b": r2})
        assert "systems" in result
        assert "metrics" in result
        assert "leader_by_metric" in result

    def test_systems_sorted(self) -> None:
        r1 = self._make_report("zebra", 0.5, 0.5, 0.5)
        r2 = self._make_report("alpha", 0.5, 0.5, 0.5)
        result = compare({"zebra": r1, "alpha": r2})
        assert result["systems"] == ["alpha", "zebra"]

    def test_leader_points_at_better_system(self) -> None:
        r1 = self._make_report("sys-a", 0.9, 0.8, 0.85)
        r2 = self._make_report("sys-b", 0.6, 0.5, 0.55)
        result = compare({"sys-a": r1, "sys-b": r2})
        leaders = result["leader_by_metric"]
        assert isinstance(leaders, dict)
        assert leaders["recall_at_k"] == "sys-a"
        assert leaders["mrr"] == "sys-a"
        assert leaders["ndcg_at_k"] == "sys-a"

    def test_leader_different_per_metric(self) -> None:
        r1 = self._make_report("sys-a", 0.9, 0.4, 0.5)
        r2 = self._make_report("sys-b", 0.6, 0.9, 0.8)
        result = compare({"sys-a": r1, "sys-b": r2})
        leaders = result["leader_by_metric"]
        assert isinstance(leaders, dict)
        assert leaders["recall_at_k"] == "sys-a"
        assert leaders["mrr"] == "sys-b"
        assert leaders["ndcg_at_k"] == "sys-b"

    def test_tie_broken_lexicographically(self) -> None:
        r1 = self._make_report("beta", 0.5, 0.5, 0.5)
        r2 = self._make_report("alpha", 0.5, 0.5, 0.5)
        result = compare({"beta": r1, "alpha": r2})
        leaders = result["leader_by_metric"]
        assert isinstance(leaders, dict)
        # Both tied at 0.5 -> alpha is lex-smallest
        assert leaders["recall_at_k"] == "alpha"
        assert leaders["mrr"] == "alpha"
        assert leaders["ndcg_at_k"] == "alpha"

    def test_metrics_values_match_reports(self) -> None:
        r1 = self._make_report("sys-a", 0.8, 0.7, 0.75)
        result = compare({"sys-a": r1})
        metrics = result["metrics"]
        assert isinstance(metrics, dict)
        assert isinstance(metrics["recall_at_k"], dict)
        assert metrics["recall_at_k"]["sys-a"] == pytest.approx(0.8)
        assert metrics["mrr"]["sys-a"] == pytest.approx(0.7)
        assert metrics["ndcg_at_k"]["sys-a"] == pytest.approx(0.75)

    def test_empty_reports_no_raise(self) -> None:
        result = compare({})
        assert result["systems"] == []
        leaders = result["leader_by_metric"]
        assert isinstance(leaders, dict)
        assert leaders["recall_at_k"] == ""
        assert leaders["mrr"] == ""
        assert leaders["ndcg_at_k"] == ""
