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
Claims in the contradiction set carry
``confidence_label=ConfidenceLabel.AMBIGUOUS.value`` when surfaced. The
backend computes that set for the retrieval snapshot unless the caller
provides an explicit precomputed set.

Note: the score is deterministic (token match count), so backend results are
reproducible for a given query and database state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..retrieval.rrf import Hit, RetrievalBackend
from .claim import ConfidenceLabel
from .query import _table_exists, find_contradictions
from .query import as_of as _as_of_fn


def _result_id(claim_scope: str, claim_id: str, requested_scope: str) -> str:
    """Keep concrete IDs stable and disambiguate explicit cross-scope reads."""
    if requested_scope != "*":
        return claim_id
    return f"{len(claim_scope)}:{claim_scope}:{claim_id}"


def ambiguous_claim_ids(
    conn: sqlite3.Connection,
    at: datetime | None = None,
    *,
    scope: str = "default",
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

    Explicit cross-scope results use a length-prefixed scope in their IDs so
    equal claim IDs in independent scopes remain distinct through RRF.
    """
    ids: set[str] = set()
    if scope == "*":
        if not _table_exists(conn):
            return frozenset()
        concrete_scopes = [
            str(row[0]) for row in conn.execute("SELECT DISTINCT scope FROM claims").fetchall()
        ]
        for concrete_scope in concrete_scopes:
            for a, b in find_contradictions(conn, at=at, scope=concrete_scope):
                ids.add(_result_id(concrete_scope, a, scope))
                ids.add(_result_id(concrete_scope, b, scope))
    else:
        for a, b in find_contradictions(conn, at=at, scope=scope):
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
    scope: str = "default",
    contradiction_keys: frozenset[str] | None = None,
) -> RetrievalBackend:
    """Build a :class:`~mneme_core.retrieval.rrf.RetrievalBackend`-compatible callable.

    The returned callable queries claims live at ``as_of``. When ``as_of`` is
    ``None``, each call uses one current UTC snapshot for validity,
    supersession, and contradiction evaluation. Returned
    :class:`~mneme_core.retrieval.rrf.Hit` instances carry structured temporal
    provenance and confidence fields with ``source="temporal"``.

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
        Optional time point for live-claim filtering. When ``None``, the
        current UTC time at each backend call is used.
    scope:
        Concrete scope to search. Pass ``"*"`` only for an explicit
        cross-scope query.
    contradiction_keys:
        Optional pre-computed frozenset of claim_ids in contradiction sets.
        Hits whose claim_id is in this set get ``confidence_label=AMBIGUOUS``
        in the returned Hit metadata. When ``None``, contradiction identities
        are computed automatically for the same snapshot as retrieval.

    Notes
    -----
    The matching strategy is a simple token-AND LIKE approach on
    ``statement_normalized``.  A future iteration can upgrade to FTS5 or
    vector search without changing the backend contract.

    ``confidence_label``, ``trust``, and ``content_hash`` are populated as
    structured Hit fields. The score remains deterministic.
    """
    _normalize: Callable[[str], str] = normalize if normalize is not None else (lambda s: s)
    _at = as_of
    _contradiction_ids = contradiction_keys

    def _backend(query: str, limit: int) -> list[Hit]:
        if not _table_exists(conn):
            return []

        q_norm = _normalize(query)
        tokens = [t for t in q_norm.split() if len(t) >= 2]
        if not tokens:
            return []

        query_at = _at if _at is not None else datetime.now(UTC)

        # Fetch candidate rows.
        try:
            live_claims = _as_of_fn(conn, query_at, scope=scope)
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

        # Sort descending by score with stable cross-scope tie breakers.
        scored.sort(key=lambda item: (-item[0], item[1].scope, item[1].claim_id, item[1].path))
        if not scored:
            return []

        contradiction_ids = _contradiction_ids
        if contradiction_ids is None:
            try:
                contradiction_ids = ambiguous_claim_ids(conn, at=query_at, scope=scope)
            except Exception:  # noqa: BLE001 - degrade gracefully
                return []

        hits: list[Hit] = []
        for _rank, (score, claim) in enumerate(scored[:limit], start=1):
            result_id = _result_id(claim.scope, claim.claim_id, scope)
            is_ambiguous = result_id in contradiction_ids
            label = ConfidenceLabel.AMBIGUOUS if is_ambiguous else claim.confidence_label
            hits.append(
                Hit(
                    id=result_id,
                    path=claim.path,
                    title=claim.statement,
                    score=float(score),
                    source="temporal",
                    content_hash=claim.content_hash,
                    trust=claim.trust,
                    confidence_label=label.value,
                )
            )
        return hits

    return _backend
