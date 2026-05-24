"""Turkish locale-aware normalization for FTS5.

Pure-Python implementation of Turkish casefold mapping. Avoids PyICU
because (a) PyICU has no Windows wheel for any Python version, (b) the
Turkish casefold rules can be expressed exactly as two character
substitutions applied before standard Unicode lowercase.

Rules (per the CLDR Unicode Turkish Casing Rules):

- Capital LATIN LETTER I WITH DOT ABOVE (U+0130) maps to LATIN SMALL
  LETTER I (U+0069).
- Capital LATIN LETTER I (U+0049) maps to LATIN SMALL LETTER DOTLESS I
  (U+0131).
- All other characters: standard Unicode lowercase.

Applied identically at ingest and query, this guarantees that any one
canonical spelling normalizes to a stable key. Note that dotted ``İ``
and dotless ``I`` are distinct letters in Turkish, so ``İstanbul`` (the
proper city spelling) folds to ``istanbul`` while ASCII ``ISTANBUL``
(dotless) folds to ``ıstanbul``. This is the correct CLDR behavior, not
a bug: the two i-forms are not interchangeable. The FTS5 layer depends
on the same normalizer running at ingest and query so a given form
always matches itself regardless of input case.

Diacritics on other Turkish letters (c-cedilla, s-cedilla, g-breve,
o-umlaut, u-umlaut) are not stripped here. The SQLite ``unicode61``
tokenizer with ``remove_diacritics 2`` handles diacritics at the FTS5
layer.
"""

from __future__ import annotations


def normalize_tr(s: str) -> str:
    """Apply Turkish locale casefold then standard lowercase.

    Args:
        s: Input string in any text.

    Returns:
        Casefolded string suitable for FTS5 ingest or query.

    Examples:
        >>> normalize_tr("İstanbul")
        'istanbul'
        >>> normalize_tr("Istanbul")
        'ıstanbul'
        >>> normalize_tr("KIYASLAMA")
        'kıyaslama'
    """
    return s.replace("İ", "i").replace("I", "ı").lower()


def normalize_tr_for_fts(s: str) -> str:
    """Casefold + collapse internal whitespace for FTS5 ingest.

    Removes extra whitespace so multi-line content normalizes
    consistently. Diacritics are not stripped here; the SQLite
    ``unicode61`` tokenizer handles diacritic folding with
    ``remove_diacritics 2``.

    Args:
        s: Input string, possibly multi-line.

    Returns:
        Normalized single-line string with single-space separation.
    """
    return " ".join(normalize_tr(s).split())
