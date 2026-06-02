"""T5 parity: Python KNOWN_TYPES frozenset must match canonical fixture.

The canonical list lives in tests/fixtures/canonical_memory_types.json and
is the shared source of truth consumed by both Python and TypeScript parity
tests (mirroring the tr_locale_vectors.json pattern). This test asserts that
the nine types declared in vault.frontmatter.KNOWN_TYPES exactly equal the
nine entries in the fixture file, so a change to either side surfaces
immediately in CI rather than silently diverging.
"""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.vault.frontmatter import KNOWN_TYPES

# Locate the fixture relative to this file: tests/unit/ -> tests/fixtures/
_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "canonical_memory_types.json"


class TestCanonicalMemoryTypesParity:
    """T5: Python KNOWN_TYPES == canonical_memory_types.json."""

    def test_fixture_file_exists(self) -> None:
        assert _FIXTURE_PATH.exists(), (
            f"Shared fixture not found: {_FIXTURE_PATH}. "
            "Run the fixture generator or restore the file."
        )

    def test_known_types_match_fixture(self) -> None:
        """KNOWN_TYPES frozenset must equal the type set from the fixture."""
        raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture_types: frozenset[str] = frozenset(entry["type"] for entry in raw)
        assert fixture_types == KNOWN_TYPES, (
            f"KNOWN_TYPES diverges from fixture.\n"
            f"  In fixture only: {sorted(fixture_types - KNOWN_TYPES)}\n"
            f"  In KNOWN_TYPES only: {sorted(KNOWN_TYPES - fixture_types)}"
        )

    def test_fixture_has_nine_entries(self) -> None:
        """The fixture must declare exactly ten canonical types (locked count).

        Now 11 entries: ``claim`` (temporal) and ``failure`` (code) were added.
        """
        raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        assert len(raw) == 11, (
            f"Expected 11 fixture entries, got {len(raw)}. "
            "Update the fixture and KNOWN_TYPES together."
        )

    def test_each_fixture_entry_has_type_and_description(self) -> None:
        """Every fixture entry must have both 'type' and 'description' keys."""
        raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        for entry in raw:
            assert "type" in entry, f"Missing 'type' key in entry: {entry!r}"
            assert "description" in entry, (
                f"Missing 'description' key in entry: {entry!r}"
            )
            assert isinstance(entry["type"], str) and entry["type"], (
                f"'type' must be a non-empty string, got: {entry['type']!r}"
            )
