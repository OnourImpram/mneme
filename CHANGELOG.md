# Changelog

All notable changes to mneme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes yet.

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
