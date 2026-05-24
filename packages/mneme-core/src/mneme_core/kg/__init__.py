"""Temporal knowledge-graph adapter.

The KG layer is the third leg of mneme's hybrid retrieval (FTS5 +
LEANN + Graphiti). It ships in the ``full`` install profile and is a
no-op for the ``lite`` and ``standard`` profiles: every entrypoint
checks ``VaultConfig.kg_active_flag`` and returns silently if it is
not present.

Public surface:

- ``KgConfig``: bundles vault-derived paths and operator-tunable knobs
  (cost cap, host identifier, capture tool whitelist).
- ``stage_event(event, config)``: called from the PostToolUse hook.
  Non-blocking; writes a JSONL record into the per-host queue.
- ``mark_community_refresh(config)``: called from the Stop hook.
  Writes a flag the next worker drain consumes.
- ``drain_dry_run(config)`` / ``drain_live(config)`` /
  ``community_refresh(config)``: implementations of the ``mneme kg``
  CLI subcommands. ``drain_live`` lazily imports ``graphiti_core``,
  so importing this package does not require the heavy dep.

The split mirrors the FTS5 architecture: build-time work (staging,
worker, flush) is Python and lives here; runtime read-only work (BFS
expansion, bi-temporal timeline) is TypeScript in ``mneme-mcp`` and
talks to Neo4j directly via the ``neo4j-driver`` npm package. No
Python<->TS bridge.
"""

from .episode_stage import KgConfig, is_active, kg_config_from_vault, stage_event
from .flush import mark_community_refresh
from .worker import community_refresh, drain_dry_run, drain_live

__all__ = [
    "KgConfig",
    "stage_event",
    "is_active",
    "kg_config_from_vault",
    "mark_community_refresh",
    "drain_dry_run",
    "drain_live",
    "community_refresh",
]
