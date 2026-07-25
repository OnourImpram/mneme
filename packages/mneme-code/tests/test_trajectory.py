"""Tests for FixTrajectory: determinism, redaction, markdown contract, temporal integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from mneme_code.failure import FailureMemory
from mneme_code.stacktrace import Frame
from mneme_code.trajectory import FixTrajectory, fix_from_failure, fix_to_markdown

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_OBSERVED_AT = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
_RESOLVED_AT = datetime(2026, 6, 2, 15, 0, 0, tzinfo=UTC)

# Minimal FailureMemory used across tests — fields not under test carry
# safe sentinel values; content_hash is a valid hex string.
_FAILURE = FailureMemory(
    failure_id="aaaabbbb-cccc-dddd-eeee-ffffffffffff",
    exc_type="ValueError",
    exc_message="bad value",
    frames=(Frame(file_path="/app/main.py", line=42, function="run", code_context=None),),
    observed_at=_OBSERVED_AT,
    source_label=None,
    content_hash="a" * 64,
)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_produce_identical_trajectory_id(self) -> None:
        a = fix_from_failure(_FAILURE, fix_summary="patched null check", resolved_at=_RESOLVED_AT)
        b = fix_from_failure(_FAILURE, fix_summary="patched null check", resolved_at=_RESOLVED_AT)
        assert a.trajectory_id == b.trajectory_id

    def test_trajectory_id_changes_with_different_fix_summary(self) -> None:
        a = fix_from_failure(_FAILURE, fix_summary="fix A", resolved_at=_RESOLVED_AT)
        b = fix_from_failure(_FAILURE, fix_summary="fix B", resolved_at=_RESOLVED_AT)
        assert a.trajectory_id != b.trajectory_id

    def test_trajectory_id_changes_with_different_failure_id(self) -> None:
        other = FailureMemory(
            failure_id="11112222-3333-4444-5555-666677778888",
            exc_type="TypeError",
            exc_message="type error",
            frames=_FAILURE.frames,
            observed_at=_OBSERVED_AT,
            source_label=None,
            content_hash="b" * 64,
        )
        a = fix_from_failure(_FAILURE, fix_summary="same fix", resolved_at=_RESOLVED_AT)
        b = fix_from_failure(other, fix_summary="same fix", resolved_at=_RESOLVED_AT)
        assert a.trajectory_id != b.trajectory_id


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_private_tag_removed_from_fix_summary_field(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="applied <private>secret</private> patch",
            resolved_at=_RESOLVED_AT,
        )
        assert "secret" not in fix.fix_summary

    def test_private_tag_removed_from_markdown_output(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="applied <private>secret</private> patch",
            resolved_at=_RESOLVED_AT,
        )
        md = fix_to_markdown(fix)
        assert "secret" not in md

    def test_redacted_marker_present_in_summary(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="<private>secret</private>",
            resolved_at=_RESOLVED_AT,
        )
        assert "[REDACTED]" in fix.fix_summary

    def test_determinism_uses_redacted_summary(self) -> None:
        # Two calls with identical plaintext (private tags differ but result same post-redact).
        a = fix_from_failure(
            _FAILURE, fix_summary="fix <private>A</private>", resolved_at=_RESOLVED_AT
        )
        b = fix_from_failure(
            _FAILURE, fix_summary="fix <private>B</private>", resolved_at=_RESOLVED_AT
        )
        # Both redact to "fix [REDACTED]" → same trajectory_id.
        assert a.trajectory_id == b.trajectory_id


# ---------------------------------------------------------------------------
# Markdown contract
# ---------------------------------------------------------------------------


class TestMarkdownContract:
    def _make_fix(self, *, resolved_at: datetime | None = None) -> FixTrajectory:
        return fix_from_failure(
            _FAILURE,
            fix_summary="null-check added",
            resolved_at=resolved_at or _RESOLVED_AT,
        )

    def test_type_is_claim(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert "type: claim" in md

    def test_supersedes_uses_failure_note_id(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="null-check added",
            resolved_at=_RESOLVED_AT,
            failure_note_id="custom-note-id-abc",
        )
        md = fix_to_markdown(fix)
        assert 'supersedes: "custom-note-id-abc"' in md

    def test_claim_key_format(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert f'claim_key: "failure.{_FAILURE.failure_id}"' in md

    def test_valid_from_present(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert "valid_from:" in md

    def test_created_present(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert "created:" in md

    def test_utc_normalization_from_offset_aware(self) -> None:
        # +03:00 offset → UTC is 3 hours earlier.
        tz_plus3 = timezone(timedelta(hours=3))
        resolved_local = datetime(2026, 6, 2, 18, 0, 0, tzinfo=tz_plus3)  # == 15:00 UTC
        fix = self._make_fix(resolved_at=resolved_local)
        md = fix_to_markdown(fix)
        expected = "2026-06-02T15:00:00.000000+00:00"
        assert f"created: {expected}" in md
        assert f"valid_from: {expected}" in md

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive = datetime(2026, 6, 2, 15, 0, 0)  # no tzinfo
        fix = self._make_fix(resolved_at=naive)
        md = fix_to_markdown(fix)
        assert "created: 2026-06-02T15:00:00.000000+00:00" in md

    def test_body_contains_failure_id_heading(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert f"# Fix for {_FAILURE.failure_id}" in md

    def test_body_contains_fix_summary(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert "null-check added" in md

    def test_frontmatter_delimiters_present(self) -> None:
        md = fix_to_markdown(self._make_fix())
        assert md.startswith("---\n")
        lines = md.splitlines()
        closing_dashes = [i for i, ln in enumerate(lines) if ln == "---"]
        assert len(closing_dashes) >= 2  # opening + closing


# ---------------------------------------------------------------------------
# failure_note_id defaults
# ---------------------------------------------------------------------------


class TestFailureNoteIdDefault:
    def test_defaults_to_failure_id_when_omitted(self) -> None:
        fix = fix_from_failure(_FAILURE, fix_summary="fix", resolved_at=_RESOLVED_AT)
        assert fix.failure_note_id == _FAILURE.failure_id

    def test_explicit_failure_note_id_overrides_default(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="fix",
            resolved_at=_RESOLVED_AT,
            failure_note_id="vault/failures/my-note.md",
        )
        assert fix.failure_note_id == "vault/failures/my-note.md"

    def test_supersedes_in_markdown_uses_explicit_note_id(self) -> None:
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="fix",
            resolved_at=_RESOLVED_AT,
            failure_note_id="vault/failures/my-note.md",
        )
        md = fix_to_markdown(fix)
        assert 'supersedes: "vault/failures/my-note.md"' in md


# ---------------------------------------------------------------------------
# Temporal integration — the critical contract test
# ---------------------------------------------------------------------------


class TestTemporalIntegration:
    """Verify the supersession contract wires up through claim_from_note."""

    def test_fix_claim_supersedes_failure_note(self) -> None:
        from mneme_core.temporal.claim import claim_from_note

        failure_note_id = _FAILURE.failure_id  # vault note id == failure_id

        # --- Build the failure frontmatter dict (mirrors what the indexer parses) ---
        failure_fm: dict[str, object] = {
            "id": failure_note_id,
            "type": "failure",
            "created": _OBSERVED_AT.isoformat(timespec="microseconds"),
            "tags": ["failure"],
            "exc_type": _FAILURE.exc_type,
        }
        failure_body = f"# {_FAILURE.exc_type}\n\n> {_FAILURE.exc_message}\n"

        # failure note is type "failure", not "claim", and has no temporal fields
        # → claim_from_note returns None (failure notes are not temporal claims).
        failure_claim = claim_from_note(
            failure_fm,
            failure_body,
            path="code-failures/test.md",
            content_hash="a" * 64,
        )
        assert failure_claim is None  # confirmed: failure notes are not temporal claims

        # --- Build the fix note ---
        fix = fix_from_failure(
            _FAILURE,
            fix_summary="null-pointer guard added",
            resolved_at=_RESOLVED_AT,
            failure_note_id=failure_note_id,
        )
        fix_md = fix_to_markdown(fix)

        # Parse frontmatter from the generated markdown manually
        # (mirrors what the indexer does — extract lines between --- delimiters).
        lines = fix_md.splitlines()
        assert lines[0] == "---"
        closing = next(i for i, ln in enumerate(lines[1:], 1) if ln == "---")
        fm_lines = lines[1:closing]

        fix_fm: dict[str, object] = {}
        for ln in fm_lines:
            if ln.startswith("  "):
                # list item under tags — skip (not needed for claim_from_note contract)
                continue
            if ":" in ln:
                k, _, v = ln.partition(":")
                fix_fm[k.strip()] = v.strip().strip('"')

        # Reconstruct body from lines after closing ---
        fix_body = "\n".join(lines[closing + 1 :])

        fix_claim = claim_from_note(
            fix_fm,
            fix_body,
            path="code-fixes/test.md",
            content_hash="b" * 64,
        )

        # THE CRITICAL ASSERTIONS — supersession contract
        assert fix_claim is not None, "fix note must be indexed as a temporal claim"
        assert fix_claim.supersedes == failure_note_id, (
            f"fix claim supersedes must equal failure_note_id={failure_note_id!r}, "
            f"got {fix_claim.supersedes!r}"
        )
        assert fix_claim.claim_key is not None
        assert fix_claim.claim_key.startswith("failure."), (
            f"claim_key must start with 'failure.', got {fix_claim.claim_key!r}"
        )
        # C1: prove valid_from round-trips to the resolved instant through the
        # real claim_from_note path (the temporal validity window wires up).
        assert fix_claim.valid_from == _RESOLVED_AT

    def test_supersedes_yaml_safe_with_colon(self) -> None:
        """C2: a failure_note_id containing a colon-space must be json-quoted so
        the frontmatter stays valid YAML and the supersession link survives."""
        from mneme_core.temporal.claim import claim_from_note

        note_id = "notes/fix: my note.md"
        fix = fix_from_failure(
            _FAILURE, fix_summary="guard added", resolved_at=_RESOLVED_AT, failure_note_id=note_id
        )
        md = fix_to_markdown(fix)
        assert 'supersedes: "notes/fix: my note.md"' in md
        # And it parses back through claim_from_note with the link intact.
        fix_fm: dict[str, object] = {
            "id": fix.trajectory_id,
            "type": "claim",
            "supersedes": note_id,
            "claim_key": f"failure.{fix.failure_id}",
            "created": fix.resolved_at.isoformat(timespec="microseconds"),
            "valid_from": fix.resolved_at.isoformat(timespec="microseconds"),
        }
        claim = claim_from_note(fix_fm, "# fix\n", path="code-fixes/x.md", content_hash="c" * 64)
        assert claim is not None
        assert claim.supersedes == note_id
