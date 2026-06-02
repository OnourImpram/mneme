"""Unit tests for mneme_core.temporal.extract."""

from __future__ import annotations

from dataclasses import dataclass

from mneme_core.compression.llm import CompressionResult, LlmCallSpec, LlmProviderError
from mneme_core.temporal.claim import ConfidenceLabel
from mneme_core.temporal.extract import extract_claims, extract_claims_rule_based

# ---------------------------------------------------------------------------
# Helpers / fake providers
# ---------------------------------------------------------------------------


@dataclass
class _FakeProvider:
    """Injects a canned JSON string as the LLM response.  No network."""

    response_text: str

    def compress(self, spec: LlmCallSpec) -> CompressionResult:  # noqa: ARG002
        return CompressionResult(
            text=self.response_text,
            tokens_in=10,
            tokens_out=5,
            model="fake-model",
        )


class _ErrorProvider:
    """Always raises LlmProviderError."""

    def compress(self, spec: LlmCallSpec) -> CompressionResult:  # noqa: ARG002
        raise LlmProviderError("provider unavailable")


class _JunkProvider:
    """Returns a non-JSON response."""

    def compress(self, spec: LlmCallSpec) -> CompressionResult:  # noqa: ARG002
        return CompressionResult(
            text="this is not json at all!!!",
            tokens_in=3,
            tokens_out=3,
            model="fake-model",
        )


class _NonListProvider:
    """Returns valid JSON but not a list."""

    def compress(self, spec: LlmCallSpec) -> CompressionResult:  # noqa: ARG002
        return CompressionResult(
            text='{"statement": "not wrapped in array"}',
            tokens_in=3,
            tokens_out=3,
            model="fake-model",
        )


# ---------------------------------------------------------------------------
# Rule-based extractor
# ---------------------------------------------------------------------------


class TestRuleBasedCoprula:
    def test_copula_is_emits_candidate(self) -> None:
        candidates = extract_claims_rule_based(
            "The service is healthy.", source_path="notes/status.md"
        )
        assert len(candidates) >= 1

    def test_copula_was_emits_candidate(self) -> None:
        candidates = extract_claims_rule_based(
            "The server was unreachable yesterday.", source_path="notes/ops.md"
        )
        assert len(candidates) >= 1

    def test_copula_will_be_emits_candidate(self) -> None:
        candidates = extract_claims_rule_based(
            "The feature will be available soon.", source_path="notes/roadmap.md"
        )
        assert len(candidates) >= 1

    def test_confidence_is_inferred(self) -> None:
        candidates = extract_claims_rule_based(
            "The service is healthy.", source_path="notes/status.md"
        )
        assert len(candidates) >= 1
        for c in candidates:
            assert c.confidence == ConfidenceLabel.INFERRED

    def test_source_path_stored(self) -> None:
        candidates = extract_claims_rule_based(
            "The service is healthy.", source_path="notes/status.md"
        )
        assert len(candidates) >= 1
        assert all(c.source_path == "notes/status.md" for c in candidates)

    def test_claim_key_is_none(self) -> None:
        candidates = extract_claims_rule_based(
            "The service is healthy.", source_path="notes/status.md"
        )
        assert len(candidates) >= 1
        assert all(c.claim_key is None for c in candidates)


class TestRuleBasedRedaction:
    def test_private_tag_redacted_from_statement(self) -> None:
        body = "The system is <private>classified-secret</private> and operational."
        candidates = extract_claims_rule_based(body, source_path="notes/sec.md")
        assert len(candidates) >= 1
        for c in candidates:
            assert "classified-secret" not in c.statement
            assert "[REDACTED]" in c.statement

    def test_private_tag_various_forms_redacted(self) -> None:
        body = "Location is <PRIVATE reason=\"x\">Istanbul</PRIVATE> confirmed."
        candidates = extract_claims_rule_based(body, source_path="notes/loc.md")
        assert len(candidates) >= 1
        for c in candidates:
            assert "Istanbul" not in c.statement


class TestRuleBasedDatedQualifiers:
    def test_since_sets_valid_from(self) -> None:
        candidates = extract_claims_rule_based(
            "Active since 2026-01-15.", source_path="notes/log.md"
        )
        assert len(candidates) >= 1
        vf_dates = [c.valid_from for c in candidates if c.valid_from is not None]
        assert len(vf_dates) >= 1
        assert vf_dates[0].year == 2026
        assert vf_dates[0].month == 1
        assert vf_dates[0].day == 15

    def test_as_of_sets_valid_from(self) -> None:
        candidates = extract_claims_rule_based(
            "As of 2025-06-01 the policy is active.", source_path="notes/policy.md"
        )
        assert len(candidates) >= 1
        vf_dates = [c.valid_from for c in candidates if c.valid_from is not None]
        assert len(vf_dates) >= 1
        assert vf_dates[0].year == 2025

    def test_effective_sets_valid_from(self) -> None:
        candidates = extract_claims_rule_based(
            "Effective 2024-03-01 the rate is changed.", source_path="notes/rate.md"
        )
        assert len(candidates) >= 1
        vf_dates = [c.valid_from for c in candidates if c.valid_from is not None]
        assert len(vf_dates) >= 1

    def test_until_sets_valid_to(self) -> None:
        candidates = extract_claims_rule_based(
            "Valid until 2026-12-31.", source_path="notes/validity.md"
        )
        assert len(candidates) >= 1
        vt_dates = [c.valid_to for c in candidates if c.valid_to is not None]
        assert len(vt_dates) >= 1
        assert vt_dates[0].year == 2026
        assert vt_dates[0].month == 12
        assert vt_dates[0].day == 31

    def test_through_sets_valid_to(self) -> None:
        candidates = extract_claims_rule_based(
            "Contract runs through 2027-06-30 and is binding.",
            source_path="notes/contract.md",
        )
        assert len(candidates) >= 1
        vt_dates = [c.valid_to for c in candidates if c.valid_to is not None]
        assert len(vt_dates) >= 1
        assert vt_dates[0].year == 2027


class TestRuleBasedEdgeCases:
    def test_two_word_fragment_skipped(self) -> None:
        candidates = extract_claims_rule_based("Is healthy.", source_path="notes/x.md")
        # "Is healthy." has only 2 tokens → skipped
        assert candidates == []

    def test_single_word_skipped(self) -> None:
        candidates = extract_claims_rule_based("Running.", source_path="notes/x.md")
        assert candidates == []

    def test_empty_text_returns_empty_list(self) -> None:
        candidates = extract_claims_rule_based("", source_path="notes/x.md")
        assert candidates == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        candidates = extract_claims_rule_based("   \n\n  ", source_path="notes/x.md")
        assert candidates == []

    def test_no_factual_sentence_returns_empty(self) -> None:
        body = "This is a question? Maybe something else here."
        # "This is a question" has copula "is" so it WILL match — use truly plain text:
        body = "Run the script now. Check the output carefully."
        candidates = extract_claims_rule_based(body, source_path="notes/x.md")
        # No copula, no dated qualifier → empty
        assert candidates == []

    def test_deterministic_same_result_twice(self) -> None:
        body = "The deployment is complete. Active since 2026-01-01 and stable."
        first = extract_claims_rule_based(body, source_path="notes/dep.md")
        second = extract_claims_rule_based(body, source_path="notes/dep.md")
        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a.statement == b.statement
            assert a.valid_from == b.valid_from
            assert a.valid_to == b.valid_to

    def test_document_order_preserved(self) -> None:
        body = "The first service is live. The second service is pending."
        candidates = extract_claims_rule_based(body, source_path="notes/order.md")
        assert len(candidates) >= 2
        assert "first" in candidates[0].statement
        assert "second" in candidates[1].statement


# ---------------------------------------------------------------------------
# extract_claims — provider=None delegates to rule_based
# ---------------------------------------------------------------------------


class TestExtractClaimsNoProvider:
    def test_no_provider_equals_rule_based(self) -> None:
        body = "The service is healthy."
        rb = extract_claims_rule_based(body, source_path="notes/s.md")
        ec = extract_claims(body, source_path="notes/s.md")
        assert len(rb) == len(ec)
        for a, b in zip(rb, ec, strict=True):
            assert a.statement == b.statement

    def test_empty_text_no_provider(self) -> None:
        assert extract_claims("", source_path="notes/s.md") == []


# ---------------------------------------------------------------------------
# extract_claims — with fake provider (no network)
# ---------------------------------------------------------------------------


class TestExtractClaimsWithFakeProvider:
    def test_parses_json_array_from_provider(self) -> None:
        canned = '[{"statement": "The server is active.", "valid_from": "2026-01-01"}]'
        provider = _FakeProvider(response_text=canned)
        candidates = extract_claims(
            "anything", source_path="notes/p.md", provider=provider
        )
        assert len(candidates) == 1
        assert candidates[0].confidence == ConfidenceLabel.INFERRED
        assert candidates[0].source_path == "notes/p.md"
        assert candidates[0].valid_from is not None
        assert candidates[0].valid_from.year == 2026

    def test_statements_are_redacted(self) -> None:
        canned = '[{"statement": "Location is <private>secret-city</private> confirmed."}]'
        provider = _FakeProvider(response_text=canned)
        candidates = extract_claims(
            "anything", source_path="notes/p.md", provider=provider
        )
        assert len(candidates) == 1
        assert "secret-city" not in candidates[0].statement
        assert "[REDACTED]" in candidates[0].statement

    def test_multiple_candidates_from_provider(self) -> None:
        canned = (
            '[{"statement": "The API is stable.", "valid_from": "2025-01-01"},'
            ' {"statement": "The cache was cleared.", "valid_to": "2025-06-30"}]'
        )
        provider = _FakeProvider(response_text=canned)
        candidates = extract_claims(
            "anything", source_path="notes/multi.md", provider=provider
        )
        assert len(candidates) == 2
        assert candidates[0].valid_from is not None
        assert candidates[1].valid_to is not None

    def test_confidence_inferred_from_provider(self) -> None:
        canned = '[{"statement": "The system is operational."}]'
        provider = _FakeProvider(response_text=canned)
        candidates = extract_claims(
            "anything", source_path="notes/p.md", provider=provider
        )
        assert len(candidates) == 1
        assert candidates[0].confidence == ConfidenceLabel.INFERRED

    def test_empty_array_from_provider_returns_empty(self) -> None:
        provider = _FakeProvider(response_text="[]")
        candidates = extract_claims(
            "anything", source_path="notes/p.md", provider=provider
        )
        assert candidates == []

    def test_valid_to_parsed_from_provider(self) -> None:
        canned = '[{"statement": "Access valid until expiry.", "valid_to": "2027-03-15"}]'
        provider = _FakeProvider(response_text=canned)
        candidates = extract_claims(
            "anything", source_path="notes/p.md", provider=provider
        )
        assert len(candidates) == 1
        assert candidates[0].valid_to is not None
        assert candidates[0].valid_to.year == 2027


# ---------------------------------------------------------------------------
# extract_claims — graceful fallback on provider errors
# ---------------------------------------------------------------------------


class TestExtractClaimsFallback:
    def test_llm_provider_error_falls_back_no_raise(self) -> None:
        provider = _ErrorProvider()
        # Should not raise; falls back to rule-based.
        candidates = extract_claims(
            "The service is healthy.", source_path="notes/s.md", provider=provider
        )
        # Rule-based should find at least one candidate for this sentence.
        assert isinstance(candidates, list)
        assert len(candidates) >= 1

    def test_junk_json_falls_back_no_raise(self) -> None:
        provider = _JunkProvider()
        candidates = extract_claims(
            "The service is healthy.", source_path="notes/s.md", provider=provider
        )
        assert isinstance(candidates, list)
        assert len(candidates) >= 1

    def test_non_list_json_falls_back_no_raise(self) -> None:
        provider = _NonListProvider()
        candidates = extract_claims(
            "The service is healthy.", source_path="notes/s.md", provider=provider
        )
        assert isinstance(candidates, list)
        assert len(candidates) >= 1

    def test_fallback_preserves_source_path(self) -> None:
        provider = _ErrorProvider()
        candidates = extract_claims(
            "The service is healthy.",
            source_path="notes/important.md",
            provider=provider,
        )
        assert all(c.source_path == "notes/important.md" for c in candidates)
