"""Tests for :mod:`mneme_core.bench.metrics`."""

from __future__ import annotations

import math

import pytest

from mneme_core.bench.metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    percentiles,
    precision_at_k,
    recall_at_k,
)


class TestNdcgAtK:
    def test_perfect_ranking_scores_one(self) -> None:
        ranked = ["a", "b", "c", "d", "e"]
        relevant = {"a", "b", "c"}
        assert ndcg_at_k(ranked, relevant, k=5) == pytest.approx(1.0)

    def test_no_relevant_returns_zero(self) -> None:
        assert ndcg_at_k(["a", "b"], set(), k=2) == 0.0

    def test_no_hits_in_top_k_returns_zero(self) -> None:
        ranked = ["a", "b", "c"]
        relevant = {"x", "y"}
        assert ndcg_at_k(ranked, relevant, k=3) == 0.0

    def test_partial_hits_drop_below_one(self) -> None:
        # One relevant doc at position 3, ideal would be position 1.
        ranked = ["x", "y", "a"]
        relevant = {"a"}
        score = ndcg_at_k(ranked, relevant, k=3)
        assert 0.0 < score < 1.0
        # DCG = 1/log2(4) = 0.5, IDCG = 1/log2(2) = 1.0
        assert score == pytest.approx(0.5, rel=1e-9)

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            ndcg_at_k(["a"], {"a"}, k=0)

    def test_duplicate_relevant_id_cannot_inflate_score(self) -> None:
        score = ndcg_at_k(["a", "a"], {"a", "b"}, k=2)
        assert 0.0 < score < 1.0


class TestRecallAtK:
    def test_full_recall(self) -> None:
        assert recall_at_k(["a", "b", "c"], {"a", "b"}, k=3) == pytest.approx(1.0)

    def test_partial_recall(self) -> None:
        # One of two relevant hits in top-2.
        assert recall_at_k(["a", "x"], {"a", "y"}, k=2) == pytest.approx(0.5)

    def test_no_relevant_returns_zero(self) -> None:
        assert recall_at_k(["a", "b"], set(), k=2) == 0.0

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            recall_at_k(["a"], {"a"}, k=-1)


class TestPrecisionAtK:
    def test_perfect_fixed_cutoff_scores_one(self) -> None:
        assert precision_at_k(["a", "b"], {"a", "b"}, k=2) == 1.0

    def test_short_result_list_uses_fixed_k_denominator(self) -> None:
        assert precision_at_k(["a"], {"a"}, k=5) == pytest.approx(0.2)

    def test_duplicate_relevant_id_counts_once(self) -> None:
        assert precision_at_k(["a", "a"], {"a"}, k=2) == pytest.approx(0.5)

    def test_no_relevant_returns_zero(self) -> None:
        assert precision_at_k(["a"], set(), k=1) == 0.0

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            precision_at_k(["a"], {"a"}, k=0)


class TestMeanReciprocalRank:
    def test_first_relevant_gives_one(self) -> None:
        per_query = [(["a", "b", "c"], {"a"})]
        assert mean_reciprocal_rank(per_query) == pytest.approx(1.0)

    def test_relevant_at_third_gives_one_third(self) -> None:
        per_query = [(["x", "y", "a"], {"a"})]
        assert mean_reciprocal_rank(per_query) == pytest.approx(1.0 / 3.0)

    def test_average_across_queries(self) -> None:
        per_query = [
            (["a"], {"a"}),  # 1.0
            (["b", "a"], {"a"}),  # 0.5
        ]
        assert mean_reciprocal_rank(per_query) == pytest.approx(0.75)

    def test_empty_query_set_returns_zero(self) -> None:
        assert mean_reciprocal_rank([]) == 0.0

    def test_cutoff_truncates_search(self) -> None:
        # Relevant doc at position 4, cutoff at 3 -> zero contribution.
        per_query = [(["x", "y", "z", "a"], {"a"})]
        assert mean_reciprocal_rank(per_query, cutoff=3) == 0.0
        assert mean_reciprocal_rank(per_query, cutoff=4) == pytest.approx(0.25)


class TestPercentiles:
    def test_known_quantiles_on_sorted_input(self) -> None:
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        out = percentiles(samples, [0.0, 0.5, 1.0])
        assert out["p0"] == pytest.approx(10.0)
        assert out["p50"] == pytest.approx(30.0)
        assert out["p100"] == pytest.approx(50.0)

    def test_interpolated_quantile_between_two_samples(self) -> None:
        samples = [0.0, 100.0]
        out = percentiles(samples, [0.25, 0.75])
        # pos = q*(n-1) -> 0.25, 0.75. Linear interpolation on [0, 100].
        assert out["p25"] == pytest.approx(25.0)
        assert out["p75"] == pytest.approx(75.0)

    def test_empty_samples_yield_nan(self) -> None:
        out = percentiles([], [0.5, 0.95])
        assert math.isnan(out["p50"])
        assert math.isnan(out["p95"])

    def test_invalid_quantile_raises(self) -> None:
        with pytest.raises(ValueError):
            percentiles([1.0], [1.5])

    def test_p95_key_naming(self) -> None:
        samples = list(range(1, 101))
        out = percentiles(samples, [0.95, 0.99])
        assert "p95" in out
        assert "p99" in out
        # Linear-interp pos = 0.95*(99) = 94.05, between sample 94=95 and
        # 95=96 -> 95.05
        assert out["p95"] == pytest.approx(95.05, rel=1e-9)
