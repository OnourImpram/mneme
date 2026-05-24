"""Tests for adaptive top-k retrieval count."""

from __future__ import annotations

import pytest

from mneme_core.distill.adaptive_topk import (
    DEFAULT_HIGH_USAGE_THRESHOLD,
    DEFAULT_LOW_USAGE_THRESHOLD,
    DEFAULT_MAX_K,
    DEFAULT_MIN_K,
    TopKPolicy,
    adaptive_topk,
)


class TestBoundaryClamping:
    def test_zero_usage_returns_max_k(self) -> None:
        assert adaptive_topk(0) == DEFAULT_MAX_K

    def test_low_threshold_returns_max_k(self) -> None:
        assert adaptive_topk(DEFAULT_LOW_USAGE_THRESHOLD) == DEFAULT_MAX_K

    def test_high_threshold_returns_min_k(self) -> None:
        assert adaptive_topk(DEFAULT_HIGH_USAGE_THRESHOLD) == DEFAULT_MIN_K

    def test_above_high_threshold_clamps(self) -> None:
        assert adaptive_topk(DEFAULT_HIGH_USAGE_THRESHOLD * 3) == DEFAULT_MIN_K

    def test_negative_treated_as_zero(self) -> None:
        assert adaptive_topk(-100) == DEFAULT_MAX_K


class TestInterpolation:
    def test_midpoint_between_defaults(self) -> None:
        mid = (DEFAULT_LOW_USAGE_THRESHOLD + DEFAULT_HIGH_USAGE_THRESHOLD) // 2
        result = adaptive_topk(mid)
        # Midpoint should land roughly halfway between min_k and max_k.
        approx_mid = (DEFAULT_MAX_K + DEFAULT_MIN_K) // 2
        assert abs(result - approx_mid) <= 1

    def test_monotonic_decreasing(self) -> None:
        a = adaptive_topk(10_000)
        b = adaptive_topk(20_000)
        c = adaptive_topk(40_000)
        assert a >= b >= c


class TestCustomPolicy:
    def test_custom_range_honored(self) -> None:
        policy = TopKPolicy(min_k=1, max_k=20, low_usage_threshold=1_000, high_usage_threshold=10_000)
        assert adaptive_topk(500, policy) == 20
        assert adaptive_topk(10_000, policy) == 1
        assert 1 < adaptive_topk(5_500, policy) < 20

    def test_invalid_policy_min_greater_than_max(self) -> None:
        with pytest.raises(ValueError, match="min_k"):
            TopKPolicy(min_k=10, max_k=3)

    def test_invalid_policy_threshold_order(self) -> None:
        with pytest.raises(ValueError, match="threshold"):
            TopKPolicy(low_usage_threshold=10_000, high_usage_threshold=5_000)
