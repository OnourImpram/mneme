# P2-001 — Stronger Local Dense Retrieval Adapter Seam

## Capability

Allow the dense retrieval backend to accept a heavier embedding model
(ONNX, sentence-transformers, etc.) without changing the default
hashing-based behavior or any calling code.  Ship a minimal stub that
formalizes the adapter contract and provides a feature-flagged factory.

## Current State

`packages/mneme-core/src/mneme_core/retrieval/dense.py` already ships the
seam informally:

- `EmbedFn = Callable[[str], tuple[float, ...]]` (line 62) — the type alias
  that every backend must satisfy.
- `DenseBackend.__init__` (lines 385-389) accepts `embed_fn: EmbedFn`,
  so any callable can be injected.
- `build_dense_index` (lines 289-357) likewise accepts `embed_fn: EmbedFn`.
- The default is `hashing_embed` (lines 70-106), a deterministic
  feature-hashing bag-of-words embedder with no model or network.

What is missing is a formal `Protocol` that documents the full contract
(name, dimension, callable), and a factory that reads a feature flag so
operators can opt in without touching source code.

## Stub (shipped in this phase)

New file: `packages/mneme-core/src/mneme_core/retrieval/adapter.py`

```
DenseAdapter          — @runtime_checkable Protocol
                        .name: str, .dim: int, .embed_fn: EmbedFn
HashingAdapter        — default impl wrapping hashing_embed, dim=256
load_dense_adapter()  — reads MNEME_DENSE_ADAPTER env var;
                        returns HashingAdapter when unset (default OFF)
```

Feature flag: env var `MNEME_DENSE_ADAPTER`.  Default = unset / empty →
`HashingAdapter` → identical to pre-stub behavior.  A heavier adapter is
wired by setting `MNEME_DENSE_ADAPTER=dotted.module.ClassName`; the factory
imports and instantiates it, falling back to `HashingAdapter` on any error.

Integration point (when a caller upgrades to use the factory):

```python
# In retrieve() or a hook script:
adapter = load_dense_adapter()
backend = DenseBackend(index, embed_fn=adapter.embed_fn)
```

`retrieve()` in `rrf.py` (lines 300-408) already accepts `dense_backend:
RetrievalBackend | None`; passing a `DenseBackend` built from a heavier
adapter requires no change to that function.

## Proposed Design for Full Adapter (deferred, not built here)

1. Implement `DenseAdapter` in a separate optional-dep package, e.g.
   `mneme-dense-onnx` or `mneme-dense-st`.
2. Provide `build_dense_index(db_path, embed_fn=adapter.embed_fn, dim=adapter.dim)`.
3. Re-index when switching adapters (vectors are not portable across dims).
4. Persist the adapter name in `index_meta` (alongside `normalization_profile`)
   so a query-side mismatch is caught at search time, mirroring the locale
   guard already in `mneme-mcp/src/tools/search.ts` (lines 258-281).

## Feature-Flag / Rollout Plan

| Stage | Condition | Behavior |
|-------|-----------|----------|
| Default (this PR) | `MNEME_DENSE_ADAPTER` unset | `HashingAdapter`; no change |
| Opt-in | Env var set to a valid class | Heavy adapter loaded |
| Error | Env var set, class not found | Warning to stderr; fall back to `HashingAdapter` |
| Future | Stable adapter + index-meta guard | Enable per-vault in config |
