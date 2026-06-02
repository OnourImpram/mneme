"""Minimal local temporal claim lifecycle layer for mneme-core.

Design constraints
------------------
* **Derived / rebuildable**: the ``claims`` table is built from vault markdown
  frontmatter and can be dropped and rebuilt at any time.  Markdown files are
  ground truth; the table is never the source of truth.
* **No LLM, no network**: all logic is pure SQLite + frontmatter parsing.
  Everything is deterministic.
* **Deferred** (do NOT build yet):
  - LLM claim extraction
  - graphiti / Neo4j integration
  - dense embeddings
  - TS / MCP wiring of the temporal leg into ``mneme_search``
    ``available_backends`` (documented follow-up seam)

Retrieval seam
--------------
:func:`make_temporal_backend` returns a :class:`~mneme_core.retrieval.rrf.RetrievalBackend`-
compatible callable that fills the existing planner ``kg`` slot with a
native fact-node backend.  When the ``claims`` table is absent or empty the
callable returns ``[]``, so ``retrieve()`` degrades cleanly to the FTS5-only
path with no configuration required.

Public API
----------
.. autofunction:: mneme_core.temporal.claim.claim_from_note
.. autoclass:: mneme_core.temporal.claim.Claim
.. autoclass:: mneme_core.temporal.claim.ConfidenceLabel
.. autofunction:: mneme_core.temporal.index.ensure_temporal_schema
.. autofunction:: mneme_core.temporal.index.index_claims
.. autofunction:: mneme_core.temporal.query.as_of
.. autofunction:: mneme_core.temporal.query.current
.. autofunction:: mneme_core.temporal.query.find_contradictions
.. autofunction:: mneme_core.temporal.backend.make_temporal_backend
.. autofunction:: mneme_core.temporal.backend.ambiguous_claim_ids
"""

from __future__ import annotations

from .backend import ambiguous_claim_ids, make_temporal_backend
from .claim import Claim, ConfidenceLabel, claim_from_note
from .index import ClaimIndexStats, ensure_temporal_schema, index_claims
from .query import as_of, current, find_contradictions

__all__ = [
    "Claim",
    "ClaimIndexStats",
    "ConfidenceLabel",
    "ambiguous_claim_ids",
    "as_of",
    "claim_from_note",
    "current",
    "ensure_temporal_schema",
    "find_contradictions",
    "index_claims",
    "make_temporal_backend",
]
