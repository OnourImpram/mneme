"""Unit tests for YAML frontmatter parsing and serialization."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.vault.frontmatter import (
    KNOWN_TYPES,
    SCHEMA_VERSION,
    Frontmatter,
    _mtime_to_dt,
    _parse_dt,
    is_known_type,
    parse,
    serialize,
)


class TestKnownTypes:
    """Phase J Day 6: soft validation against the canonical type schema.

    Now 11 types: ``claim`` was added for the temporal claim lifecycle module
    (mneme_core.temporal) and ``failure`` for the code-failure memory module
    (mneme-code).
    """

    def test_known_types_size_locked(self) -> None:
        assert len(KNOWN_TYPES) == 11

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
            "claim",
            "failure",
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


class TestParseDt:
    """Unit tests for the _parse_dt sentinel-return behaviour."""

    def test_valid_iso_returns_datetime(self) -> None:
        result = _parse_dt("2026-05-19T10:00:00+00:00")
        assert isinstance(result, datetime)

    def test_z_suffix_returns_datetime(self) -> None:
        result = _parse_dt("2026-05-19T10:00:00Z")
        assert isinstance(result, datetime)

    def test_datetime_passthrough(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert _parse_dt(dt) is dt

    def test_not_a_date_returns_none(self) -> None:
        assert _parse_dt("not-a-date") is None

    def test_out_of_range_returns_none(self) -> None:
        # Use a value with time component so YAML does not try to construct
        # it as a bare date (bare "2026-13-99" triggers a YAML ValueError
        # before _parse_dt is ever reached).
        assert _parse_dt("2026-99-99T00:00:00") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_dt("") is None


class TestMtimeToDt:
    """Unit tests for the _mtime_to_dt fallback helper."""

    def test_none_returns_epoch(self) -> None:
        result = _mtime_to_dt(None)
        assert result == datetime(1970, 1, 1, tzinfo=UTC)

    def test_known_mtime_roundtrips(self) -> None:
        # Use a known epoch value (2026-01-01T00:00:00 UTC = 1767225600)
        mtime = 1767225600.0
        result = _mtime_to_dt(mtime)
        assert result.tzinfo is not None
        assert result == datetime.fromtimestamp(mtime, tz=UTC)


class TestMalformedDateDegradation:
    """Acceptance criteria (a) and (b) for F5 RESILIENCE.

    (a) A document with created="not-a-date" parses without raising and
        falls back to the file's mtime — the parsed created equals the
        explicitly-set mtime value.
    (b) A full index walk over a vault with one bad-date note completes
        and all other notes remain parseable (no whole-walk abort).
    """

    def test_bad_created_falls_back_to_mtime(self, tmp_path: Path) -> None:
        """Criterion (a): bad created → mtime fallback, no raise."""
        md = tmp_path / "bad.md"
        md.write_text(
            "---\nid: bad-note\ntype: session\ncreated: not-a-date\n---\nbody\n",
            encoding="utf-8",
        )
        # Set a known, distinct mtime so we can assert the exact value.
        known_mtime = 1_700_000_000.0
        os.utime(md, (known_mtime, known_mtime))

        stat = md.stat()
        fm, body = parse(md.read_text(encoding="utf-8"), file_path=md, mtime=stat.st_mtime)

        assert fm is not None, "parse() must not return None for a valid-structure note"
        expected_dt = datetime.fromtimestamp(known_mtime, tz=UTC)
        assert fm.created == expected_dt, (
            f"created should equal mtime fallback {expected_dt!r}, got {fm.created!r}"
        )
        assert body.strip() == "body"

    def test_bad_created_does_not_raise(self, tmp_path: Path) -> None:
        """parse() must not raise ValueError on a malformed created value."""
        text = "---\nid: x\ntype: session\ncreated: not-a-date\n---\nbody\n"
        # Should not raise
        fm, _ = parse(text, mtime=1_700_000_000.0)
        assert fm is not None

    def test_missing_structural_field_still_raises(self) -> None:
        """Missing 'id' must still raise — only timestamps degrade."""
        text = "---\ntype: session\ncreated: 2026-01-01T00:00:00Z\n---\nbody\n"
        with pytest.raises(ValueError, match="id"):
            parse(text)

    def test_vault_walk_completes_with_one_bad_note(self, tmp_path: Path) -> None:
        """Criterion (b): vault walk over bad+good notes completes; good notes parse."""
        # Write one bad-date note.
        bad = tmp_path / "bad.md"
        bad.write_text(
            "---\nid: bad\ntype: session\ncreated: not-a-date\n---\nbad body\n",
            encoding="utf-8",
        )
        # Write several good notes.
        good_ids = []
        for i in range(3):
            p = tmp_path / f"good_{i}.md"
            p.write_text(
                f"---\nid: good-{i}\ntype: session\n"
                f"created: 2026-01-0{i + 1}T00:00:00Z\n---\nbody {i}\n",
                encoding="utf-8",
            )
            good_ids.append(f"good-{i}")

        # Walk all markdown files, parsing each with its mtime.
        parsed_ids = []
        errors = []
        for md_path in sorted(tmp_path.glob("*.md")):
            stat = md_path.stat()
            try:
                fm, _ = parse(
                    md_path.read_text(encoding="utf-8"),
                    file_path=md_path,
                    mtime=stat.st_mtime,
                )
                if fm is not None:
                    parsed_ids.append(fm.id)
            except Exception as exc:  # noqa: BLE001
                errors.append((md_path.name, str(exc)))

        assert errors == [], f"Walk raised on: {errors}"
        # All four notes were indexed (bad note degraded, not skipped).
        assert "bad" in parsed_ids
        for gid in good_ids:
            assert gid in parsed_ids

    def test_warning_emitted_for_bad_created(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A structured warning naming the field must be emitted."""
        import logging

        text = "---\nid: w\ntype: session\ncreated: not-a-date\n---\nbody\n"
        with caplog.at_level(logging.WARNING, logger="mneme_core.vault.frontmatter"):
            parse(text, file_path=Path("some/note.md"), mtime=1_700_000_000.0)

        assert any("created" in r.message for r in caplog.records), (
            "Expected a warning mentioning field 'created'"
        )

    def test_yaml_level_malformed_dates_degrade(self) -> None:
        """The finding's literal examples degrade to the mtime fallback.

        Includes an UNQUOTED out-of-range date (``2026-13-99``) and an empty
        value. A default YAML loader resolves these as timestamps and raises
        inside the constructor before ``_parse_dt`` runs; the frontmatter
        loader drops the timestamp resolver so they arrive as plain strings
        and degrade uniformly rather than aborting the walk.
        """
        known_mtime = 1_700_000_000.0
        expected = datetime.fromtimestamp(known_mtime, tz=UTC)
        for raw in ("2026-13-99", '""', "2026-99-99T00:00:00"):
            text = f"---\nid: y\ntype: session\ncreated: {raw}\n---\nbody\n"
            fm, _ = parse(text, file_path=Path("note.md"), mtime=known_mtime)
            assert fm is not None, f"raw={raw!r} should not return None"
            assert fm.created == expected, (
                f"raw={raw!r} should degrade to mtime {expected!r}, got {fm.created!r}"
            )
