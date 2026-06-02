"""Graphiti episode serialisation bridge for temporal claims.

Converts :class:`~mneme_core.temporal.extract.ClaimCandidate` and
:class:`~mneme_core.temporal.claim.Claim` objects into plain ``dict``
values that match the Graphiti episode ingestion schema.

Design constraints
------------------
* **Pure dict serialisation** — no Neo4j import, no network calls, no
  third-party dependencies beyond the standard library.
* **Deterministic** — given the same input the output is always identical.
* **Operator note** — the live Neo4j write path is intentionally out of
  scope for this module.  The operator ingests the returned episode dicts
  into a Graphiti/Neo4j instance out of band, e.g.::

      from graphiti_core import Graphiti

      g = Graphiti(neo4j_uri, neo4j_user, neo4j_password)
      for ep_dict in episodes_from_candidates(candidates):
          await g.add_episode(**ep_dict)

  A CI service-container for the live Neo4j integration lives in
  ``tests/integration/test_kg_neo4j_integration.py`` and is gated behind
  the ``full`` optional-dependency profile.

Public API
----------
.. autofunction:: mneme_core.temporal.graphiti_export.episode_from_candidate
.. autofunction:: mneme_core.temporal.graphiti_export.episodes_from_candidates
.. autofunction:: mneme_core.temporal.graphiti_export.episode_from_claim
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .claim import Claim, _to_utc_iso  # noqa: PLC2701
from .extract import ClaimCandidate

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_GROUP_ID = "mneme-temporal"
_NAME_MAX_LEN = 60

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int = _NAME_MAX_LEN) -> str:
    """Return at most *max_len* characters of *text*, no ellipsis added."""
    return text[:max_len]


def _reference_time_from_candidate(c: ClaimCandidate) -> str:
    """Return an ISO UTC string for the episode ``reference_time`` field.

    Priority: ``valid_from`` → ``observed_at`` → ``""`` (empty string when
    neither is available).
    """
    dt = c.valid_from if c.valid_from is not None else c.observed_at
    if dt is None:
        return ""
    return _to_utc_iso(dt)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def episode_from_candidate(c: ClaimCandidate) -> dict[str, object]:
    """Serialise a :class:`~mneme_core.temporal.extract.ClaimCandidate` to a
    Graphiti episode dict.

    The ``episode_body`` field carries the already-redacted statement from
    the candidate; no additional redaction is applied here.

    Parameters
    ----------
    c:
        A :class:`~mneme_core.temporal.extract.ClaimCandidate` produced by
        :func:`~mneme_core.temporal.extract.extract_claims`.

    Returns
    -------
    dict[str, object]
        Graphiti-compatible episode mapping with keys ``name``,
        ``episode_body``, ``reference_time``, ``source``,
        ``source_description``, ``group_id``, and ``confidence``.
    """
    return {
        "name": _truncate(c.statement),
        "episode_body": c.statement,
        "reference_time": _reference_time_from_candidate(c),
        "source": "mneme",
        "source_description": c.source_path,
        "group_id": _GROUP_ID,
        "confidence": c.confidence.value,
    }


def episodes_from_candidates(
    cs: Iterable[ClaimCandidate],
) -> list[dict[str, object]]:
    """Serialise an iterable of :class:`~mneme_core.temporal.extract.ClaimCandidate`
    objects, preserving order.

    Parameters
    ----------
    cs:
        Any iterable of :class:`~mneme_core.temporal.extract.ClaimCandidate`.

    Returns
    -------
    list[dict[str, object]]
        One episode dict per candidate, in iteration order.
    """
    return [episode_from_candidate(c) for c in cs]


def episode_from_claim(claim: Claim) -> dict[str, object]:
    """Serialise a verbatim :class:`~mneme_core.temporal.claim.Claim` to a
    Graphiti episode dict.

    Both verbatim (EXTRACTED) and inferred (INFERRED) claims export to the
    same episode shape, enabling uniform downstream ingestion regardless of
    how the claim was produced.

    Parameters
    ----------
    claim:
        A :class:`~mneme_core.temporal.claim.Claim` from the vault index.

    Returns
    -------
    dict[str, object]
        Graphiti-compatible episode mapping identical in shape to the output
        of :func:`episode_from_candidate`.
    """
    dt = claim.valid_from if claim.valid_from is not None else claim.observed_at
    reference_time = _to_utc_iso(dt) if dt is not None else ""
    return {
        "name": _truncate(claim.statement),
        "episode_body": claim.statement,
        "reference_time": reference_time,
        "source": "mneme",
        "source_description": claim.path,
        "group_id": _GROUP_ID,
        "confidence": claim.confidence_label.value,
    }


__all__: Sequence[str] = [
    "episode_from_candidate",
    "episode_from_claim",
    "episodes_from_candidates",
]
