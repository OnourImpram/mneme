"""Temporal claim extraction from note body text.

This module is the reserved INFERRED producer described in
:class:`~mneme_core.temporal.claim.ConfidenceLabel`.  All verbatim
frontmatter claims are EXTRACTED at index time; this module derives
additional temporal claims from free-text note bodies.

Design constraints
------------------
* **Pure rule-based default** — deterministic, no network, no LLM import at
  module level.  Safe to import on the critical/Stop path even though the
  extractor itself is intended to run as a background job.
* **Optional LLM enrichment** — callers may inject a
  :class:`~mneme_core.compression.llm.LlmProvider` for richer extraction.
  When the provider raises or returns malformed JSON the function degrades
  gracefully to the rule-based path and never raises.
* **Privacy-first** — every emitted statement is passed through
  :func:`~mneme_core.privacy.redact` before it leaves this module.

Usage
-----
::

    from mneme_core.temporal.extract import extract_claims

    candidates = extract_claims(body_text, source_path="notes/foo.md")
    # with LLM enrichment (background job only):
    candidates = extract_claims(body_text, source_path="notes/foo.md",
                                provider=my_provider)
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..compression.llm import LlmCallSpec, LlmProvider, LlmProviderError
from ..privacy import redact
from ..vault.frontmatter import _parse_dt  # noqa: PLC2701
from .claim import ConfidenceLabel

# ---------------------------------------------------------------------------
# Regex patterns used by the rule-based extractor
# ---------------------------------------------------------------------------

# Sentence-boundary split: period, exclamation mark, or bare newline.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!])\s+|\n+")

# Copula forms that signal a factual / existential statement.
_COPULA_RE = re.compile(r"\b(is|was|are|were|will\s+be)\b", re.IGNORECASE)

# Dated qualifier: "since/as of/effective/until/through <YYYY-MM-DD>"
# The date portion is captured in group 1; the keyword in group 0 prefix.
_DATED_QUALIFIER_RE = re.compile(
    r"\b(since|as\s+of|effective|until|through)\s+(\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

# Keywords that push their date into valid_from vs valid_to.
_VALID_FROM_KEYWORDS: frozenset[str] = frozenset({"since", "as of", "effective"})
_VALID_TO_KEYWORDS: frozenset[str] = frozenset({"until", "through"})

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimCandidate:
    """A single temporal claim candidate derived from a note body.

    All :attr:`statement` values have already been passed through
    :func:`~mneme_core.privacy.redact`.

    Attributes
    ----------
    statement:
        Redacted claim text (a single sentence or LLM-derived assertion).
    source_path:
        Vault-relative path of the note the candidate was derived from.
    valid_from:
        Inclusive start of the claim's validity window, when a dated
        qualifier ``since/as of/effective <YYYY-MM-DD>`` was present.
    valid_to:
        Exclusive end of the claim's validity window, when a dated
        qualifier ``until/through <YYYY-MM-DD>`` was present.
    observed_at:
        When the claim was observed (not set by rule-based path).
    claim_key:
        Semantic deduplication key.  Left ``None`` by the rule-based path
        because an incorrect key is worse than no key.
    scope:
        Vault scope that owns the source note. Defaults to ``default``.
    confidence:
        Always :attr:`~mneme_core.temporal.claim.ConfidenceLabel.INFERRED`
        for candidates produced by this module.
    """

    statement: str
    source_path: str
    valid_from: datetime | None = field(default=None)
    valid_to: datetime | None = field(default=None)
    observed_at: datetime | None = field(default=None)
    claim_key: str | None = field(default=None)
    scope: str = field(default="default")
    confidence: ConfidenceLabel = field(default=ConfidenceLabel.INFERRED)


# ---------------------------------------------------------------------------
# Rule-based extractor (deterministic, no network)
# ---------------------------------------------------------------------------


def extract_claims_rule_based(
    text: str,
    *,
    source_path: str,
    scope: str = "default",
) -> list[ClaimCandidate]:
    """Derive :class:`ClaimCandidate` objects from *text* using pure rules.

    Algorithm
    ---------
    1. Split *text* into sentences on ``.``, ``!``, or bare newlines.
    2. Skip sentences shorter than 3 words.
    3. Emit a candidate when the sentence contains a copula
       (``is/was/are/were/will be``) OR a dated qualifier
       (``since/as of/effective/until/through <YYYY-MM-DD>``).
    4. For dated qualifiers parse ``valid_from`` / ``valid_to`` via
       :func:`~mneme_core.temporal.claim._parse_dt`.
    5. Redact the statement before storing.

    The function never raises; any per-sentence error is silently skipped.
    """
    candidates: list[ClaimCandidate] = []
    if not text:
        return candidates

    sentences = _SENTENCE_SPLIT_RE.split(text)

    for raw in sentences:
        sentence = raw.strip()
        if not sentence:
            continue
        # Skip very short fragments (fewer than 3 whitespace-separated tokens).
        if len(sentence.split()) < 3:
            continue

        has_copula = bool(_COPULA_RE.search(sentence))
        dated_matches = list(_DATED_QUALIFIER_RE.finditer(sentence))
        has_dated = len(dated_matches) > 0

        if not has_copula and not has_dated:
            continue

        # Parse date qualifiers.
        valid_from: datetime | None = None
        valid_to: datetime | None = None
        for m in dated_matches:
            keyword = m.group(1).lower().replace("  ", " ").strip()
            date_str = m.group(2)
            dt = _parse_dt(date_str)
            if dt is None:
                continue
            # Normalise multi-word keywords (e.g. "as  of" -> "as of").
            normalised = " ".join(keyword.split())
            if normalised in _VALID_FROM_KEYWORDS:
                valid_from = dt
            elif normalised in _VALID_TO_KEYWORDS:
                valid_to = dt

        statement = redact(sentence)
        candidates.append(
            ClaimCandidate(
                statement=statement,
                source_path=source_path,
                valid_from=valid_from,
                valid_to=valid_to,
                scope=scope,
                confidence=ConfidenceLabel.INFERRED,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# LLM system prompt (kept here so it ships with the module, not a file)
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a temporal claim extractor. "
    "Given a piece of note text, return ONLY a JSON array (no markdown, no prose) "
    "of objects, one per temporal or factual claim you find. "
    "Each object must have exactly these fields: "
    '{"statement": "<claim text>", "valid_from": "<YYYY-MM-DD or omit>", '
    '"valid_to": "<YYYY-MM-DD or omit>"}. '
    "Omit valid_from and valid_to when they are not present in the text. "
    "Do not include any explanation outside the JSON array. "
    "Return an empty array [] when no claims are found."
)


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------


def extract_claims(
    text: str,
    *,
    source_path: str,
    scope: str = "default",
    provider: LlmProvider | None = None,
) -> list[ClaimCandidate]:
    """Extract temporal :class:`ClaimCandidate` objects from *text*.

    When *provider* is ``None`` the deterministic rule-based path is used.
    When a provider is supplied the LLM result is parsed; any failure
    (network error, quota, malformed JSON, unexpected structure) causes a
    transparent fallback to the rule-based path — the function never raises.

    Parameters
    ----------
    text:
        Raw note body text to analyse.
    source_path:
        Vault-relative path of the source note, stored on every candidate.
    scope:
        Vault scope that owns the source note.
    provider:
        Optional :class:`~mneme_core.compression.llm.LlmProvider`.  Must be
        ``None`` on the critical/Stop path; pass a provider only from a
        background job.

    Returns
    -------
    list[ClaimCandidate]
        Candidates in document order.  Empty when *text* is empty or no
        temporal/factual sentences are found.
    """
    if provider is None:
        return extract_claims_rule_based(text, source_path=source_path, scope=scope)

    try:
        spec = LlmCallSpec(system=_EXTRACT_SYSTEM, payload=redact(text))
        result = provider.compress(spec)
        raw_json = result.text.strip()
        parsed: object = json.loads(raw_json)
        if not isinstance(parsed, list):
            return extract_claims_rule_based(text, source_path=source_path, scope=scope)
        candidates: list[ClaimCandidate] = []
        for obj in parsed:
            if not isinstance(obj, dict):
                continue
            stmt_raw = obj.get("statement")
            if not isinstance(stmt_raw, str) or not stmt_raw.strip():
                continue
            statement = redact(stmt_raw.strip())
            vf_raw = obj.get("valid_from")
            vt_raw = obj.get("valid_to")
            valid_from: datetime | None = _parse_dt(vf_raw) if vf_raw else None
            valid_to: datetime | None = _parse_dt(vt_raw) if vt_raw else None
            candidates.append(
                ClaimCandidate(
                    statement=statement,
                    source_path=source_path,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    scope=scope,
                    confidence=ConfidenceLabel.INFERRED,
                )
            )
        return candidates
    except (LlmProviderError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return extract_claims_rule_based(text, source_path=source_path, scope=scope)
    except Exception:  # noqa: BLE001 — any unexpected error degrades gracefully
        return extract_claims_rule_based(text, source_path=source_path, scope=scope)


__all__: Sequence[str] = [
    "ClaimCandidate",
    "extract_claims",
    "extract_claims_rule_based",
]
