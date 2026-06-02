# Changelog

All notable changes to mneme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes yet.

## [2.0.0] - 2026-06-02

Major release: the Full and Power profile advanced capabilities. Every module is
local-first and gated, ships with redaction-before-store, provenance, and confidence
labels, and never runs on the Stop or critical path.

### Added

- **Graph analytics + multi-language extraction (`mneme-graph` 0.2.0).** Community
  detection, PR-impact analysis, and content-hash-discriminated ghost-duplicate
  detection over the project graph; JavaScript and TypeScript extraction via
  tree-sitter behind an extractor registry (`extract_any`).
- **Code memory completion (`mneme-code` 0.2.0).** AGENTS.md procedural-memory
  parsing, pytest/unittest output to failure memories, and a fix modelled as a
  temporal claim that supersedes the failure.
- **Vault-config domain modes (`mneme_core.modes`, `mneme-modes` CLI).** User modes
  loaded from a vault config; user config can never weaken a built-in privacy mode
  or disable redaction.
- **Agent security (`mneme_core.capability` / `taint` / `approval` / `security_bench`).**
  A capability firewall (retrieved or tainted content gets only non-mutating
  capabilities), data-flow taint tracking, a human-approval gate for durable memory
  edits, and a poisoned-vault benchmark with an Agent Security Bench adapter.
- **Read-only console (`mneme_core.console`, `mneme-console`).** A self-contained,
  offline, injection-safe HTML audit report. No server, no network.
- **Dense retrieval (`mneme_core.retrieval.dense`).** A local-first
  hashing-embedding backend fused with FTS5 via RRF on a shared document id;
  sentence-transformers is an opt-in seam, never a default dependency.
- **Temporal extraction + Graphiti export (`mneme_core.temporal.extract` /
  `graphiti_export`).** Rule-based inferred claim extraction with an optional LLM
  seam, and a Graphiti episode bridge.
- **Network connectors (`mneme_core.connectors_net`).** Obsidian (local) and GitHub
  (injected transport) external sources, default off, redaction-before-ingest,
  revocation by disabling.
- **Benchmark harness (`mneme_core.bench.harness`).** LongMemEval and LoCoMo dataset
  adapters, a system-versus-system runner over recall/MRR/nDCG, and a head-to-head
  comparator.

### Notes

- No head-to-head superiority claim is published; the harness measures, and the
  operator runs and publishes the benchmark. All external or opt-in surfaces (dense
  embeddings, LLM extraction, Graphiti, network connectors) are off by default and
  never touch the Stop or critical path.

## [1.2.0] - 2026-06-02

### Added

- **Temporal claim lifecycle (`mneme_core.temporal`).** A local, derived,
  rebuildable SQLite claims index parsed from markdown frontmatter
  (`valid_from`/`valid_to`/`observed_at`/`supersedes`/`claim_key`). Point-in-time
  `as_of(t)` queries (inclusive-from, exclusive-to) with dynamic non-destructive
  supersession, contradiction detection, an `AMBIGUOUS` query-time overlay, a
  `RetrievalBackend`-compatible temporal leg (clean FTS5 fallback), and a
  `mneme temporal index/as-of/current` CLI. New `claim` memory type. No LLM, no
  network; redaction before every store; all datetimes normalized to UTC.
- **`mneme-code` package.** Deterministic Python traceback parsing
  (`parse_traceback`), redacted failure memories (`failure_from_traceback` /
  `failure_to_markdown` with provenance + confidence), frame-to-graph
  resolution, and a `mneme-code parse-trace` CLI. New `failure` memory type.
- **Domain mode packs (`mneme_core.modes`).** Named policy bundles
  (language + ontology + write/retrieval/privacy policy): `code`, `research`,
  `clinical-research`, `security-review`. Privacy enforcement: clinical-research
  and security-review block external extraction and artifact upload by default;
  unknown modes deny.
- **Defensive security scanner (`mneme_core.security`).** Detects secret-like
  material and prompt-injection phrasing in the vault; findings never echo raw
  secrets (masked / redacted). Includes a poisoned-vault test.
- **Read-only audit aggregator (`mneme_core.audit`).** Vault note-type counts
  plus a security summary; the v1 console surface (browser UI deferred).
- **Opt-in connector framework (`mneme_core.connectors`).** A `Connector`
  protocol with redaction-before-ingest and provenance (`trust='external'`),
  disabled by default; bundled `LocalMarkdownConnector` reference (no network).

### Changed

- **`mneme-graph` completed and CI-gated.** `inherits` / `calls` / `variable`
  extraction (`calls` is the first `INFERRED`-confidence producer); a
  `mneme-graph build/report` CLI; node ids fold `line_start` for local nodes so
  same-named symbols in one file stay distinct (externals remain
  line-independent for cross-file dedup); ruff + mypy --strict + an 80% coverage
  gate now run in CI.
- **Retrieval fidelity.** TS/Python telemetry shape parity, a dense-seam RRF
  integration test, and an official LongMemEval `--dataset-path` runner.

### Fixed

- **Provenance integrity.** `content_hash` now attests to the redacted stored
  content rather than the raw bytes (the indexer previously hashed pre-redaction
  bytes while storing the redacted form).

## [1.1.0] - 2026-05-29

### Added

- **Antigravity native client.** A new `mneme-antigravity-plugin` package ships
  a Gemini-CLI extension (`gemini-extension.json` declaring the mneme MCP
  server, a Claude-Code-compatible `hooks/hooks.json` for SessionStart,
  PostToolUse, Stop, and PreCompact, two skills, and a `GEMINI.md` rules file).
  `mneme install --client antigravity` materializes it into the Antigravity
  extensions directory. Claude Code, Codex, and Antigravity are now all
  first-class native clients reusing the same MCP server and the
  `mneme hook <event>` shim. A `validate_antigravity_plugin` gate runs in CI.
- **Open model-agnostic MCP adapter.** `mneme install --client mcp --config
  <path>` merges the mneme MCP stanza into any MCP-capable client's config
  (Kimi, Qwen, Cline, Cursor, and others), preserving all other servers. This
  is the non-native tier: MCP tools only, no lifecycle hooks, no auto-capture.
  See `docs/INTEGRATIONS.md` and `examples/`.

### Fixed

- **Retrieval correctness.** A full-pass index prune clears all rows when every
  file is excluded, so a fully-excluded vault no longer leaves stale index
  entries. `benchmark_queries` uses the production OR-of-phrases query builder
  so benchmark numbers reflect the retrieval path actually executed.
- **Deterministic indexing.** The indexer now sorts the `*.md` walk before
  assigning document rowids. `rglob` yields directory order, which differs
  across filesystems (ext4 vs NTFS); because FTS5 breaks equal-BM25 ties by
  rowid, the unsorted walk made ranking — and the retrieval benchmark's nDCG —
  depend on the host filesystem. Sorting makes indexing reproducible
  everywhere and keeps the locked benchmark baseline stable across runners.
- **Vault-escape containment (security).** The indexer resolves each `*.md`
  file's realpath and skips any whose target escapes the vault root. `rglob`
  follows symlinks and the exclusion check is purely lexical, so a symlink
  planted inside the vault that points outside it (for example
  `vault/private.md` → `~/.ssh/id_rsa`) would otherwise be read and stored in
  the FTS5 index, leaking out-of-vault file contents through `mneme_search`
  and `mneme_summarize`. This mirrors the existing TypeScript write-path
  containment guard.
- **Durability and atomicity.** `reserve_cost` writes the cost ledger through
  the same fsync-and-rename atomic path as settlement and rollback; the
  injection-dedup tracker and the Codex config are written atomically; staging
  events are written LF-only so the rolling size counter matches on-disk bytes
  on every OS.
- **Resilience.** The `doctor` frontmatter-date check, trajectory loading, and
  pattern loading parse frontmatter through the date-safe loader, so a single
  out-of-range date in one file no longer aborts the whole walk or listing. The
  compression pipeline's cap check is guarded against a corrupt ledger and
  returns a structured report instead of raising. Payload truncation is
  byte-accurate. The knowledge-graph drain loop archives per file and survives a
  cross-device move.
- **Python and TypeScript parity.** The MCP write tool uses the canonical
  case-insensitive, attribute-tolerant, fail-closed `<private>` redactor;
  `mneme_prime` snippets are built from the frontmatter-stripped body; the MCP
  vault-config reader accepts single-quoted TOML paths; `redact(None)` returns
  an empty string. The write tool rejects section bodies containing a bare H2
  heading and emits exactly one blank line between appended sections.
- **Fail-soft hooks.** The Stop hook emits its response even when the
  empty-session state touch fails; SessionStart opens the FTS5 index read-only.
- **Metadata drift.** `CITATION.cff` and the Antigravity manifest are tracked
  by `version_bump`, raising the cross-checked version-source count to 13. The
  README banner no longer names a single drift-prone version string.

### Changed

- The C3 no-network import scan (`spec_verify`) now also covers the
  `session_end` and `post_tool_use` hooks, which run on the live session path.
- `mneme-cc-plugin` pytest enforces an 80 percent coverage floor, matching
  `mneme-core`.

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
