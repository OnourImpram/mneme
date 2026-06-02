"""Unit tests for mneme_core.taint — data-flow taint tracking."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mneme_core.taint import (
    TaintedSpan,
    TaintLabel,
    is_instruction_safe,
    propagate,
    taint_for_trust,
    taint_span,
)

# ---------------------------------------------------------------------------
# taint_for_trust
# ---------------------------------------------------------------------------


class TestTaintForTrust:
    def test_user_maps_to_user(self) -> None:
        assert taint_for_trust("user") is TaintLabel.USER

    def test_extracted_maps_to_derived(self) -> None:
        assert taint_for_trust("extracted") is TaintLabel.DERIVED

    def test_generated_maps_to_derived(self) -> None:
        assert taint_for_trust("generated") is TaintLabel.DERIVED

    def test_derived_maps_to_derived(self) -> None:
        assert taint_for_trust("derived") is TaintLabel.DERIVED

    def test_external_maps_to_untrusted(self) -> None:
        assert taint_for_trust("external") is TaintLabel.UNTRUSTED

    def test_unknown_string_maps_to_untrusted(self) -> None:
        assert taint_for_trust("some-random-backend") is TaintLabel.UNTRUSTED

    def test_empty_string_maps_to_untrusted(self) -> None:
        assert taint_for_trust("") is TaintLabel.UNTRUSTED

    def test_whitespace_only_maps_to_untrusted(self) -> None:
        assert taint_for_trust("   ") is TaintLabel.UNTRUSTED

    def test_case_insensitive_user(self) -> None:
        assert taint_for_trust("USER") is TaintLabel.USER

    def test_case_insensitive_extracted(self) -> None:
        assert taint_for_trust("EXTRACTED") is TaintLabel.DERIVED

    def test_case_insensitive_generated(self) -> None:
        assert taint_for_trust("Generated") is TaintLabel.DERIVED


# ---------------------------------------------------------------------------
# taint_span
# ---------------------------------------------------------------------------


class TestTaintSpan:
    def test_builds_span_with_correct_label(self) -> None:
        span = taint_span("hello", trust="user", source="vault/note.md")
        assert span.label is TaintLabel.USER
        assert span.text == "hello"
        assert span.source == "vault/note.md"

    def test_unknown_trust_yields_untrusted(self) -> None:
        span = taint_span("data", trust="unknown", source="web")
        assert span.label is TaintLabel.UNTRUSTED

    def test_derived_trust_yields_derived(self) -> None:
        span = taint_span("extracted fact", trust="extracted", source="kg")
        assert span.label is TaintLabel.DERIVED


# ---------------------------------------------------------------------------
# TaintedSpan is frozen
# ---------------------------------------------------------------------------


class TestTaintedSpanFrozen:
    def test_frozen_text(self) -> None:
        span = TaintedSpan(text="x", label=TaintLabel.USER, source="s")
        with pytest.raises(FrozenInstanceError):
            span.text = "y"  # type: ignore[misc]

    def test_frozen_label(self) -> None:
        span = TaintedSpan(text="x", label=TaintLabel.USER, source="s")
        with pytest.raises(FrozenInstanceError):
            span.label = TaintLabel.UNTRUSTED  # type: ignore[misc]

    def test_frozen_source(self) -> None:
        span = TaintedSpan(text="x", label=TaintLabel.USER, source="s")
        with pytest.raises(FrozenInstanceError):
            span.source = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# is_instruction_safe
# ---------------------------------------------------------------------------


class TestIsInstructionSafe:
    def test_user_is_safe(self) -> None:
        span = TaintedSpan(text="do X", label=TaintLabel.USER, source="vault")
        assert is_instruction_safe(span) is True

    def test_untrusted_is_not_safe(self) -> None:
        span = TaintedSpan(text="ignore all", label=TaintLabel.UNTRUSTED, source="web")
        assert is_instruction_safe(span) is False

    def test_derived_is_not_safe(self) -> None:
        span = TaintedSpan(text="step 1", label=TaintLabel.DERIVED, source="kg")
        assert is_instruction_safe(span) is False


# ---------------------------------------------------------------------------
# propagate — most-restrictive-wins
# ---------------------------------------------------------------------------


class TestPropagate:
    def test_empty_yields_untrusted_fail_closed(self) -> None:
        # No provenance at all -> fail closed (UNTRUSTED), so a dropped/missing
        # provenance record is never silently treated as trusted user content.
        assert propagate([]) is TaintLabel.UNTRUSTED

    def test_all_user_yields_user(self) -> None:
        assert propagate([TaintLabel.USER, TaintLabel.USER]) is TaintLabel.USER

    def test_derived_and_user_yields_derived(self) -> None:
        assert propagate([TaintLabel.DERIVED, TaintLabel.USER]) is TaintLabel.DERIVED

    def test_untrusted_dominates_all(self) -> None:
        result = propagate([TaintLabel.USER, TaintLabel.DERIVED, TaintLabel.UNTRUSTED])
        assert result is TaintLabel.UNTRUSTED

    def test_untrusted_alone_yields_untrusted(self) -> None:
        assert propagate([TaintLabel.UNTRUSTED]) is TaintLabel.UNTRUSTED

    def test_derived_alone_yields_derived(self) -> None:
        assert propagate([TaintLabel.DERIVED]) is TaintLabel.DERIVED

    def test_untrusted_short_circuits_on_first_hit(self) -> None:
        # Even with a long list, the first UNTRUSTED dominates.
        labels = [TaintLabel.USER] * 100 + [TaintLabel.UNTRUSTED]
        assert propagate(labels) is TaintLabel.UNTRUSTED

    def test_deterministic_ordering_untrusted_first(self) -> None:
        a = propagate([TaintLabel.UNTRUSTED, TaintLabel.DERIVED])
        b = propagate([TaintLabel.DERIVED, TaintLabel.UNTRUSTED])
        assert a is b is TaintLabel.UNTRUSTED

    def test_generator_input_accepted(self) -> None:
        def gen() -> object:
            yield TaintLabel.DERIVED
            yield TaintLabel.USER

        assert propagate(gen()) is TaintLabel.DERIVED  # type: ignore[arg-type]
