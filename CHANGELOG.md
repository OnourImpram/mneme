# Changelog

All notable changes to mneme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes yet.

## [1.0.3] - 2026-05-25

### Added

- `mneme doctor` reports vault and index health: whether the vault resolves,
  whether the FTS5 index exists and is current with the indexer schema version,
  the indexed document count and freshness, and whether a compression config is
  present. Exits non-zero only when a check fails.
- The FTS5 schema migration runner is now version-driven: it derives the
  expected columns from a canonical map and adds any missing ones in place, so
  future columns migrate without a full rebuild.
- A pre-registered retrieval evaluation protocol (`benchmarks/retrieval/PROTOCOL.md`).
  The harness now reports Recall@10 and runs a negative-query probe (queries with
  no relevant document must return nothing) alongside nDCG@5, and the regression
  guard enforces all three. Relevance judgments are fixed before the system runs,
  to avoid circularity.
- A shared Turkish-locale golden-vector fixture that validates the Python and
  TypeScript casefold against the same cases, so the two cannot drift.
- A retrieval-seam test proving injected dense and knowledge-graph backends fuse
  with FTS5 through RRF. No default dense backend is wired; dense retrieval stays
  roadmap, gated on the evaluation harness.
- A Neo4j service-container CI job and a gated integration test that exercise the
  full-profile knowledge-graph connection contract against a real database. The
  test skips cleanly wherever the service and the `neo4j` driver are absent.

### Security

- Vault content that mneme re-injects into model context is now fenced as
  untrusted data with an explicit "treat as data, not instructions" notice, and
  the fence sentinel is neutralized inside that content so a crafted note cannot
  break out of the fence (the spotlighting/delimiting mitigation). The fence
  wraps the SessionStart preamble, the `mneme_prime` bundle, and `mneme_recall`
  bodies; `mneme_search`, `mneme_summarize`, and `mneme_timeline` neutralize the
  titles and snippets they surface. A shared Python/TypeScript conformance
  fixture keeps both implementations aligned. This mitigates, and does not
  eliminate, prompt injection from untrusted notes; it layers with `<private>`
  redaction and the read-path vault containment checks.

## [1.0.2] - 2026-05-25

### Fixed

- Default capture loop now produces searchable memory. The Stop hook writes the
  daily session log with `type: session` frontmatter, so the indexer records
  `frontmatter_type='session'` and SessionStart surfaces recent sessions.
  SessionStart's today-headings block now reads from the `sessions/` directory
  the Stop hook writes to. Previously the log carried no frontmatter type and
  the recent-sessions block was always empty on a default install.
- Deleted vault files are pruned from the FTS5 index on a full reindex, so
  removed notes no longer remain searchable (privacy and recall correctness).
- Consolidated privacy redaction into a single `mneme_core.privacy.redact` used
  by every writer (staging, knowledge-graph staging, telemetry, patterns,
  trajectories), with a matching TypeScript implementation validated against a
  shared conformance fixture. Redaction is now case-insensitive,
  attribute-tolerant (`<private reason="...">`), and fail-closed.
- The installer wires hooks through the `mneme hook <event>` console script
  (with an absolute-interpreter fallback), so hooks work under a pipx isolated
  venv instead of a bare `python3 -m` the system interpreter cannot import.
- Hook timeouts written into `settings.json` are now seconds, matching the
  Claude Code hook schema and the native plugin manifest. They were
  milliseconds, which the schema read as 1000-2000 second timeouts that could
  hang the editor on a wedged hook. A test keeps the installer and manifest in
  sync, and the ceilings now sit above each hook's internal deadlines.
- Resolved the duplicate `mneme` console script. Only `mneme-cc-plugin`
  publishes `mneme`; `mneme-core` publishes `mneme-core`. A co-install no longer
  resolves to whichever package pip wrote last.
- The FTS5 query builder splits hyphenated and reserved-character identifiers
  into a phrase (`claude-mem` becomes `"claude mem"`) instead of fusing them
  into an unmatchable token. The Python and TypeScript builders now behave
  identically.
- Closed a SQLite connection leak in `mneme_core.retrieval.rrf.fts5_search` on
  the query error paths.
- The compression cost ledger now fails closed on a corrupt ledger file instead
  of resetting computed spend to zero and bypassing the cost cap.
- Pattern and trajectory writes are serialized under a file lock, closing a
  concurrent read-modify-write lost-update window.
- MCP search snippets are built from the document body, never the raw
  frontmatter, so YAML metadata is not returned to callers. A `body_text`
  column is added to pre-existing vault databases in place.
- Implemented `MNEME_SKIP_HOOKS` selective hook bypass (documented but
  previously unimplemented).
- The MCP server declares `neo4j-driver` as an optional dependency and
  lazy-loads it, so lite and standard installs no longer pull the driver.
- Replaced an unsafe tool-error cast in the MCP dispatcher with a runtime type
  guard.

### Added

- `CITATION.cff` for citable-software metadata.

## [1.0.1] - 2026-05-24

### Fixed

- Aligned public GitHub release state across README, package docs, plugin
  manifests, runtime constants, and release-integrity validators.
- Made the Codex plugin manifest pass the repo-local plugin validator by moving
  rejected hook wiring out of `.codex-plugin/plugin.json` and keeping MCP
  wiring in the supported `.mcp.json` shape.
- Separated the user-facing plugin CLI from the core vault-operations CLI by
  documenting `mneme` for install and hooks, adding `mneme-core` for core
  commands, and enabling `python -m mneme_core`.
- Made `mneme install --dry-run` truly non-mutating. It now reports planned
  vault initialization without creating `.mneme/config.toml`.
- Added `mneme install --upgrade-profile=...` as a compatibility alias while
  documenting `mneme upgrade --profile=...` as the canonical upgrade command.
- Added `py.typed` markers so downstream strict type checking can analyze
  `mneme-core` and `mneme-cc-plugin`.
- Aligned MCP error-code documentation with the implementation and made
  `mneme-mcp --version` work without resolving a vault.

### CI

- Added `mneme-cc-plugin` ruff, mypy strict, and pytest gates to CI.
- Added repo-local Codex plugin validation and release-integrity checks to CI
  and release preflight.
- Expanded `tools/version_bump.py` to cover plugin manifests and marketplace
  release metadata.

## [1.0.0] - 2026-05-24

Initial public release. mneme is a vault-native memory system for
Claude Code: markdown is the single source of truth, indexes are derived and
rebuildable.

### Added

- Vault contract: markdown documents with a typed frontmatter schema, atomic
  writes with vault-root containment, and a path-traversal guard. The entire
  memory state is reconstructible from the vault directory alone.
- Turkish-aware FTS5 retrieval: pure-Python locale casefold (correct dotted and
  dotless `i` handling) with no native dependency, plus a build-time indexer.
- Hybrid retrieval: FTS5 BM25, optional LEANN dense embeddings, and an optional
  Graphiti bi-temporal knowledge graph, fused with Reciprocal Rank Fusion
  (k=60).
- Zero-LLM Stop hook: session-end capture is a deterministic markdown append.
  No LLM call, no network dependency on the critical path.
- Privacy redaction: `<private>...</private>` content is stripped before it can
  reach staging, telemetry, the knowledge graph, the FTS5 index, or the vault,
  with a SHA256 audit entry per redaction.
- Adaptive Context Layer (token efficiency): shell-output compression,
  per-session injection dedup, context-budget-aware top-k, and full / keypoints
  / ref injection formats. Enabled by default.
- Background AI compression pipeline: opt-in, off by default, with a monthly
  cost cap enforced by a lock-backed reservation ledger.
- Six MCP tools (`mneme_search`, `mneme_recall`, `mneme_write`, `mneme_prime`,
  `mneme_summarize`, `mneme_timeline`) over a stdio server, with a structured
  error envelope.
- Claude Code plugin: five hooks (PostToolUse, SessionStart, Stop, PreCompact,
  SessionEnd), three slash commands, and two skills, with BOM-safe settings
  mutation and a three-tier installer (lite / standard / full profiles).
- Migration tool (`mneme-migrate`): one-command import from claude-mem into the
  vault, idempotent on re-run via content-hash dedup, with a tri-state
  `--archive` flag (preserve / copy / move behind a two-factor confirm).
- Reproducible benchmark suite: retrieval quality, latency, token cost,
  migration validation, and head-to-head comparison, runnable via `make
  bench-all` with a pinned seed.

### License

- MIT.
