"""Unit tests for mneme_core.temporal.claim."""

from __future__ import annotations

from typing import Any

import pytest

from mneme_core.temporal.claim import Claim, ConfidenceLabel, claim_from_note

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fm(**kwargs: Any) -> dict[str, Any]:
    """Build a minimal frontmatter dict for a claim note."""
    base = {
        "id": "test-001",
        "type": "claim",
        "created": "2024-01-01T00:00:00+00:00",
    }
    base.update(kwargs)
    return base


def _make(
    frontmatter: dict[str, Any] | None = None,
    body: str = "# Test title\nsome body",
) -> Claim | None:
    if frontmatter is None:
        frontmatter = _fm()
    return claim_from_note(
        frontmatter,
        body,
        path="notes/test.md",
        content_hash="abc123",
    )


# ---------------------------------------------------------------------------
# Eligibility gate
# ---------------------------------------------------------------------------


class TestEligibilityGate:
    def test_returns_none_for_plain_note(self) -> None:
        fm = {"id": "x", "type": "topic", "created": "2024-01-01T00:00:00+00:00"}
        result = claim_from_note(fm, "# Topic\nbody", path="x.md", content_hash="h")
        assert result is None

    def test_claim_type_makes_eligible(self) -> None:
        fm = _fm(type="claim")
        result = _make(frontmatter=fm)
        assert result is not None

    def test_valid_from_makes_eligible(self) -> None:
        fm = {
            "id": "x",
            "type": "topic",
            "created": "2024-01-01T00:00:00+00:00",
            "valid_from": "2024-06-01T00:00:00+00:00",
        }
        result = claim_from_note(fm, "body", path="x.md", content_hash="h")
        assert result is not None

    def test_valid_to_makes_eligible(self) -> None:
        fm = {
            "id": "x",
            "type": "topic",
            "created": "2024-01-01T00:00:00+00:00",
            "valid_to": "2024-12-31T00:00:00+00:00",
        }
        result = claim_from_note(fm, "body", path="x.md", content_hash="h")
        assert result is not None

    def test_observed_at_makes_eligible(self) -> None:
        fm = {
            "id": "x",
            "type": "topic",
            "created": "2024-01-01T00:00:00+00:00",
            "observed_at": "2024-06-15T00:00:00+00:00",
        }
        result = claim_from_note(fm, "body", path="x.md", content_hash="h")
        assert result is not None


# ---------------------------------------------------------------------------
# Malformed dates → None, never raise
# ---------------------------------------------------------------------------


class TestMalformedDates:
    def test_malformed_valid_from_rejects_claim(self) -> None:
        fm = _fm(valid_from="not-a-date")
        assert _make(frontmatter=fm) is None

    def test_malformed_valid_to_rejects_claim(self) -> None:
        fm = _fm(valid_to="9999-99-99")
        assert _make(frontmatter=fm) is None

    def test_malformed_observed_at_rejects_claim(self) -> None:
        fm = _fm(observed_at="bad-date", created="2024-03-01T00:00:00+00:00")
        assert _make(frontmatter=fm) is None

    def test_malformed_created_rejects_claim(self) -> None:
        fm = _fm(created="also-bad")
        assert _make(frontmatter=fm) is None

    def test_all_malformed_dates_reject_claim_without_raising(self) -> None:
        fm = _fm(valid_from="bad", valid_to="bad", observed_at="bad", created="bad")
        assert _make(frontmatter=fm) is None

    def test_missing_observation_time_rejects_claim(self) -> None:
        fm = {"id": "test-001", "type": "claim"}
        assert _make(frontmatter=fm) is None

    def test_valid_from_does_not_substitute_for_observation_time(self) -> None:
        fm = {
            "id": "test-001",
            "type": "claim",
            "valid_from": "2026-07-18T00:00:00+00:00",
        }

        assert _make(frontmatter=fm) is None

    @pytest.mark.parametrize(
        ("valid_from", "valid_to"),
        [
            ("2025-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
            ("2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        ],
    )
    def test_empty_or_reversed_window_rejects_claim(
        self, valid_from: str, valid_to: str
    ) -> None:
        assert _make(frontmatter=_fm(valid_from=valid_from, valid_to=valid_to)) is None


# ---------------------------------------------------------------------------
# Statement extraction and redaction
# ---------------------------------------------------------------------------


class TestStatementExtraction:
    def test_statement_field_used_when_present(self) -> None:
        fm = _fm(statement="User lives in Istanbul")
        result = _make(frontmatter=fm)
        assert result is not None
        assert "Istanbul" in result.statement

    def test_redaction_applied_to_statement_field(self) -> None:
        fm = _fm(statement="<private>secret</private> public")
        result = _make(frontmatter=fm)
        assert result is not None
        assert "secret" not in result.statement
        assert "[REDACTED]" in result.statement

    def test_title_heading_used_when_no_statement(self) -> None:
        body = "# My Claim Title\nsome content"
        result = _make(body=body)
        assert result is not None
        assert "My Claim Title" in result.statement

    def test_title_frontmatter_field_used_when_no_statement_or_heading(self) -> None:
        fm = _fm(title="Frontmatter Title")
        result = _make(frontmatter=fm, body="no heading here")
        assert result is not None
        assert "Frontmatter Title" in result.statement

    def test_path_used_as_final_fallback(self) -> None:
        result = _make(body="no heading here")
        assert result is not None
        # path is the fallback when no statement, title field, or heading
        assert result.statement  # non-empty


# ---------------------------------------------------------------------------
# observed_at defaults to created
# ---------------------------------------------------------------------------


class TestObservedAtDefault:
    def test_observed_at_defaults_to_created(self) -> None:
        fm = _fm(created="2024-05-01T12:00:00+00:00")
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.observed_at.year == 2024
        assert result.observed_at.month == 5

    def test_explicit_observed_at_overrides_created(self) -> None:
        fm = _fm(created="2024-01-01T00:00:00+00:00", observed_at="2024-06-01T00:00:00+00:00")
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.observed_at.month == 6


# ---------------------------------------------------------------------------
# Confidence label default
# ---------------------------------------------------------------------------


class TestConfidenceLabel:
    def test_default_label_is_extracted(self) -> None:
        result = _make()
        assert result is not None
        assert result.confidence_label == ConfidenceLabel.EXTRACTED

    def test_extracted_value(self) -> None:
        assert ConfidenceLabel.EXTRACTED.value == "EXTRACTED"

    def test_inferred_value_defined(self) -> None:
        assert ConfidenceLabel.INFERRED.value == "INFERRED"

    def test_ambiguous_value_defined(self) -> None:
        assert ConfidenceLabel.AMBIGUOUS.value == "AMBIGUOUS"


# ---------------------------------------------------------------------------
# Provenance fields
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_path_stored(self) -> None:
        result = _make()
        assert result is not None
        assert result.path == "notes/test.md"

    def test_content_hash_stored(self) -> None:
        result = _make()
        assert result is not None
        assert result.content_hash == "abc123"

    def test_trust_is_user(self) -> None:
        result = _make()
        assert result is not None
        assert result.trust == "user"

    def test_scope_defaults_to_default(self) -> None:
        result = _make()
        assert result is not None
        assert result.scope == "default"

    def test_conflicting_scope_and_legacy_project_are_rejected(self) -> None:
        result = _make(frontmatter=_fm(scope="clinical", project="legacy-project"))
        assert result is None

    def test_legacy_project_is_scope_fallback(self) -> None:
        result = _make(frontmatter=_fm(project="research"))
        assert result is not None
        assert result.scope == "research"

    def test_wildcard_scope_is_rejected_for_storage(self) -> None:
        assert _make(frontmatter=_fm(scope="*")) is None

    @pytest.mark.parametrize("scope", [" clinical ", "clinical*", "\u200bclinical"])
    def test_malformed_scope_is_rejected_for_storage(self, scope: str) -> None:
        assert _make(frontmatter=_fm(scope=scope)) is None

    def test_claim_id_stable(self) -> None:
        result1 = _make()
        result2 = _make()
        assert result1 is not None and result2 is not None
        assert result1.claim_id == result2.claim_id

    def test_claim_id_is_frontmatter_id_when_present(self) -> None:
        """With Fix B, claim_id == frontmatter id so supersedes references resolve."""
        r1 = claim_from_note(_fm(id="my-note-id"), "body", path="a.md", content_hash="h")
        assert r1 is not None
        assert r1.claim_id == "my-note-id"

    def test_claim_id_differs_for_different_frontmatter_ids(self) -> None:
        """Notes with different frontmatter ids produce different claim_ids."""
        r1 = claim_from_note(_fm(id="id-aaa"), "body", path="a.md", content_hash="h")
        r2 = claim_from_note(_fm(id="id-bbb"), "body", path="b.md", content_hash="h")
        assert r1 is not None and r2 is not None
        assert r1.claim_id != r2.claim_id

    def test_claim_id_fallback_uuid5_when_no_id(self) -> None:
        """When frontmatter has no id, claim_id is a deterministic uuid5 of path+statement."""
        fm_no_id = {k: v for k, v in _fm().items() if k != "id"}
        r1 = claim_from_note(fm_no_id, "body", path="a.md", content_hash="h")
        r2 = claim_from_note(fm_no_id, "body", path="b.md", content_hash="h")
        assert r1 is not None and r2 is not None
        # Different paths → different uuid5 → different claim_ids
        assert r1.claim_id != r2.claim_id
        # Stable: same path+statement → same id
        r1b = claim_from_note(fm_no_id, "body", path="a.md", content_hash="h")
        assert r1b is not None
        assert r1.claim_id == r1b.claim_id


# ---------------------------------------------------------------------------
# Optional fields
# ---------------------------------------------------------------------------


class TestOptionalFields:
    def test_supersedes_stored(self) -> None:
        fm = _fm(supersedes="some-other-claim-id")
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.supersedes == "some-other-claim-id"

    def test_claim_key_stored(self) -> None:
        fm = _fm(claim_key="user.location")
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.claim_key == "user.location"

    def test_valid_from_and_to_parsed(self) -> None:
        fm = _fm(
            valid_from="2024-01-01T00:00:00+00:00",
            valid_to="2025-01-01T00:00:00+00:00",
        )
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.valid_from is not None
        assert result.valid_to is not None
        assert result.valid_from.year == 2024
        assert result.valid_to.year == 2025


# ---------------------------------------------------------------------------
# Normalize callable
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_normalize_applied_to_statement_normalized(self) -> None:
        fm = _fm(statement="Istanbul")
        result = claim_from_note(
            fm,
            "body",
            path="x.md",
            content_hash="h",
            normalize=str.lower,
        )
        assert result is not None
        assert result.statement_normalized == "istanbul"

    def test_identity_normalize_default(self) -> None:
        fm = _fm(statement="Istanbul")
        result = _make(frontmatter=fm)
        assert result is not None
        assert result.statement_normalized == "Istanbul"
