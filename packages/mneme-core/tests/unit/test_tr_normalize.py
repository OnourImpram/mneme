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

import json
from pathlib import Path

import pytest

from mneme_core.fts5.locale.tr import (
    normalize_tr,
    normalize_tr_ascii_fold,
    normalize_tr_ascii_fold_for_fts,
    normalize_tr_for_fts,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_VECTORS_PATH = _FIXTURES_DIR / "tr_locale_vectors.json"


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


class TestNormalizeTrAsciiFold:
    """Behavior of the dual-key ASCII-fold recall normalizer."""

    @pytest.mark.parametrize(
        "input_str",
        ["İ", "I", "ı", "i"],
    )
    def test_all_four_i_forms_collapse_to_ascii_i(self, input_str: str) -> None:
        """Every dotted/dotless i-form folds to plain ASCII i (U+0069)."""
        assert normalize_tr_ascii_fold(input_str) == "i"

    @pytest.mark.parametrize(
        "input_str",
        ["İzmir", "izmir", "Izmir", "IZMIR"],
    )
    def test_all_izmir_spellings_share_one_key(self, input_str: str) -> None:
        """The whole point: every casing of Izmir folds to one key."""
        assert normalize_tr_ascii_fold(input_str) == "izmir"

    def test_does_not_disturb_other_diacritics(self) -> None:
        # ş/ç/ğ/ö/ü are left to the unicode61 tokenizer; only i-forms fold.
        assert normalize_tr_ascii_fold("İŞIK") == "işik"
        assert normalize_tr_ascii_fold("Çağ") == "çağ"

    def test_diverges_from_cldr_only_on_dotless(self) -> None:
        # Where CLDR keeps the dotless distinction, the ASCII fold collapses it.
        assert normalize_tr("Istanbul") == "ıstanbul"
        assert normalize_tr_ascii_fold("Istanbul") == "istanbul"

    def test_empty_string(self) -> None:
        assert normalize_tr_ascii_fold("") == ""

    def test_idempotent(self) -> None:
        for s in ["İzmir", "Istanbul", "KIYASLAMA", "İŞIK"]:
            once = normalize_tr_ascii_fold(s)
            assert normalize_tr_ascii_fold(once) == once

    def test_for_fts_collapses_whitespace(self) -> None:
        assert (
            normalize_tr_ascii_fold_for_fts("Izmir   Istanbul")
            == "izmir istanbul"
        )
        assert normalize_tr_ascii_fold_for_fts("  IZMIR  ") == "izmir"


def _load_vectors() -> list[dict[str, str]]:
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))  # type: ignore[return-value]


def _params() -> list[pytest.param]:  # type: ignore[type-arg]
    return [pytest.param(v, id=v["id"]) for v in _load_vectors()]


class TestGoldenVectors:
    """Drive both normalizers from the shared cross-language fixture.

    The same JSON file is consumed by the TS parity test in
    ``packages/mneme-mcp/tests/locale/tr.test.ts``.  Any divergence
    between the two implementations will surface here first (Python)
    and again there (TypeScript).
    """

    def test_fixture_file_exists(self) -> None:
        assert _VECTORS_PATH.exists(), f"fixture missing: {_VECTORS_PATH}"

    @pytest.mark.parametrize("vec", _params())
    def test_normalize_tr(self, vec: dict[str, str]) -> None:
        assert normalize_tr(vec["input"]) == vec["normalize_tr"]

    @pytest.mark.parametrize("vec", _params())
    def test_normalize_tr_for_fts(self, vec: dict[str, str]) -> None:
        assert normalize_tr_for_fts(vec["input"]) == vec["normalize_tr_for_fts"]

    @pytest.mark.parametrize("vec", _params())
    def test_normalize_tr_ascii_fold(self, vec: dict[str, str]) -> None:
        assert (
            normalize_tr_ascii_fold(vec["input"]) == vec["normalize_tr_ascii_fold"]
        )

    @pytest.mark.parametrize("vec", _params())
    def test_normalize_tr_ascii_fold_for_fts(self, vec: dict[str, str]) -> None:
        assert (
            normalize_tr_ascii_fold_for_fts(vec["input"])
            == vec["normalize_tr_ascii_fold_for_fts"]
        )
