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
from typing import TYPE_CHECKING, Any, Protocol

from ..scope import DEFAULT_SCOPE, valid_scope

if TYPE_CHECKING:
    from ..vault.config import VaultConfig
    from .telemetry import BackendTelemetryStatus

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
    content_hash: str | None = None
    trust: str | None = None
    confidence_label: str | None = None


class RetrievalIndexStaleError(RuntimeError):
    """Raised when a derived FTS5 index cannot enforce the query contract."""


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
        min_query_length: a query is gated (returns empty) when its
            stripped length is below this character count, OR it has
            fewer than ``min_query_words`` meaningful tokens after
            stopword removal — i.e. reject empty or greeting-only
            queries. Default 3.
        min_query_words: a query is gated (returns empty) when it has
            fewer than this many meaningful tokens after stopword
            removal, OR its stripped length is below
            ``min_query_length`` characters — i.e. reject empty or
            greeting-only queries. Default 1.
        top_k_per_backend: max results pulled from each backend before
            fusion.
        top_n_final: max results returned after fusion (and reranking
            if a reranker is plugged in).
        rrf_k: RRF constant. Default 60.
        total_budget_ms: soft deadline. Backends that exceed it should
            return what they have so far. Currently advisory.
        stopwords: optional set of low-information tokens to drop
            before building the FTS5 MATCH query. Empty by default.
        scope: concrete FTS5 partition, or exact ``*`` for an intentional
            cross-scope read. Invalid values are rejected before any backend.
    """

    fts5_db: Path
    normalize: Callable[[str], str] = _identity
    normalize_ascii: Callable[[str], str] | None = None
    min_query_length: int = 3
    min_query_words: int = 1
    top_k_per_backend: int = 50
    top_n_final: int = 5
    rrf_k: int = DEFAULT_RRF_K
    total_budget_ms: int = 500
    stopwords: frozenset[str] = field(default_factory=frozenset)
    scope: str = DEFAULT_SCOPE


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
    conn: sqlite3.Connection,
    fts_table: str,
    fts_query: str,
    limit: int,
    scope: str,
) -> list[tuple[Any, ...]]:
    """Run one FTS5 MATCH against ``fts_table`` and return raw rows.

    ``fts_table`` must be one of :data:`_FTS_TABLES`; it is validated before
    interpolation so the formatted SQL cannot carry untrusted input.
    """
    if fts_table not in _FTS_TABLES:
        raise ValueError(f"unknown FTS table: {fts_table!r}")
    scope_clause = "" if scope == "*" else "AND documents.scope = ?"
    params: tuple[object, ...] = (
        (fts_query, limit) if scope == "*" else (fts_query, scope, limit)
    )
    return conn.execute(  # noqa: S608 - table name validated against allowlist
        f"""SELECT documents.id, documents.path, documents.title,
                   documents.content_hash, documents.trust, {fts_table}.rank
            FROM {fts_table}
            JOIN documents ON {fts_table}.rowid = documents.id
            WHERE {fts_table} MATCH ?
              {scope_clause}
            ORDER BY {fts_table}.rank
            LIMIT ?""",
        params,
    ).fetchall()


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})"))


def _require_queryable_index(
    conn: sqlite3.Connection,
    *,
    scope: str,
    ascii_query: str,
) -> None:
    columns = _table_columns(conn, "documents")
    if scope != "*" and "scope" not in columns:
        raise RetrievalIndexStaleError(
            "concrete-scope retrieval requires an index rebuild with scope metadata"
        )
    if not ascii_query:
        return
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_ascii_fts'"
    ).fetchone()
    try:
        profile_row = conn.execute(
            "SELECT value FROM index_meta WHERE key='ascii_normalization_profile'"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise RetrievalIndexStaleError(
            "dual-key retrieval requires an index rebuild with ASCII metadata"
        ) from exc
    if table_exists is None or profile_row is None or profile_row[0] == "disabled":
        raise RetrievalIndexStaleError(
            "dual-key retrieval requires an index rebuild with ASCII metadata"
        )


def fts5_search(
    prompt_norm: str,
    db_path: Path,
    limit: int = 50,
    stopwords: frozenset[str] = frozenset(),
    prompt_ascii: str | None = None,
    *,
    scope: str = DEFAULT_SCOPE,
    _fail_on_error: bool = False,
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
    resolved_scope = valid_scope(scope)
    if resolved_scope is None:
        raise ValueError("scope must be a valid concrete identifier or exact '*'")
    if not db_path.exists():
        if _fail_on_error:
            raise FileNotFoundError(db_path)
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
            _require_queryable_index(
                conn,
                scope=resolved_scope,
                ascii_query=ascii_query,
            )
            if cldr_query:
                rows.extend(
                    _match_rows(
                        conn,
                        "documents_fts",
                        cldr_query,
                        limit,
                        resolved_scope,
                    )
                )
            if ascii_query:
                rows.extend(
                    _match_rows(
                        conn,
                        "documents_ascii_fts",
                        ascii_query,
                        limit,
                        resolved_scope,
                    )
                )
    except RetrievalIndexStaleError:
        raise
    except sqlite3.OperationalError:
        if _fail_on_error:
            raise
        return []
    except Exception:
        if _fail_on_error:
            raise
        return []

    # Deduplicate by document id, keeping the best (lowest) rank seen across
    # either key, then order by rank and truncate to the limit.
    best: dict[Any, Hit] = {}
    for r in rows:
        doc_id = r[0]
        rank = float(r[5])
        current = best.get(doc_id)
        if current is None or rank < current.score:
            best[doc_id] = Hit(
                id=doc_id,
                path=r[1],
                title=r[2] or "",
                score=rank,
                source="fts5",
                content_hash=str(r[3]) if r[3] is not None else None,
                trust=str(r[4]) if r[4] is not None else None,
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
            # Vault-relative path is the cross-backend document identity.
            # Backend-local row or claim ids are not comparable domains.
            canonical_path = hit.path.replace("\\", "/").removeprefix("./")
            key = f"path:{canonical_path}" if canonical_path else hit.id
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
            for metadata_key in ("content_hash", "trust", "confidence_label"):
                if entry.get(metadata_key) is None:
                    entry[metadata_key] = getattr(hit, metadata_key)
            if (
                hit.confidence_label is not None
                and hit.confidence_label.casefold() == "ambiguous"
            ):
                entry["confidence_label"] = "AMBIGUOUS"

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
            content_hash=entry.get("content_hash"),
            trust=entry.get("trust"),
            confidence_label=entry.get("confidence_label"),
        )
        result.append(new_hit)
    return result


def retrieve(
    query: str,
    config: RetrievalConfig,
    dense_backend: RetrievalBackend | None = None,
    kg_backend: RetrievalBackend | None = None,
    reranker: Callable[[str, list[Hit], int], list[Hit]] | None = None,
    vault: VaultConfig | None = None,
) -> list[Hit]:
    """Run the full hybrid retrieval pipeline and return top-N hits.

    Steps:

    1. Apply the word-aware gate. Empty list if the query has fewer
       than ``min_query_words`` meaningful tokens after stopword
       removal, OR its stripped length is below ``min_query_length``.
    2. Normalize the query.
    3. Run FTS5 always. Run dense and KG backends if provided.
    4. Fuse with RRF at the configured ``k``.
    5. Apply the optional reranker, otherwise truncate to
       ``top_n_final``.
    6. When ``vault`` is supplied, emit a RetrievalEvent to the daily
       telemetry JSONL. The call is wrapped in try/except and never
       raises. When ``vault`` is None, telemetry is silently skipped so
       existing call sites with no vault arg are unaffected.
    """
    if valid_scope(config.scope) is None:
        raise ValueError("scope must be a valid concrete identifier or exact '*'")
    stripped = query.strip()
    tokens = [
        t
        for t in stripped.split()
        if t.casefold() not in {s.casefold() for s in config.stopwords}
    ]
    if len(tokens) < config.min_query_words or len(stripped) < config.min_query_length:
        return []

    q_norm = config.normalize(query)
    q_ascii = (
        config.normalize_ascii(query)
        if config.normalize_ascii is not None
        else None
    )

    # --- FTS5 backend (always active) ---
    _t0 = time.perf_counter()
    _per_status: dict[str, BackendTelemetryStatus] = {}
    try:
        fts5_results = fts5_search(
            q_norm,
            config.fts5_db,
            limit=config.top_k_per_backend,
            stopwords=config.stopwords,
            prompt_ascii=q_ascii,
            scope=config.scope,
            _fail_on_error=True,
        )
        _per_status["fts5"] = {
            "attempted": True,
            "succeeded": True,
            "failed": False,
            "contributed": False,
        }
    except Exception:
        fts5_results = []
        _per_status["fts5"] = {
            "attempted": True,
            "succeeded": False,
            "failed": True,
            "contributed": False,
        }
    _fts5_ms = (time.perf_counter() - _t0) * 1000.0

    rankings: list[list[Hit]] = [fts5_results]
    _backend_names: list[str] = ["fts5"]
    _per_hits: dict[str, int] = {"fts5": len(fts5_results)}
    _per_latency: dict[str, float] = {"fts5": _fts5_ms}

    if dense_backend is not None:
        _t1 = time.perf_counter()
        try:
            dense_results = dense_backend(q_norm, config.top_k_per_backend)
            _per_status["dense"] = {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": False,
            }
        except Exception:
            dense_results = []
            _per_status["dense"] = {
                "attempted": True,
                "succeeded": False,
                "failed": True,
                "contributed": False,
            }
        _dense_ms = (time.perf_counter() - _t1) * 1000.0
        rankings.append(dense_results)
        _backend_names.append("dense")
        _per_hits["dense"] = len(dense_results)
        _per_latency["dense"] = _dense_ms

    if kg_backend is not None:
        _t2 = time.perf_counter()
        try:
            kg_results = kg_backend(q_norm, config.top_k_per_backend)
            _per_status["kg"] = {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": False,
            }
        except Exception:
            kg_results = []
            _per_status["kg"] = {
                "attempted": True,
                "succeeded": False,
                "failed": True,
                "contributed": False,
            }
        _kg_ms = (time.perf_counter() - _t2) * 1000.0
        rankings.append(kg_results)
        _backend_names.append("kg")
        _per_hits["kg"] = len(kg_results)
        _per_latency["kg"] = _kg_ms

    fused = rrf_fuse(rankings, k=config.rrf_k)
    contributing_backends = {
        source
        for hit in fused
        for source in (hit.sources if hit.sources else [hit.source])
    }
    for backend, status in _per_status.items():
        status["contributed"] = status["succeeded"] and backend in contributing_backends

    # --- Emit telemetry (non-fatal, only when vault is provided) ---
    if vault is not None:
        try:
            from datetime import UTC, datetime

            from .telemetry import RetrievalEvent, emit_retrieval_event

            event = RetrievalEvent(
                query_hash=q_norm,
                backends=_backend_names,
                per_backend_hits=_per_hits,
                per_backend_latency_ms=_per_latency,
                per_backend_status=_per_status,
                fused_count=len(fused),
                top1_source=fused[0].path if fused else None,
                timestamp_iso=datetime.now(UTC).isoformat(),
            )
            emit_retrieval_event(vault, event)
        except Exception:  # noqa: BLE001
            pass

    if reranker is not None:
        try:
            return reranker(q_norm, fused, config.top_n_final)
        except Exception:
            return fused[: config.top_n_final]
    return fused[: config.top_n_final]


def _now_perf_ms(start_perf_counter: float) -> float:
    return (time.perf_counter() - start_perf_counter) * 1000.0
