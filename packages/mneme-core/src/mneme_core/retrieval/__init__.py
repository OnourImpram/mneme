"""Hybrid retrieval pipeline with Reciprocal Rank Fusion.

mneme retrieval fuses results from multiple search backends (FTS5
BM25, optional dense vector, optional temporal knowledge graph) into
a single ranked list. Fusion is performed with RRF at ``k=60``, the
default established by Cormack et al. 2009.

Public surface:

- ``Hit``: typed search result with score, path, title, and source
  metadata.
- ``RetrievalConfig``: paths, top-k limits, RRF k, query gate
  threshold, and an injectable normalizer.
- ``RetrievalBackend``: Protocol for plugging in dense or KG search
  legs. The built-in FTS5 backend is exposed as ``fts5_search``.
- ``retrieve``: orchestrator that runs enabled backends, applies RRF,
  and returns the top-N.
"""

from mneme_core.retrieval.planner import (
    RetrievalConfig as PlannerConfig,
)
from mneme_core.retrieval.planner import (
    plan_retrieval,
)
from mneme_core.retrieval.rrf import (
    DEFAULT_RRF_K,
    Hit,
    RetrievalBackend,
    RetrievalConfig,
    RetrievalIndexStaleError,
    build_fts5_query,
    fts5_search,
    retrieve,
    rrf_fuse,
)
from mneme_core.retrieval.telemetry import (
    RetrievalEvent,
    emit_retrieval_event,
)

__all__ = [
    "DEFAULT_RRF_K",
    "Hit",
    "PlannerConfig",
    "RetrievalBackend",
    "RetrievalConfig",
    "RetrievalEvent",
    "RetrievalIndexStaleError",
    "build_fts5_query",
    "emit_retrieval_event",
    "fts5_search",
    "plan_retrieval",
    "retrieve",
    "rrf_fuse",
]
