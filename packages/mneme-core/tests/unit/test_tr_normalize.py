"""Unit tests for the Turkish casefold normalizer.

These tests prove the zero-PyICU claim: a pure-Python implementation
correctly handles Turkish dotted-vs-dotless i pairs plus standard
Unicode lowercase for all other characters.

The KIYASLAMA edge case is the critical one. Standard ``str.lower()``
produces ``"kiyaslama"`` (with dotted i) because Python's default
locale rules are not Turkish-aware. Our normalizer produces
``"kıyaslama"`` (with dotless i), which is the correct CLDR result.
"""

from __future__ import annotations

import pytest

from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts


class TestNormalizeTr:
    """Behavior of the primary normalizer."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("Istanbul", "ıstanbul"),
            ("İstanbul", "istanbul"),
            ("istanbul", "istanbul"),
            ("İSTANBUL", "istanbul"),
            ("İLERİ", "ileri"),
            ("ileri", "ileri"),
            ("KIYASLAMA", "kıyaslama"),
            ("kıyaslama", "kıyaslama"),
            ("Kıyaslama", "kıyaslama"),
            ("ŞİMDİ", "şimdi"),
            ("Çağ", "çağ"),
            ("öğrenci", "öğrenci"),
            ("Üniversitesi", "üniversitesi"),
        ],
    )
    def test_turkish_casefold(self, input_str: str, expected: str) -> None:
        assert normalize_tr(input_str) == expected

    def test_kiyaslama_edge_case_naive_lower_diverges(self) -> None:
        """Standard ``.lower()`` produces a different (dotted i) result.

        This is the canonical reason we cannot use ``str.lower()`` alone
        for Turkish text destined for an FTS5 index. The dotted vs
        dotless distinction matters for search recall.
        """
        # Naive: dotted i.
        assert "KIYASLAMA".lower() == "kiyaslama"
        # Locale-aware: dotless i (the correct Turkish form).
        assert normalize_tr("KIYASLAMA") == "kıyaslama"
        assert normalize_tr("KIYASLAMA") != "KIYASLAMA".lower()

    def test_empty_string(self) -> None:
        assert normalize_tr("") == ""

    def test_idempotent(self) -> None:
        """Applying normalize_tr twice is the same as applying it once."""
        samples = ["İstanbul", "kıyaslama", "Hello World", "Öğrenci"]
        for s in samples:
            once = normalize_tr(s)
            twice = normalize_tr(once)
            assert once == twice, f"non-idempotent for {s!r}: {once!r} -> {twice!r}"

    def test_ascii_passthrough(self) -> None:
        assert normalize_tr("Hello World") == "hello world"
        assert normalize_tr("FOO-BAR-1") == "foo-bar-1"

    def test_capital_i_maps_to_dotless(self) -> None:
        """Critical mapping: U+0049 -> U+0131."""
        assert normalize_tr("I") == "ı"

    def test_capital_i_with_dot_maps_to_plain_i(self) -> None:
        """Critical mapping: U+0130 -> U+0069."""
        assert normalize_tr("İ") == "i"


class TestNormalizeTrForFts:
    """Behavior of the FTS-targeted whitespace-collapsing variant."""

    def test_collapses_internal_runs(self) -> None:
        assert (
            normalize_tr_for_fts("İstanbul   Üniversitesi")
            == "istanbul üniversitesi"
        )

    def test_strips_leading_and_trailing(self) -> None:
        assert normalize_tr_for_fts("  İstanbul  ") == "istanbul"

    def test_collapses_newlines(self) -> None:
        assert (
            normalize_tr_for_fts("İstanbul\n\nÜniversitesi")
            == "istanbul üniversitesi"
        )

    def test_collapses_tabs(self) -> None:
        assert normalize_tr_for_fts("foo\tbar") == "foo bar"

    def test_empty_string(self) -> None:
        assert normalize_tr_for_fts("") == ""

    def test_whitespace_only(self) -> None:
        assert normalize_tr_for_fts("   \n\t  ") == ""
