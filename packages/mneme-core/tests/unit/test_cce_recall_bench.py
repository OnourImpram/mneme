"""Unit tests for the compaction-recall benchmark logic.

Tests are in-memory where possible (no real filesystem I/O in the metric
computation path), using tmp_path only for the JSONL transcript that
``detect_dropped`` reads.

Assertions:
1. self-heal recall > baseline recall on a seeded fixture.
2. Determinism: same seed always produces the same numbers.
3. Negative probe: recovered texts are a subset of the original checkpoint
   texts (self-heal never invents a fact).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing the benchmark module directly without installing it.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))
_BENCH_ROOT = _REPO_ROOT / "benchmarks" / "compaction-recall"
sys.path.insert(0, str(_BENCH_ROOT))

from run import (  # type: ignore[import-not-found]  # noqa: E402
    _K_FACTS,
    _build_checkpoint,
    _build_facts,
    _choose_survivors,
    _compute_baseline_recall,
    _compute_selfheal_recall,
    _negative_probe,
    _write_post_compaction_transcript,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_fixture(
    seed: int,
    k: int,
    token_budget: int,
    tmp_path: Path,
) -> tuple[float, float, frozenset[str], frozenset[str]]:
    """Run the full benchmark pipeline and return (baseline, selfheal, recovered, original)."""
    items = _build_facts(seed, k)
    checkpoint = _build_checkpoint(items, seed)
    survivor_indices = _choose_survivors(items, seed)

    tmp_path.mkdir(parents=True, exist_ok=True)
    transcript_path = tmp_path / f"transcript_{seed}.jsonl"
    _write_post_compaction_transcript(items, survivor_indices, transcript_path)

    baseline = _compute_baseline_recall(items, survivor_indices)
    selfheal, recovered = _compute_selfheal_recall(
        items, survivor_indices, checkpoint, transcript_path, token_budget
    )
    original = frozenset(item.text for item in items)
    return baseline, selfheal, recovered, original


# ---------------------------------------------------------------------------
# Core assertion: self-heal improves recall
# ---------------------------------------------------------------------------


class TestSelfHealImproves:
    def test_selfheal_recall_exceeds_baseline(self, tmp_path: Path) -> None:
        """Primary invariant: self-heal recall must be strictly greater than baseline."""
        baseline, selfheal, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path)
        assert selfheal > baseline, (
            f"self-heal recall {selfheal} must exceed baseline {baseline}"
        )

    def test_baseline_recall_equals_survival_rate(self, tmp_path: Path) -> None:
        """Baseline recall (no self-heal) equals the survival fraction by definition."""
        items = _build_facts(42, _K_FACTS)
        survivor_indices = _choose_survivors(items, 42)
        baseline = _compute_baseline_recall(items, survivor_indices)
        expected = len(survivor_indices) / len(items)
        assert abs(baseline - expected) < 1e-9

    def test_selfheal_recall_bounded_by_one(self, tmp_path: Path) -> None:
        """Recall must never exceed 1.0."""
        _, selfheal, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path)
        assert selfheal <= 1.0

    def test_headline_gain_positive(self, tmp_path: Path) -> None:
        """Headline gain (selfheal - baseline) must be positive."""
        baseline, selfheal, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path)
        gain = selfheal - baseline
        assert gain > 0.0


# ---------------------------------------------------------------------------
# Determinism: same seed → same numbers
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_baseline(self, tmp_path: Path) -> None:
        b1, _, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r1")
        b2, _, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r2")
        assert b1 == b2

    def test_same_seed_same_selfheal(self, tmp_path: Path) -> None:
        _, s1, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r1")
        _, s2, _, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r2")
        assert s1 == s2

    def test_same_seed_same_recovered_set(self, tmp_path: Path) -> None:
        _, _, rec1, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r1")
        _, _, rec2, _ = _run_fixture(42, _K_FACTS, 4000, tmp_path / "r2")
        assert rec1 == rec2

    def test_different_seeds_different_facts(self) -> None:
        items_a = _build_facts(42, _K_FACTS)
        items_b = _build_facts(99, _K_FACTS)
        texts_a = {i.text for i in items_a}
        texts_b = {i.text for i in items_b}
        # Different seeds produce different item texts.
        assert texts_a != texts_b


# ---------------------------------------------------------------------------
# Negative probe: recovered ⊆ original (no invented facts)
# ---------------------------------------------------------------------------


class TestNegativeProbe:
    def test_recovered_subset_of_original_seed42(self, tmp_path: Path) -> None:
        """Self-heal must not invent any fact absent from the original checkpoint."""
        _, _, recovered, original = _run_fixture(42, _K_FACTS, 4000, tmp_path)
        assert recovered <= original, (
            f"Invented facts detected: {recovered - original}"
        )

    def test_negative_probe_helper_passes_on_valid_input(self) -> None:
        original = frozenset(["fact-A", "fact-B", "fact-C"])
        recovered = frozenset(["fact-A", "fact-B"])
        result = _negative_probe(recovered, original)
        assert result["all_passed"] is True
        assert result["invented_count"] == 0

    def test_negative_probe_helper_fails_on_invented_fact(self) -> None:
        original = frozenset(["fact-A", "fact-B"])
        recovered = frozenset(["fact-A", "fact-INVENTED"])
        result = _negative_probe(recovered, original)
        assert result["all_passed"] is False
        assert result["invented_count"] == 1

    def test_negative_probe_empty_recovery(self) -> None:
        original = frozenset(["fact-A"])
        recovered: frozenset[str] = frozenset()
        result = _negative_probe(recovered, original)
        assert result["all_passed"] is True
        assert result["invented_count"] == 0

    def test_no_invented_facts_across_multiple_seeds(self, tmp_path: Path) -> None:
        """Run across several seeds to confirm the invariant holds broadly."""
        for seed in (0, 1, 7, 13, 99):
            _, _, recovered, original = _run_fixture(
                seed, _K_FACTS, 4000, tmp_path / f"seed_{seed}"
            )
            assert recovered <= original, (
                f"Seed {seed}: invented facts {recovered - original}"
            )


# ---------------------------------------------------------------------------
# Token budget boundary
# ---------------------------------------------------------------------------


class TestTokenBudget:
    def test_zero_budget_no_recovery(self, tmp_path: Path) -> None:
        """With a zero token budget no dropped items can be rehydrated."""
        items = _build_facts(42, _K_FACTS)
        checkpoint = _build_checkpoint(items, 42)
        survivor_indices = _choose_survivors(items, 42)
        transcript_path = tmp_path / "t.jsonl"
        _write_post_compaction_transcript(items, survivor_indices, transcript_path)

        baseline = _compute_baseline_recall(items, survivor_indices)
        selfheal, recovered = _compute_selfheal_recall(
            items, survivor_indices, checkpoint, transcript_path, token_budget=0
        )
        assert selfheal == baseline
        assert recovered == frozenset()

    def test_large_budget_recovers_more_than_small(self, tmp_path: Path) -> None:
        """A larger budget should recover at least as much as a smaller one."""
        items = _build_facts(42, _K_FACTS)
        checkpoint = _build_checkpoint(items, 42)
        survivor_indices = _choose_survivors(items, 42)

        tp_small = tmp_path / "small.jsonl"
        tp_large = tmp_path / "large.jsonl"
        _write_post_compaction_transcript(items, survivor_indices, tp_small)
        _write_post_compaction_transcript(items, survivor_indices, tp_large)

        selfheal_small, _ = _compute_selfheal_recall(
            items, survivor_indices, checkpoint, tp_small, token_budget=50
        )
        selfheal_large, _ = _compute_selfheal_recall(
            items, survivor_indices, checkpoint, tp_large, token_budget=4000
        )
        assert selfheal_large >= selfheal_small
