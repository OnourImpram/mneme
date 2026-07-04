# mneme-graph

Local code knowledge graph for mneme. Extracts GraphNode/GraphEdge from Python
source files via tree-sitter. Derived and rebuildable — the ground truth is
always the source files; `graph.json` is a derived artifact.

Part of the [mneme](https://github.com/OnourImpram/mneme) memory engine.

## Scope (v1)

This package is deliberately small and honest about its limits.

- **Python only.** Extraction uses tree-sitter for Python source. TypeScript,
  JavaScript, Rust, and other languages are not yet supported.
- **`calls` resolution is heuristic.** A call edge resolves to a local
  function/method by unqualified name within the same vault, with `INFERRED`
  confidence. There is no cross-file binding or precise symbol resolution; a
  call with no local name match points at an `<external>` node (`EXTRACTED`).
- **Derived, never source of truth.** `graph.json` is rebuilt from source on
  every `build`; the source files remain the ground truth.

### Deferred (not implemented yet)

- Community detection / clustering.
- Pull-request impact analysis.
- Entity canonicalization and a merge queue (to avoid ghost-duplicate nodes
  across renames and aliases).
- Multi-language extraction.

These are roadmap items, not present capabilities.
