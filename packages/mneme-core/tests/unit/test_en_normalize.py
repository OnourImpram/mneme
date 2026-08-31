"""English normalizer contract, shared with the TypeScript retrieval path.

The vectors live in ``tests/fixtures/en_locale_vectors.json`` and are read by
BOTH this module and ``packages/mneme-mcp/tests/locale/en.test.ts``. That
shared file is the cross-language equivalence mechanism: the Python indexer
writes tokens and the TS client queries them, so a divergence between the two
implementations produces an index nothing can search. Asserting each side
against its own expectations would not catch that; asserting both against one
file does.

Mirrors the existing ``test_tr_normalize.py`` arrangement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mneme_core.fts5.locale.en import normalize_en, normalize_en_for_fts

_VECTORS_PATH = Path(__file__).parent.parent / "fixtures" / "en_locale_vectors.json"


def _load_vectors() -> list[dict[str, Any]]:
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))


VECTORS = _load_vectors()


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: str(v["id"]))
class TestSharedVectors:
    """Every vector in the shared fixture, both function variants."""

    def test_normalize_en(self, vector: dict[str, Any]) -> None:
        assert normalize_en(vector["input"]) == vector["normalize_en"], (
            vector["note"]
        )

    def test_normalize_en_for_fts(self, vector: dict[str, Any]) -> None:
        assert normalize_en_for_fts(vector["input"]) == vector[
            "normalize_en_for_fts"
        ], vector["note"]


class TestLengthInvariant:
    """normalize_en must map one character to exactly one.

    The TS snippet builder finds a match offset in the NORMALIZED body and
    slices the ORIGINAL body at that offset. A normalizer that changes length
    shifts every subsequent snippet, so this is a correctness constraint on
    the Python side too: both implementations must agree, and the TS one is
    bound by it.
    """

    @pytest.mark.parametrize("vector", VECTORS, ids=lambda v: str(v["id"]))
    def test_length_preserved(self, vector: dict[str, Any]) -> None:
        text = vector["input"]
        assert len(normalize_en(text)) == len(text), (
            f"length changed for {text!r}: {normalize_en(text)!r}"
        )

    def test_negative_control_bare_lower_violates_invariant(self) -> None:
        """Proof the guard is load-bearing.

        ``str.lower()`` is the obvious implementation and it fails: U+0130
        expands to i + U+0307. If this ever stops failing, the length
        assertions above have become vacuous.
        """
        assert len("İ".lower()) == 2
        assert "İ".lower() != "i"


class TestProfileSeparation:
    """The two profiles must disagree exactly where the languages disagree."""

    def test_english_keeps_ascii_i(self) -> None:
        from mneme_core.fts5.locale.tr import normalize_tr

        # The defect this profile exists to fix.
        assert normalize_en("API") == "api"
        assert normalize_tr("API") == "apı"
        assert normalize_en("Istanbul") == "istanbul"
        assert normalize_tr("Istanbul") == "ıstanbul"

    def test_english_does_not_introduce_dotless_i(self) -> None:
        for text in ("INDEX", "I/O", "CLI", "API"):
            assert "ı" not in normalize_en(text)

    def test_english_leaves_existing_dotless_i_alone(self) -> None:
        # A Turkish word inside English prose is lowercased, not re-folded.
        assert normalize_en("kıyaslama") == "kıyaslama"
