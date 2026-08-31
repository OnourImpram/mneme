"""English (and default Latin) normalization for FTS5.

Mirrors ``mneme_mcp/src/locale/en.ts`` so the Python indexer and the
TypeScript retrieval path emit identical tokens. A cross-language parity
test asserts the two stay in step.

This is plain Unicode lowercase, deliberately NOT the Turkish fold. The
difference is visible in both directions::

    normalize_tr("API")  ->  "apı"   # I is a distinct Turkish letter
    normalize_en("API")  ->  "api"

Before 4.0 every vault was indexed with the Turkish fold regardless of its
content, so an English corpus stored ``apı`` and ``ı/o``. Queries still
matched because the same fold ran at query time, which is exactly why the
defect stayed invisible: retrieval "worked" while the two languages were
indistinguishable in the index.

There is no ASCII-fold sibling here. That second key exists to bridge the
Turkish dotted/dotless ``i``; English has no such ambiguity, so adding one
would duplicate the index for no recall gain.

**Length invariant.** The TS snippet builder locates a match in the
normalized body and then slices the *original* body at that offset, so a
normalizer must map one code unit to exactly one. Python's ``str.lower()``
breaks this for U+0130 the same way JavaScript does::

    "İ".lower()  ->  "i̇"   # one character becomes two

U+0130 is therefore folded to plain ``i`` explicitly, before lowercasing.
Dotless ``I`` is deliberately left alone: mapping it to ``ı`` is the Turkish
rule and would be wrong for English.
"""

from __future__ import annotations

#: Latin capital letter I with dot above. Folded before ``str.lower()`` so
#: the transform stays length-preserving (see module docstring).
_DOTTED_CAPITAL_I = "İ"


def normalize_en(s: str) -> str:
    """Lowercase for FTS5 ingest or query, preserving string length.

    Args:
        s: Input string in any text.

    Returns:
        Lowercased string suitable for FTS5 ingest or query.

    Examples:
        >>> normalize_en("API")
        'api'
        >>> normalize_en("Istanbul")
        'istanbul'
        >>> normalize_en("İstanbul")
        'istanbul'
        >>> len(normalize_en("İ")) == len("İ")
        True
    """
    return s.replace(_DOTTED_CAPITAL_I, "i").lower()


def normalize_en_for_fts(s: str) -> str:
    """Lowercase + collapse internal whitespace for FTS5 ingest.

    The whitespace-collapsing counterpart of :func:`normalize_en`, mirroring
    how :func:`~mneme_core.fts5.locale.tr.normalize_tr_for_fts` relates to
    :func:`~mneme_core.fts5.locale.tr.normalize_tr`.

    Args:
        s: Input string, possibly multi-line.

    Returns:
        Single-line lowercased string with single-space separation.
    """
    return " ".join(normalize_en(s).split())
