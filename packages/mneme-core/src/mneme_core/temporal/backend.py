"""Temporal retrieval backend compatible with the RRF ``kg_backend`` slot.

:func:`make_temporal_backend` returns a callable with the
:class:`~mneme_core.retrieval.rrf.RetrievalBackend` signature.  It queries the
``claims`` table using a simple normalized LIKE match on ``statement_normalized``
and filters to claims live at ``as_of``.

If the ``claims`` table is absent or empty the callable returns ``[]``, giving a
clean FTS5 fallback: ``retrieve()`` has no kg leg, so only FTS5 hits are returned
without any configuration change.

Contradiction overlay
---------------------
Claims whose ``claim_key`` is in the contradiction set carry a different
``confidence_label`` when surfaced: the caller should overlay
``ConfidenceLabel.AMBIGUOUS`` on those hits.  Use :func:`ambiguous_claim_ids`
to get the set of ids at a given time, then relabel before returning.

Note: the score is deterministic (token match count), so backend results are
reproducible for a given query and database state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..retrieval.rrf import Hit
from .claim import ConfidenceLabel
from .query import _table_exists, find_contradictions
from .query import as_of as _as_of_fn


def ambiguous_claim_ids(
    conn: sqlite3.Connection,
    at: datetime | None = None,
) -> frozenset[str]:
    """Return the set of claim_ids that appear in any contradiction pair at ``at``.

    Callers can use this to overlay ``ConfidenceLabel.AMBIGUOUS`` on hits
    before returning them to the retrieval pipeline.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    at:
        Optional time point; forwarded to :func:`find_contradictions`.
        When provided, only live (non-shadowed) claims at ``at`` are
        considered — resolved supersessions are excluded so a superseded/
        superseder pair for the same ``claim_key`` is NOT flagged as an
        AMBIGUOUS contradiction.
        When ``None``, all claims regardless of time are checked (bulk-audit
        mode only — NOT suitable for the AMBIGUOUS overlay).
    """
    pairs = find_contradictions(conn, at=at)
    ids: set[str] = set()
    for a, b in pairs:
        ids.add(a)
        ids.add(b)
    return frozenset(ids)


def _token_match_score(statement_norm: str, query_tokens: list[str]) -> int:
    """Count how many query tokens appear in the normalized statement."""
    stmt_lower = statement_norm.lower()
    return sum(1 for tok in query_tokens if tok and tok.lower() in stmt_lower)


def make_temporal_backend(
    conn: sqlite3.Connection,
    *,
    normalize: Any = None,
    as_of: datetime | None = None,
    contradiction_keys: frozenset[str] | None = None,
) -> Callable[[str, int], list[Hit]]:
    """Build a :class:`~mneme_core.retrieval.rrf.RetrievalBackend`-compatible callable.

    The returned callable queries claims live at ``as_of`` (or all claims when
    ``as_of`` is ``None``) and returns :class:`~mneme_core.retrieval.rrf.Hit`
    instances with ``source="temporal"``.

    When the ``claims`` table is absent or empty the callable returns ``[]``
    so the retrieval pipeline degrades cleanly to FTS5-only results.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    normalize:
        Optional ``(str) -> str`` normalizer applied to the query before
        matching against ``statement_normalized``.  Defaults to identity.
    as_of:
        Optional time point for live-claim filtering.  When ``None``, all
        claims are searched regardless of validity window.
    contradiction_keys:
        Optional pre-computed frozenset of claim_ids in contradiction sets.
        Hits whose claim_id is in this set get ``confidence_label=AMBIGUOUS``
        in the returned Hit metadata.  When ``None``, no AMBIGUOUS overlay is
        applied.  Callers can build this with :func:`ambiguous_claim_ids`.

    Notes
    -----
    The matching strategy is a simple token-AND LIKE approach on
    ``statement_normalized``.  A future iteration can upgrade to FTS5 or
    vector search without changing the backend contract.

    The caller is responsible for surfacing ``confidence_label=AMBIGUOUS``
    for contradiction-set members; this function exposes the label in the
    Hit's ``title`` field (as a prefix) and ``score`` remains deterministic.
    """
    _normalize: Callable[[str], str] = normalize if normalize is not None else (lambda s: s)
    _at = as_of
    _contradiction_ids: frozenset[str] = (
        contradiction_keys if contradiction_keys is not None else frozenset()
    )

    def _backend(query: str, limit: int) -> list[Hit]:
        if not _table_exists(conn):
            return []

        q_norm = _normalize(query)
        tokens = [t for t in q_norm.split() if len(t) >= 2]
        if not tokens:
            return []

        # Fetch candidate rows.
        try:
            if _at is not None:
                live_claims = _as_of_fn(conn, _at)
            else:
                from .query import _all_columns, _row_to_claim

                cols = _all_columns()
                rows = conn.execute(f"SELECT {cols} FROM claims").fetchall()  # noqa: S608
                live_claims = [_row_to_claim(r) for r in rows]
        except Exception:  # noqa: BLE001 - degrade gracefully
            return []

        if not live_claims:
            return []

        # Score and filter.
        scored: list[tuple[int, Any]] = []
        for claim in live_claims:
            score = _token_match_score(claim.statement_normalized, tokens)
            if score > 0:
                scored.append((score, claim))

        # Sort descending by score.
        scored.sort(key=lambda x: -x[0])

        hits: list[Hit] = []
        for _rank, (score, claim) in enumerate(scored[:limit], start=1):
            is_ambiguous = claim.claim_id in _contradiction_ids
            label = (
                ConfidenceLabel.AMBIGUOUS if is_ambiguous else claim.confidence_label
            )
            title_prefix = f"[{label.value}] " if is_ambiguous else ""
            hits.append(
                Hit(
                    id=claim.claim_id,
                    path=claim.path,
                    title=f"{title_prefix}{claim.statement}",
                    score=float(score),
                    source="temporal",
                )
            )
        return hits

    return _backend
