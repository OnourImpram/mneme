"""Reciprocal Rank Fusion over a hybrid retrieval pipeline.

Given a query string, run any number of search backends (FTS5 built in,
plus optional injected backends for dense vector and temporal knowledge
graph search), fuse the per-backend rankings with RRF, and return the
top-N. The RRF default ``k`` of 60 follows Cormack et al. 2009 and is
the value most papers report as robust across tasks.

The pipeline is intentionally side-effect free at import time. Callers
own the database connection and the ``RetrievalConfig`` that supplies
paths, the normalizer, and tunables.

This module deliberately does not handle telemetry or output
formatting. Both are downstream concerns: telemetry belongs in
``mneme_core.telemetry``, output formatting belongs in the hook script
that consumes ``retrieve``.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_RRF_K: int = 60

_FTS5_RESERVED = set('"-:^*()')


def _identity(s: str) -> str:
    return s


@dataclass
class Hit:
    """A single retrieval result before or after fusion."""

    id: int | str
    path: str
    title: str
    score: float
    source: str
    rrf_score: float = 0.0
    sources: list[str] = field(default_factory=list)


class RetrievalBackend(Protocol):
    """Signature shared by all retrieval backends.

    Implementations receive the (normalized) query and a per-backend
    limit, and return a ranked list of ``Hit`` records. Backends should
    return an empty list on failure rather than raising, so the pipeline
    can degrade gracefully when one leg is unavailable.
    """

    def __call__(self, query: str, limit: int) -> list[Hit]: ...


@dataclass
class RetrievalConfig:
    """Configuration for the retrieval pipeline.

    Attributes:
        fts5_db: SQLite database path produced by
            ``mneme_core.fts5.indexer``.
        normalize: token-level normalizer. Defaults to identity. Pass
            ``mneme_core.fts5.locale.tr.normalize_tr`` for Turkish
            vaults.
        min_query_length: queries shorter than this character count
            return an empty list without running any backend. Matches
            the gate established by claude-mem and others to avoid
            spurious noise on greetings.
        top_k_per_backend: max results pulled from each backend before
            fusion.
        top_n_final: max results returned after fusion (and reranking
            if a reranker is plugged in).
        rrf_k: RRF constant. Default 60.
        total_budget_ms: soft deadline. Backends that exceed it should
            return what they have so far. Currently advisory.
        stopwords: optional set of low-information tokens to drop
            before building the FTS5 MATCH query. Empty by default.
    """

    fts5_db: Path
    normalize: Callable[[str], str] = _identity
    min_query_length: int = 20
    top_k_per_backend: int = 50
    top_n_final: int = 5
    rrf_k: int = DEFAULT_RRF_K
    total_budget_ms: int = 500
    stopwords: frozenset[str] = field(default_factory=frozenset)


def build_fts5_query(prompt_norm: str, stopwords: frozenset[str] = frozenset()) -> str:
    """Tokenize, escape, and OR-join a normalized prompt for FTS5 MATCH.

    Each token is wrapped in double quotes for phrase-mode safety so
    that FTS5 operators inside the token are treated as literal text.
    Tokens shorter than 2 characters and configured stopwords are
    dropped to reduce false-positive recall.

    Returns the empty string if no tokens survive filtering.
    """
    tokens: list[str] = []
    for raw in prompt_norm.split():
        t = "".join(c for c in raw if c not in _FTS5_RESERVED)
        if len(t) < 2:
            continue
        if t in stopwords:
            continue
        tokens.append(f'"{t}"')
    return " OR ".join(tokens)


def fts5_search(
    prompt_norm: str,
    db_path: Path,
    limit: int = 50,
    stopwords: frozenset[str] = frozenset(),
) -> list[Hit]:
    """Run a single FTS5 BM25 search against the mneme indexer schema.

    Opens the database in read-only mode and returns at most ``limit``
    hits ordered by FTS5 rank (lower is better, per SQLite convention).
    Returns an empty list when the database does not exist, the query
    is empty after filtering, or any SQLite error occurs.
    """
    if not db_path.exists():
        return []
    fts_query = build_fts5_query(prompt_norm, stopwords)
    if not fts_query:
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            """SELECT documents.id, documents.path, documents.title,
                      documents_fts.rank
               FROM documents_fts
               JOIN documents ON documents_fts.rowid = documents.id
               WHERE documents_fts MATCH ?
               ORDER BY documents_fts.rank
               LIMIT ?""",
            (fts_query, limit),
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []

    return [
        Hit(
            id=r[0],
            path=r[1],
            title=r[2] or "",
            score=float(r[3]),
            source="fts5",
        )
        for r in rows
    ]


def rrf_fuse(rankings: list[list[Hit]], k: int = DEFAULT_RRF_K) -> list[Hit]:
    """Fuse multiple ranked lists with Reciprocal Rank Fusion.

    For each document, the fused score is the sum across rankings of
    ``1 / (k + rank_i)`` where ``rank_i`` is the 1-indexed position in
    ranking ``i``. Documents not present in a ranking contribute zero
    for that leg.

    Returns a new list of ``Hit`` records, each carrying an
    ``rrf_score`` and the deduplicated list of source backends that
    produced it.
    """
    scores: dict[int | str, dict[str, object]] = {}
    for ranking in rankings:
        for rank_idx, hit in enumerate(ranking, start=1):
            key = hit.id if hit.id is not None else hit.path
            if key not in scores:
                scores[key] = {
                    "hit": hit,
                    "score": 0.0,
                    "sources": set(),
                }
            entry = scores[key]
            entry["score"] = float(entry["score"]) + 1.0 / (k + rank_idx)
            source_set = entry["sources"]
            assert isinstance(source_set, set)
            source_set.add(hit.source)

    fused = sorted(scores.values(), key=lambda x: -float(x["score"]))
    result: list[Hit] = []
    for entry in fused:
        original = entry["hit"]
        assert isinstance(original, Hit)
        source_set = entry["sources"]
        assert isinstance(source_set, set)
        new_hit = Hit(
            id=original.id,
            path=original.path,
            title=original.title,
            score=original.score,
            source=original.source,
            rrf_score=float(entry["score"]),
            sources=sorted(source_set),
        )
        result.append(new_hit)
    return result


def retrieve(
    query: str,
    config: RetrievalConfig,
    dense_backend: RetrievalBackend | None = None,
    kg_backend: RetrievalBackend | None = None,
    reranker: Callable[[str, list[Hit], int], list[Hit]] | None = None,
) -> list[Hit]:
    """Run the full hybrid retrieval pipeline and return top-N hits.

    Steps:

    1. Apply the ``min_query_length`` gate. Empty list if too short.
    2. Normalize the query.
    3. Run FTS5 always. Run dense and KG backends if provided.
    4. Fuse with RRF at the configured ``k``.
    5. Apply the optional reranker, otherwise truncate to
       ``top_n_final``.
    """
    if len(query) < config.min_query_length:
        return []

    q_norm = config.normalize(query)
    fts5_results = fts5_search(
        q_norm,
        config.fts5_db,
        limit=config.top_k_per_backend,
        stopwords=config.stopwords,
    )
    rankings: list[list[Hit]] = [fts5_results]

    if dense_backend is not None:
        try:
            dense_results = dense_backend(q_norm, config.top_k_per_backend)
        except Exception:
            dense_results = []
        rankings.append(dense_results)

    if kg_backend is not None:
        try:
            kg_results = kg_backend(q_norm, config.top_k_per_backend)
        except Exception:
            kg_results = []
        rankings.append(kg_results)

    fused = rrf_fuse(rankings, k=config.rrf_k)

    if reranker is not None:
        try:
            return reranker(q_norm, fused, config.top_n_final)
        except Exception:
            return fused[: config.top_n_final]
    return fused[: config.top_n_final]


def _now_perf_ms(start_perf_counter: float) -> float:
    return (time.perf_counter() - start_perf_counter) * 1000.0
