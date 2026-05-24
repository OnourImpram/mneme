"""Unit tests for YAML frontmatter parsing and serialization."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mneme_core.vault.frontmatter import (
    KNOWN_TYPES,
    SCHEMA_VERSION,
    Frontmatter,
    is_known_type,
    parse,
    serialize,
)


class TestKnownTypes:
    """Phase J Day 6: soft validation against the canonical 9-type schema."""

    def test_known_types_size_locked(self) -> None:
        assert len(KNOWN_TYPES) == 9

    def test_canonical_literals_present(self) -> None:
        canonical = {
            "session",
            "topic",
            "reference",
            "pattern",
            "trajectory",
            "compressed",
            "observation",
            "session_summary",
            "user_prompt",
        }
        assert canonical == KNOWN_TYPES

    def test_is_known_type_true_for_canonical(self) -> None:
        for canonical in KNOWN_TYPES:
            assert is_known_type(canonical) is True

    def test_is_known_type_false_for_unknown(self) -> None:
        # Day 1 regression class: migration tool was emitting these.
        assert is_known_type("observation-auto") is False
        assert is_known_type("session_summary_v2") is False
        assert is_known_type("") is False

    def test_parser_accepts_unknown_type_without_raising(self) -> None:
        """Parser stays permissive so vendored content keeps working."""
        text = (
            "---\n"
            "id: t\n"
            "type: vendored-custom\n"
            "created: 2026-01-01T00:00:00Z\n"
            "---\n"
            "body"
        )
        fm, _ = parse(text)
        assert fm is not None
        assert fm.type == "vendored-custom"
        assert is_known_type(fm.type) is False


class TestParse:
    def test_no_frontmatter(self) -> None:
        text = "just a body without frontmatter"
        fm, body = parse(text)
        assert fm is None
        assert body == text

    def test_basic_parse(self) -> None:
        text = (
            "---\n"
            "id: test-id\n"
            "type: session\n"
            "created: 2026-05-19T10:00:00Z\n"
            "schema_version: 1\n"
            "---\n"
            "body content"
        )
        fm, body = parse(text)
        assert fm is not None
        assert fm.id == "test-id"
        assert fm.type == "session"
        assert fm.created == datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
        assert fm.schema_version == 1
        assert body == "body content"

    def test_missing_required_raises(self) -> None:
        text = (
            "---\n"
            "id: x\n"
            "type: y\n"
            "---\n"
        )
        with pytest.raises(ValueError, match="created"):
            parse(text)

    def test_tags_optional(self) -> None:
        text = (
            "---\n"
            "id: t\n"
            "type: session\n"
            "created: 2026-01-01T00:00:00Z\n"
            "tags: [foo, bar]\n"
            "---\n"
            "body"
        )
        fm, _ = parse(text)
        assert fm is not None
        assert fm.tags == ["foo", "bar"]

    def test_extra_fields_preserved(self) -> None:
        text = (
            "---\n"
            "id: t\n"
            "type: session\n"
            "created: 2026-01-01T00:00:00Z\n"
            "custom_field: value\n"
            "another: 42\n"
            "---\n"
            "body"
        )
        fm, _ = parse(text)
        assert fm is not None
        assert fm.extra == {"custom_field": "value", "another": 42}

    def test_default_schema_version(self) -> None:
        text = (
            "---\n"
            "id: t\n"
            "type: session\n"
            "created: 2026-01-01T00:00:00Z\n"
            "---\n"
            "body"
        )
        fm, _ = parse(text)
        assert fm is not None
        assert fm.schema_version == SCHEMA_VERSION


class TestSerialize:
    def test_round_trip(self) -> None:
        fm = Frontmatter(
            id="t",
            type="session",
            created=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            tags=["a", "b"],
            session_id="s1",
        )
        text = serialize(fm, "body")
        parsed_fm, parsed_body = parse(text)
        assert parsed_fm is not None
        assert parsed_fm.id == fm.id
        assert parsed_fm.type == fm.type
        assert parsed_fm.created == fm.created
        assert parsed_fm.tags == fm.tags
        assert parsed_fm.session_id == fm.session_id
        assert parsed_body == "body"

    def test_unicode_preserved(self) -> None:
        fm = Frontmatter(
            id="t",
            type="session",
            created=datetime(2026, 1, 1, tzinfo=UTC),
            extra={"title": "Foo Bar"},
        )
        text = serialize(fm, "kıyaslama body içerik")
        assert "kıyaslama" in text
        assert "içerik" in text

    def test_extra_round_trip(self) -> None:
        fm = Frontmatter(
            id="t",
            type="session",
            created=datetime(2026, 1, 1, tzinfo=UTC),
            extra={"custom": "value", "n": 7},
        )
        text = serialize(fm, "body")
        parsed_fm, _ = parse(text)
        assert parsed_fm is not None
        assert parsed_fm.extra == {"custom": "value", "n": 7}
