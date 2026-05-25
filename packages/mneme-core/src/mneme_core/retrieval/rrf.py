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

import contextlib
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_RRF_K: int = 60

# FTS5-reserved and unicode61 tokenizer-separator characters. A word
# containing any of these is split into a phrase rather than fused, so a
# hyphenated identifier matches the adjacent tokens the tokenizer indexed.
_FTS5_TOKEN_SEP = re.compile(r'[-":\^*()]+')


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
        normalize_ascii: optional ASCII-fold normalizer enabling the
            dual-key Turkish recall path. When set (pass
            ``mneme_core.fts5.locale.tr.normalize_tr_ascii_fold``), the
            FTS5 leg also matches the ASCII-fold key so an ASCII-capital
            query recalls dotted Turkish spellings. ``None`` (default)
            queries only the CLDR key, preserving prior behavior.
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
    normalize_ascii: Callable[[str], str] | None = None
    min_query_length: int = 20
    top_k_per_backend: int = 50
    top_n_final: int = 5
    rrf_k: int = DEFAULT_RRF_K
    total_budget_ms: int = 500
    stopwords: frozenset[str] = field(default_factory=frozenset)


def build_fts5_query(prompt_norm: str, stopwords: frozenset[str] = frozenset()) -> str:
    """Tokenize, escape, and OR-join a normalized prompt for FTS5 MATCH.

    Each whitespace-separated word is split on FTS5-reserved and
    tokenizer-separator characters and rejoined as one quoted phrase, so
    a hyphenated identifier like ``claude-mem`` becomes the phrase
    ``"claude mem"``. That matches the adjacent ``claude`` and ``mem``
    tokens the unicode61 tokenizer actually indexed; the previous
    strip-and-fuse approach produced ``"claudemem"`` and matched nothing.
    Quoting keeps any surviving FTS5 operator literal. Subtokens shorter
    than 2 characters and configured stopwords are dropped.

    Returns the empty string if no tokens survive filtering.
    """
    phrases: list[str] = []
    for raw in prompt_norm.split():
        parts = [
            p
            for p in _FTS5_TOKEN_SEP.split(raw)
            if len(p) >= 2 and p not in stopwords
        ]
        if parts:
            phrases.append('"' + " ".join(parts) + '"')
    return " OR ".join(phrases)


# FTS5 tables the search layer is allowed to MATCH against. Names are
# interpolated into SQL, so they are validated against this allowlist to keep
# the query construction injection-proof (the values are always module
# literals, never caller input).
_FTS_TABLES: frozenset[str] = frozenset({"documents_fts", "documents_ascii_fts"})


def _match_rows(
    conn: sqlite3.Connection, fts_table: str, fts_query: str, limit: int
) -> list[tuple[Any, ...]]:
    """Run one FTS5 MATCH against ``fts_table`` and return raw rows.

    ``fts_table`` must be one of :data:`_FTS_TABLES`; it is validated before
    interpolation so the formatted SQL cannot carry untrusted input.
    """
    if fts_table not in _FTS_TABLES:
        raise ValueError(f"unknown FTS table: {fts_table!r}")
    return conn.execute(  # noqa: S608 - table name validated against allowlist
        f"""SELECT documents.id, documents.path, documents.title,
                   {fts_table}.rank
            FROM {fts_table}
            JOIN documents ON {fts_table}.rowid = documents.id
            WHERE {fts_table} MATCH ?
            ORDER BY {fts_table}.rank
            LIMIT ?""",
        (fts_query, limit),
    ).fetchall()


def fts5_search(
    prompt_norm: str,
    db_path: Path,
    limit: int = 50,
    stopwords: frozenset[str] = frozenset(),
    prompt_ascii: str | None = None,
) -> list[Hit]:
    """Run an FTS5 BM25 search against the mneme indexer schema.

    Opens the database read-only and returns at most ``limit`` hits ordered
    by FTS5 rank (lower is better, per SQLite convention). Returns an empty
    list when the database does not exist, both queries are empty after
    filtering, or any SQLite error occurs.

    When ``prompt_ascii`` is supplied (the ASCII-fold of the query for a
    Turkish vault), the sibling ``documents_ascii_fts`` table is queried as a
    second key and its hits are merged with the CLDR hits, deduplicated by
    document id keeping the best (lowest) rank. This is a single fused FTS5
    leg: callers fuse it with dense/KG legs via RRF unchanged. A database
    built before dual-key indexing simply lacks the ASCII table, so that leg
    degrades to no extra recall without disturbing the CLDR results.
    """
    if not db_path.exists():
        return []
    cldr_query = build_fts5_query(prompt_norm, stopwords)
    ascii_query = build_fts5_query(prompt_ascii, stopwords) if prompt_ascii else ""
    if not cldr_query and not ascii_query:
        return []
    rows: list[tuple[Any, ...]] = []
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ) as conn:
            conn.execute("PRAGMA query_only = ON")
            if cldr_query:
                rows.extend(_match_rows(conn, "documents_fts", cldr_query, limit))
            if ascii_query:
                # The ASCII-fold key is a strictly best-effort recall boost:
                # the sibling table may be absent on a pre-dual-key database,
                # and either way its failure must never discard the CLDR rows
                # already collected above. Swallow any SQLite error from this
                # leg so it can only ever add recall, never remove it.
                with contextlib.suppress(sqlite3.Error):
                    rows.extend(
                        _match_rows(
                            conn, "documents_ascii_fts", ascii_query, limit
                        )
                    )
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []

    # Deduplicate by document id, keeping the best (lowest) rank seen across
    # either key, then order by rank and truncate to the limit.
    best: dict[Any, Hit] = {}
    for r in rows:
        doc_id = r[0]
        rank = float(r[3])
        current = best.get(doc_id)
        if current is None or rank < current.score:
            best[doc_id] = Hit(
                id=doc_id,
                path=r[1],
                title=r[2] or "",
                score=rank,
                source="fts5",
            )
    return sorted(best.values(), key=lambda h: h.score)[:limit]


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
    scores: dict[int | str, dict[str, Any]] = {}
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
    q_ascii = (
        config.normalize_ascii(query)
        if config.normalize_ascii is not None
        else None
    )
    fts5_results = fts5_search(
        q_norm,
        config.fts5_db,
        limit=config.top_k_per_backend,
        stopwords=config.stopwords,
        prompt_ascii=q_ascii,
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
