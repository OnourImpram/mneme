"""Per-document language detection for the FTS5 index.

The ``documents.language`` column has existed since schema 3 with a
``DEFAULT 'en'``, but nothing ever wrote to it. Measured on a real
Turkish-majority vault before 4.0: 11,910 of 11,910 documents carried the
default ``'en'``. The column was configured, never populated — a fact no
query could have revealed, because no query read it either.

This module fills it. Detection is deliberately a small, dependency-free
heuristic rather than a statistical language model:

* The vault owner's declaration wins. A ``lang:`` or ``language:``
  frontmatter key is taken verbatim; nothing here second-guesses it.
* Otherwise two cheap signals are combined over a bounded prefix:
  characters that only occur in Turkish orthography, and function-word
  frequency for each candidate language.
* When the signals do not separate, the answer is ``UNDETERMINED`` rather
  than a coin flip. Callers fall back to the index-wide profile language,
  mirroring the scope rule that an ambiguous filter must never silently
  widen.

A wrong label is worse than no label, so the thresholds below are tuned to
abstain rather than guess. Greek is out of scope for 4.0: the vault holds
Greek material, but no ``el`` normalization profile exists yet, so Greek
documents resolve to ``UNDETERMINED`` and fall back like any other
unrecognised text.
"""

from __future__ import annotations

import re
from typing import Final

#: Returned when the signals do not separate. Never persisted as a language.
UNDETERMINED: Final = ""

#: Characters that appear in Turkish orthography but not in English.
#: Shared Latin letters carry no signal and are excluded on purpose.
_TR_ONLY_CHARS: Final[frozenset[str]] = frozenset("ğşıİĞŞÇÖÜçöü")

#: High-frequency Turkish function words. Chosen because they are common in
#: running prose yet rare as English tokens, so a mixed technical document
#: does not drift Turkish on shared words alone.
_TR_FUNCTION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "ve", "bir", "bu", "için", "ile", "olarak", "daha", "gibi", "olan",
        "var", "yok", "ama", "çok", "sonra", "kadar", "ancak", "ise", "de",
        "da", "her", "şey", "değil", "göre", "üzere", "veya", "hem", "ya",
    }
)

#: High-frequency English function words, same selection criteria.
_EN_FUNCTION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "which", "are",
        "was", "were", "have", "has", "been", "not", "but", "can", "will",
        "would", "should", "when", "what", "there", "their", "its",
    }
)

#: Only this many characters are inspected. Language is a whole-document
#: property; reading a 200 KB note to decide it wastes the indexer's budget.
_SAMPLE_CHARS: Final = 4000

#: Minimum word count before the function-word ratio means anything.
#: Below this a single stray "the" would dominate.
_MIN_WORDS: Final = 12

#: How much one signal must exceed the other to win. At 1.25 a document
#: needs a clear margin; inside that band the answer is UNDETERMINED.
_DECISION_MARGIN: Final = 1.25

#: Weight applied to Turkish-only character density. Scaled so that a page
#: of Turkish prose (roughly 2-4% such characters) produces a signal
#: comparable to its function-word ratio.
_TR_CHAR_WEIGHT: Final = 200.0

_WORD_RE: Final = re.compile(r"[a-zçğıöşüA-ZÇĞİÖŞÜ]+")

_FRONTMATTER_KEYS: Final = ("lang", "language")


def language_from_frontmatter(frontmatter: dict[str, object] | None) -> str:
    """Return the language the document declares, or ``UNDETERMINED``.

    The owner's declaration outranks any heuristic. Only the primary subtag
    is kept and it is lowercased, so ``en-GB`` and ``EN`` both yield ``en``.

    Args:
        frontmatter: parsed YAML frontmatter mapping, or ``None``.

    Returns:
        A lowercase language subtag, or :data:`UNDETERMINED`.

    Examples:
        >>> language_from_frontmatter({"lang": "TR"})
        'tr'
        >>> language_from_frontmatter({"language": "en-GB"})
        'en'
        >>> language_from_frontmatter({"title": "x"})
        ''
    """
    if not frontmatter:
        return UNDETERMINED
    for key in _FRONTMATTER_KEYS:
        raw = frontmatter.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().split("-")[0].lower()
    return UNDETERMINED


def detect_language(text: str) -> str:
    """Guess ``tr`` or ``en`` from body text, abstaining when unsure.

    Args:
        text: document body. Only the first :data:`_SAMPLE_CHARS` are read.

    Returns:
        ``"tr"``, ``"en"``, or :data:`UNDETERMINED` when the signals do not
        separate by :data:`_DECISION_MARGIN`.

    Examples:
        >>> detect_language("Bu dosya kararların tek kanonik listesidir ve "
        ...                 "her madde için bir gerekçe içerir.")
        'tr'
        >>> detect_language("This document describes the retrieval path and "
        ...                 "the way that queries are normalized for search.")
        'en'
        >>> detect_language("x")
        ''
    """
    if not text:
        return UNDETERMINED
    sample = text[:_SAMPLE_CHARS]
    words = _WORD_RE.findall(sample.lower())
    if len(words) < _MIN_WORDS:
        return UNDETERMINED

    tr_chars = sum(1 for char in sample if char in _TR_ONLY_CHARS)
    tr_ratio = sum(1 for word in words if word in _TR_FUNCTION_WORDS) / len(words)
    en_ratio = sum(1 for word in words if word in _EN_FUNCTION_WORDS) / len(words)

    tr_score = tr_ratio * 100.0 + (tr_chars / len(sample)) * _TR_CHAR_WEIGHT
    en_score = en_ratio * 100.0

    if tr_score > en_score * _DECISION_MARGIN:
        return "tr"
    if en_score > tr_score * _DECISION_MARGIN:
        return "en"
    return UNDETERMINED


def resolve_language(
    frontmatter: dict[str, object] | None,
    body: str,
    fallback: str,
) -> str:
    """Resolve a document's language: declaration, then heuristic, then fallback.

    Args:
        frontmatter: parsed frontmatter mapping, or ``None``.
        body: document body text.
        fallback: language to use when neither source decides. Callers pass
            the index-wide profile language so an undetected document lands
            where its index already is, instead of a hard-coded guess.

    Returns:
        A lowercase language subtag. Never :data:`UNDETERMINED`.

    Examples:
        >>> resolve_language({"lang": "el"}, "irrelevant", "tr")
        'el'
        >>> resolve_language(None, "short", "tr")
        'tr'
    """
    declared = language_from_frontmatter(frontmatter)
    if declared:
        return declared
    detected = detect_language(body)
    return detected if detected else fallback
