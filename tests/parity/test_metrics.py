"""Unit tests for parity comparison metrics."""

from __future__ import annotations

import pytest

from tools.parity.metrics import (
    kendall_tau,
    parity_summary,
    top_k_jaccard,
    top_n_agreement,
)


class TestTopNAgreement:
    def test_identical_top_1(self) -> None:
        assert top_n_agreement(["a", "b", "c"], ["a", "x", "y"], n=1) == 1.0

    def test_different_top_1(self) -> None:
        assert top_n_agreement(["a", "b"], ["c", "a"], n=1) == 0.0

    def test_set_match_top_3(self) -> None:
        # same three docs in different order
        assert top_n_agreement(["a", "b", "c"], ["c", "b", "a"], n=3) == 1.0

    def test_empty_lists(self) -> None:
        assert top_n_agreement([], [], n=1) == 0.0

    def test_one_empty(self) -> None:
        assert top_n_agreement(["a"], [], n=1) == 0.0


class TestTopKJaccard:
    def test_identical(self) -> None:
        assert top_k_jaccard(["a", "b", "c"], ["a", "b", "c"], k=5) == 1.0

    def test_disjoint(self) -> None:
        assert top_k_jaccard(["a", "b"], ["c", "d"], k=5) == 0.0

    def test_partial_overlap(self) -> None:
        # intersection {a, b} = 2, union {a, b, c, d, e} = 5, Jaccard = 0.4
        assert top_k_jaccard(["a", "b", "c"], ["a", "b", "d", "e"], k=5) == 0.4

    def test_both_empty(self) -> None:
        assert top_k_jaccard([], [], k=5) == 1.0

    def test_one_empty(self) -> None:
        assert top_k_jaccard(["a"], [], k=5) == 0.0

    def test_respects_k(self) -> None:
        # beyond k=2 the c and d shouldn't count
        a = ["a", "b", "c"]
        b = ["a", "b", "d"]
        assert top_k_jaccard(a, b, k=2) == 1.0


class TestKendallTau:
    def test_identical_ordering(self) -> None:
        assert kendall_tau(["a", "b", "c"], ["a", "b", "c"], k=5) == 1.0

    def test_fully_inverted(self) -> None:
        assert kendall_tau(["a", "b", "c"], ["c", "b", "a"], k=5) == -1.0

    def test_no_overlap(self) -> None:
        assert kendall_tau(["a", "b"], ["c", "d"], k=5) == 0.0

    def test_single_common_doc(self) -> None:
        # only one doc in common -> no pairs to compare -> 1.0
        assert kendall_tau(["a", "b"], ["a", "c"], k=5) == 1.0

    def test_mixed_concordance(self) -> None:
        # ordering: a < b < c vs a < c < b: a-b concordant, a-c concordant,
        # b-c discordant -> 2/3 concordant -> tau = 2*(2/3) - 1 = 1/3
        result = kendall_tau(["a", "b", "c"], ["a", "c", "b"], k=5)
        assert result == pytest.approx(1.0 / 3.0, rel=1e-6)


class TestParitySummary:
    def test_empty_input(self) -> None:
        result = parity_summary([])
        assert result["comparable"] is False
        assert result["queries_total"] == 0

    def test_all_perfect_agreement(self) -> None:
        per_query = [
            {"top_1_agreement": 1.0, "top_5_jaccard": 1.0, "kendall_tau": 1.0}
            for _ in range(10)
        ]
        result = parity_summary(per_query)
        assert result["comparable"] is True
        assert result["mean_top_1_agreement"] == 1.0
        assert result["mean_top_5_jaccard"] == 1.0
        assert result["divergent_query_count"] == 0
        assert result["gate_passed"] is True

    def test_all_divergent_fails_gate(self) -> None:
        per_query = [
            {"top_1_agreement": 0.0, "top_5_jaccard": 0.0, "kendall_tau": 0.0}
            for _ in range(10)
        ]
        result = parity_summary(per_query)
        assert result["divergent_query_count"] == 10
        assert result["divergent_query_fraction"] == 1.0
        assert result["gate_passed"] is False

    def test_five_percent_boundary(self) -> None:
        # 1 of 20 divergent = 5% exactly -> gate passes (<=, not <)
        per_query = [
            {"top_1_agreement": 1.0, "top_5_jaccard": 1.0, "kendall_tau": 1.0}
            for _ in range(19)
        ] + [{"top_1_agreement": 0.0, "top_5_jaccard": 0.0, "kendall_tau": 0.0}]
        result = parity_summary(per_query)
        assert result["divergent_query_fraction"] == 0.05
        assert result["gate_passed"] is True

    def test_six_percent_fails(self) -> None:
        # 2 of 20 divergent = 10% -> fails
        per_query = [
            {"top_1_agreement": 1.0, "top_5_jaccard": 1.0, "kendall_tau": 1.0}
            for _ in range(18)
        ] + [
            {"top_1_agreement": 0.0, "top_5_jaccard": 0.0, "kendall_tau": 0.0}
            for _ in range(2)
        ]
        result = parity_summary(per_query)
        assert result["divergent_query_fraction"] == 0.10
        assert result["gate_passed"] is False
