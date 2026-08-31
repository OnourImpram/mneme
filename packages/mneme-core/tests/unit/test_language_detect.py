"""Per-document language resolution.

Context for why this exists: ``documents.language`` shipped in schema 3 with
a ``DEFAULT 'en'`` and nothing ever wrote to it. Measured on a real
Turkish-majority vault before 4.0, 11,910 of 11,910 rows carried the default.
The column was configured but never populated, and no query read it, so
nothing could surface the gap.

The detector is deliberately conservative. A wrong label is worse than no
label: it would route a document through the wrong normalizer at query time.
So the contract is "decide, or abstain" — never "guess".
"""

from __future__ import annotations

import pytest

from mneme_core.fts5.language import (
    UNDETERMINED,
    detect_language,
    language_from_frontmatter,
    resolve_language,
)

TURKISH_PROSE = (
    "Bu dosya operatörün verdiği ve kapattığı kararların tek kanonik "
    "listesidir. Her madde için bir gerekçe ve tarih vardır, bu liste "
    "değişmez ve yeni kanıt olmadan yeniden açılmaz."
)

ENGLISH_PROSE = (
    "This document describes the retrieval path and the way that queries "
    "are normalized before they reach the index. It also explains which "
    "backends contributed to a result and what that means for the caller."
)


class TestFrontmatterDeclaration:
    """The author's declaration outranks any heuristic."""

    def test_lang_key(self) -> None:
        assert language_from_frontmatter({"lang": "tr"}) == "tr"

    def test_language_key(self) -> None:
        assert language_from_frontmatter({"language": "en"}) == "en"

    def test_primary_subtag_only(self) -> None:
        assert language_from_frontmatter({"lang": "en-GB"}) == "en"
        assert language_from_frontmatter({"language": "pt-BR"}) == "pt"

    def test_case_insensitive(self) -> None:
        assert language_from_frontmatter({"lang": "TR"}) == "tr"

    def test_absent_or_blank(self) -> None:
        assert language_from_frontmatter(None) == UNDETERMINED
        assert language_from_frontmatter({}) == UNDETERMINED
        assert language_from_frontmatter({"lang": "   "}) == UNDETERMINED
        assert language_from_frontmatter({"title": "x"}) == UNDETERMINED

    def test_non_string_value_ignored(self) -> None:
        assert language_from_frontmatter({"lang": 42}) == UNDETERMINED

    def test_declaration_wins_over_contradicting_body(self) -> None:
        # An author labelling a Turkish-looking note as English is respected.
        assert resolve_language({"lang": "en"}, TURKISH_PROSE, "tr") == "en"


class TestDetection:
    def test_turkish_prose(self) -> None:
        assert detect_language(TURKISH_PROSE) == "tr"

    def test_english_prose(self) -> None:
        assert detect_language(ENGLISH_PROSE) == "en"

    @pytest.mark.parametrize("text", ["", "x", "short text here", "a b c"])
    def test_too_short_abstains(self, text: str) -> None:
        assert detect_language(text) == UNDETERMINED

    def test_abstains_rather_than_guessing(self) -> None:
        """Content with no function words in either language must abstain."""
        code_like = " ".join(["foo", "bar", "baz", "qux"] * 6)
        assert detect_language(code_like) == UNDETERMINED

    def test_turkish_orthography_alone_is_enough(self) -> None:
        # Turkish-only characters carry signal even without function words.
        assert detect_language("çğışöü " * 20) == "tr"


class TestResolution:
    def test_falls_back_to_index_language(self) -> None:
        # Undetectable content lands where its index already is, rather than
        # on a hard-coded 'en' — that default was the pre-4.0 defect.
        assert resolve_language(None, "x", "tr") == "tr"
        assert resolve_language(None, "x", "en") == "en"

    def test_detection_beats_fallback(self) -> None:
        assert resolve_language(None, TURKISH_PROSE, "en") == "tr"
        assert resolve_language(None, ENGLISH_PROSE, "tr") == "en"

    def test_never_returns_undetermined(self) -> None:
        for body in ("", "x", TURKISH_PROSE, ENGLISH_PROSE):
            assert resolve_language(None, body, "en") != UNDETERMINED

    def test_greek_is_out_of_scope_and_falls_back(self) -> None:
        """Greek has no normalization profile in 4.0.

        The vault holds Greek material, so this records the boundary rather
        than pretending it is handled: undeclared Greek resolves to the index
        language. A declared ``lang: el`` is still honoured verbatim.
        """
        greek = "Το κείμενο αυτό περιγράφει τη διαδρομή ανάκτησης και τον τρόπο."
        assert resolve_language(None, greek, "tr") == "tr"
        assert resolve_language({"lang": "el"}, greek, "tr") == "el"
