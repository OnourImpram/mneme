"""Unit tests for CCE salience scoring (cce.salience)."""

from __future__ import annotations

from mneme_core.cce.salience import score_item


class TestScoreItem:
    def test_kind_signal_applied_at_weight(self) -> None:
        # kind="recent_edit" with weight 1.0 and no other signals → 1.0
        score = score_item("recent_edit", {}, {"recent_edit": 1.0})
        assert score == 1.0

    def test_missing_kind_in_weights_contributes_zero(self) -> None:
        # kind not in weights, no other signals → 0.0
        score = score_item("unknown_kind", {}, {"recent_edit": 1.0})
        assert score == 0.0

    def test_explicit_signal_value_used_over_kind_default(self) -> None:
        # Explicit signal value 0.5 for "recent_edit" overrides the implicit 1.0
        score = score_item("recent_edit", {"recent_edit": 0.5}, {"recent_edit": 1.0})
        assert abs(score - 0.5) < 1e-9

    def test_weighted_combination_of_multiple_signals(self) -> None:
        weights = {"fts5_hit": 0.7, "recent_file": 0.4}
        signals = {"fts5_hit": 1.0, "recent_file": 1.0}
        # kind="other" not in weights, sum = 0.7 + 0.4 = 1.1 → clamped to 1.0
        score = score_item("other", signals, weights)
        assert score == 1.0

    def test_clamp_below_zero(self) -> None:
        # Negative weight and positive signal → negative sum → clamped to 0.0
        score = score_item("todo", {"todo": 1.0}, {"todo": -5.0})
        assert score == 0.0

    def test_clamp_above_one(self) -> None:
        weights = {"recent_edit": 1.0, "decision": 0.9}
        signals = {"recent_edit": 1.0, "decision": 1.0}
        score = score_item("other", signals, weights)
        assert score == 1.0

    def test_zero_signals_yields_zero(self) -> None:
        score = score_item("todo", {}, {})
        assert score == 0.0

    def test_deterministic_same_inputs(self) -> None:
        weights = {"recent_edit": 1.0, "fts5_hit": 0.7, "todo": 0.6}
        signals = {"fts5_hit": 0.8}
        a = score_item("todo", signals, weights)
        b = score_item("todo", signals, weights)
        assert a == b

    def test_fractional_weight_application(self) -> None:
        # kind="decision" signal value 1.0 * weight 0.9 = 0.9
        score = score_item("decision", {}, {"decision": 0.9})
        assert abs(score - 0.9) < 1e-9

    def test_signals_not_in_weights_are_ignored(self) -> None:
        # "ghost_signal" not in weights, so it does not affect score
        score = score_item("todo", {"ghost_signal": 999.0}, {"todo": 0.6})
        assert abs(score - 0.6) < 1e-9

    def test_all_default_weights_produce_valid_score(self) -> None:
        from mneme_core.cce.config import DEFAULT_SALIENCE_WEIGHTS

        for kind in DEFAULT_SALIENCE_WEIGHTS:
            score = score_item(kind, {}, DEFAULT_SALIENCE_WEIGHTS)
            assert 0.0 <= score <= 1.0, f"score out of range for kind={kind!r}"
