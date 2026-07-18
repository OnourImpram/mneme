"""Unit tests for mneme_core.temporal.graphiti_export."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from mneme_core.temporal.claim import Claim, ConfidenceLabel
from mneme_core.temporal.extract import ClaimCandidate
from mneme_core.temporal.graphiti_export import (
    episode_from_candidate,
    episode_from_claim,
    episodes_from_candidates,
    group_id_for_scope,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(
    statement: str = "The service is healthy.",
    source_path: str = "notes/status.md",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    observed_at: datetime | None = None,
    scope: str = "default",
) -> ClaimCandidate:
    return ClaimCandidate(
        statement=statement,
        source_path=source_path,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
        scope=scope,
    )


def _claim(
    statement: str = "User is based in Istanbul.",
    path: str = "notes/user.md",
    valid_from: datetime | None = None,
    observed_at: datetime | None = None,
    confidence_label: ConfidenceLabel = ConfidenceLabel.EXTRACTED,
    scope: str = "default",
) -> Claim:
    return Claim(
        claim_id="test-claim-001",
        path=path,
        statement=statement,
        statement_normalized=statement.lower(),
        valid_from=valid_from,
        valid_to=None,
        observed_at=observed_at or datetime(2026, 1, 1, tzinfo=UTC),
        supersedes=None,
        claim_key=None,
        confidence_label=confidence_label,
        trust="user",
        content_hash="abc123",
        scope=scope,
    )


# ---------------------------------------------------------------------------
# episode_from_candidate — shape
# ---------------------------------------------------------------------------


class TestEpisodeFromCandidateShape:
    def test_required_keys_present(self) -> None:
        ep = episode_from_candidate(_candidate())
        for key in (
            "name",
            "episode_body",
            "reference_time",
            "source",
            "source_description",
            "group_id",
            "confidence",
        ):
            assert key in ep, f"missing key: {key}"

    def test_source_is_mneme(self) -> None:
        ep = episode_from_candidate(_candidate())
        assert ep["source"] == "mneme"

    def test_group_id_is_mneme_temporal(self) -> None:
        ep = episode_from_candidate(_candidate())
        assert ep["group_id"] == "mneme-temporal"

    def test_non_default_scope_has_isolated_group_id(self) -> None:
        ep = episode_from_candidate(_candidate(scope="clinical"))
        assert ep["group_id"] == (
            "mneme-98569e7e9080addd9e387d4674b33830a6c516ea67a150b1f2aae304e17f7b06"
        )

    def test_source_description_is_path(self) -> None:
        ep = episode_from_candidate(_candidate(source_path="notes/ops.md"))
        assert ep["source_description"] == "notes/ops.md"

    def test_episode_body_equals_statement(self) -> None:
        stmt = "The cache is warm and ready."
        ep = episode_from_candidate(_candidate(statement=stmt))
        assert ep["episode_body"] == stmt

    def test_confidence_value_is_string(self) -> None:
        ep = episode_from_candidate(_candidate())
        assert ep["confidence"] == ConfidenceLabel.INFERRED.value
        assert ep["confidence"] == "INFERRED"


class TestEpisodeFromCandidateName:
    def test_name_is_first_60_chars(self) -> None:
        long_stmt = "A" * 80
        ep = episode_from_candidate(_candidate(statement=long_stmt))
        assert ep["name"] == "A" * 60

    def test_name_short_statement_unchanged(self) -> None:
        stmt = "Short statement here."
        ep = episode_from_candidate(_candidate(statement=stmt))
        assert ep["name"] == stmt

    def test_name_exactly_60_chars_unchanged(self) -> None:
        stmt = "X" * 60
        ep = episode_from_candidate(_candidate(statement=stmt))
        assert ep["name"] == stmt


class TestEpisodeFromCandidateReferenceTime:
    def test_valid_from_used_as_reference_time(self) -> None:
        vf = datetime(2026, 3, 15, tzinfo=UTC)
        ep = episode_from_candidate(_candidate(valid_from=vf))
        rt = ep["reference_time"]
        assert isinstance(rt, str)
        assert rt.startswith("2026-03-15")

    def test_observed_at_used_when_no_valid_from(self) -> None:
        oa = datetime(2025, 7, 4, tzinfo=UTC)
        ep = episode_from_candidate(_candidate(observed_at=oa))
        rt = ep["reference_time"]
        assert isinstance(rt, str)
        assert rt.startswith("2025-07-04")

    def test_valid_from_takes_priority_over_observed_at(self) -> None:
        vf = datetime(2026, 1, 1, tzinfo=UTC)
        oa = datetime(2020, 1, 1, tzinfo=UTC)
        ep = episode_from_candidate(_candidate(valid_from=vf, observed_at=oa))
        rt = ep["reference_time"]
        assert rt.startswith("2026-01-01")

    def test_reference_time_empty_when_no_dates(self) -> None:
        ep = episode_from_candidate(_candidate())
        assert ep["reference_time"] == ""

    def test_reference_time_is_utc_iso(self) -> None:
        vf = datetime(2026, 6, 1, 12, 30, 0, tzinfo=UTC)
        ep = episode_from_candidate(_candidate(valid_from=vf))
        rt = ep["reference_time"]
        assert "+00:00" in rt


# ---------------------------------------------------------------------------
# episodes_from_candidates — order preservation
# ---------------------------------------------------------------------------


class TestEpisodesFromCandidates:
    def test_empty_iterable_returns_empty_list(self) -> None:
        result = episodes_from_candidates([])
        assert result == []

    def test_order_preserved(self) -> None:
        cs = [
            _candidate(statement="First claim is active."),
            _candidate(statement="Second claim is pending."),
            _candidate(statement="Third claim is done."),
        ]
        eps = episodes_from_candidates(cs)
        assert len(eps) == 3
        assert eps[0]["episode_body"] == "First claim is active."
        assert eps[1]["episode_body"] == "Second claim is pending."
        assert eps[2]["episode_body"] == "Third claim is done."

    def test_generator_input_accepted(self) -> None:
        def _gen() -> ClaimCandidate:  # type: ignore[misc]
            for s in ("Alpha is ready.", "Beta is live."):
                yield _candidate(statement=s)

        eps = episodes_from_candidates(_gen())
        assert len(eps) == 2

    def test_single_candidate(self) -> None:
        eps = episodes_from_candidates([_candidate()])
        assert len(eps) == 1


# ---------------------------------------------------------------------------
# episode_from_claim — shape matches episode_from_candidate
# ---------------------------------------------------------------------------


class TestGraphitiFinalRedactionBoundary:
    def test_candidate_payload_and_path_are_redacted(self) -> None:
        episode = episode_from_candidate(
            _candidate(
                statement="Status is <private>candidate-canary</private> stable.",
                source_path="notes/<private>path-canary</private>.md",
            )
        )

        assert "candidate-canary" not in str(episode)
        assert "path-canary" not in str(episode)
        assert "[REDACTED]" in str(episode)

    def test_claim_payload_and_path_are_redacted(self) -> None:
        episode = episode_from_claim(
            _claim(
                statement="Status is <private>claim-canary</private> stable.",
                path="notes/<private>claim-path-canary</private>.md",
            )
        )

        assert "claim-canary" not in str(episode)
        assert "claim-path-canary" not in str(episode)
        assert "[REDACTED]" in str(episode)


class TestEpisodeFromClaim:
    def test_required_keys_present(self) -> None:
        ep = episode_from_claim(_claim())
        for key in (
            "name",
            "episode_body",
            "reference_time",
            "source",
            "source_description",
            "group_id",
            "confidence",
        ):
            assert key in ep, f"missing key: {key}"

    def test_source_is_mneme(self) -> None:
        ep = episode_from_claim(_claim())
        assert ep["source"] == "mneme"

    def test_group_id_is_mneme_temporal(self) -> None:
        ep = episode_from_claim(_claim())
        assert ep["group_id"] == "mneme-temporal"

    def test_non_default_scope_has_isolated_group_id(self) -> None:
        ep = episode_from_claim(_claim(scope="research"))
        assert ep["group_id"] == (
            "mneme-66f62d1807d3821a3865f2573b69c74be033f1341240ac861fefc6d430bff5e0"
        )


class TestGroupIdForScope:
    def test_arbitrary_printable_scope_uses_graphiti_safe_grammar(self) -> None:
        group_id = group_id_for_scope("Clinical / İstanbul")
        assert group_id == (
            "mneme-ef48898e4bef242fbe5c13e4c00230a74c085f094ae2a6e0ed8ef25cc6308a25"
        )
        assert group_id.replace("-", "").replace("_", "").isalnum()

    def test_wildcard_is_never_writable(self) -> None:
        with pytest.raises(ValueError, match="cross-scope read marker"):
            group_id_for_scope("*")

    @pytest.mark.parametrize("scope", [" clinical ", "clinical*", "\u200bclinical"])
    def test_malformed_scope_is_never_exported(self, scope: str) -> None:
        with pytest.raises(ValueError, match="concrete valid identifier"):
            group_id_for_scope(scope)

    def test_source_description_is_path(self) -> None:
        ep = episode_from_claim(_claim(path="notes/facts.md"))
        assert ep["source_description"] == "notes/facts.md"

    def test_episode_body_equals_statement(self) -> None:
        stmt = "The deployment was successful."
        ep = episode_from_claim(_claim(statement=stmt))
        assert ep["episode_body"] == stmt

    def test_confidence_extracted(self) -> None:
        ep = episode_from_claim(_claim(confidence_label=ConfidenceLabel.EXTRACTED))
        assert ep["confidence"] == "EXTRACTED"

    def test_confidence_inferred(self) -> None:
        ep = episode_from_claim(_claim(confidence_label=ConfidenceLabel.INFERRED))
        assert ep["confidence"] == "INFERRED"

    def test_valid_from_used_as_reference_time(self) -> None:
        vf = datetime(2025, 9, 1, tzinfo=UTC)
        ep = episode_from_claim(_claim(valid_from=vf))
        rt = ep["reference_time"]
        assert isinstance(rt, str)
        assert rt.startswith("2025-09-01")

    def test_observed_at_used_when_no_valid_from(self) -> None:
        oa = datetime(2024, 4, 20, tzinfo=UTC)
        ep = episode_from_claim(_claim(observed_at=oa))
        rt = ep["reference_time"]
        assert rt.startswith("2024-04-20")

    def test_name_truncated_to_60_chars(self) -> None:
        stmt = "Z" * 80
        ep = episode_from_claim(_claim(statement=stmt))
        assert ep["name"] == "Z" * 60


# ---------------------------------------------------------------------------
# No neo4j import anywhere in the module
# ---------------------------------------------------------------------------


class TestNoNeo4jImport:
    def test_graphiti_export_does_not_import_neo4j(self) -> None:
        import mneme_core.temporal.graphiti_export  # noqa: F401 — side-effect import

        for mod_name in sys.modules:
            assert not mod_name.startswith("neo4j"), (
                f"neo4j module '{mod_name}' was imported by graphiti_export"
            )

    def test_extract_does_not_import_neo4j(self) -> None:
        import mneme_core.temporal.extract  # noqa: F401

        for mod_name in sys.modules:
            assert not mod_name.startswith("neo4j"), (
                f"neo4j module '{mod_name}' was imported by extract"
            )
