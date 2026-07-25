# Changelog

All notable changes to mneme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes yet.

## [3.6.1] - 2026-07-25

### Changed

- The MCP Registry server identity moved to the `io.github.OnourImpram` namespace. The earlier entry, `io.github.TheGoatPsy/mneme`, was claimed under this project's former GitHub handle; once the account was renamed the registry stopped granting permission to update it, so the last version published there was 3.5.0. Install mneme from the new registry entry to receive updates. Installs that go straight to npm (`mneme-mcp-server`) or PyPI are unaffected and need no action.

## [3.6.0] - 2026-07-18

### Added

- `mneme doctor --verify-isolation` runs scope and redaction checks against an isolated temporary fixture without reading or changing the operator vault.
- `mneme_checkpoint_list` and `mneme_working_set_load` accept an optional scope and use the configured default when it is omitted.
- A deterministic release candidate benchmark gate reports retrieval quality, latency, index build time, index size, memory, backend contribution, and ablation results. LongMemEval and LoCoMo adapters validate pinned official schemas.
- Claude, Codex, and Antigravity plugin validators now run with repository integrity and package candidate checks.

### Changed

- All nine MCP schemas are derived from the authoritative Zod contracts and checked against runtime acceptance behavior. Unknown fields remain accepted and discarded for compatibility.
- Concrete scope reads fail closed against legacy unscoped FTS5 indexes and request a rebuild. Exact `scope: "*"` remains the only cross-scope read opt-in. Durable writes reject `*`.
- Turkish FTS5 retrieval preserves distinct `I`, `i`, `U+0130`, and `U+0131` normalization behavior through dual indexed forms.
- Temporal queries apply valid time and transaction time through a deterministic planner. Supersession, contradiction, CCE checkpoints, KG staging, and Graphiti group identifiers are scope isolated.
- Temporal indexing rejects invalid or reversed windows, prunes stale derived claims, takes one deterministic current snapshot, and exposes ambiguity consistently across retrieval paths.
- Retrieval telemetry records attempted, succeeded, failed, and contributed backend states separately. Python and TypeScript query identifiers use separate per-vault keyed HMAC values and backend names are allowlisted.
- RRF deduplicates canonical document paths, retains provenance and confidence fields, and gives the canonical `AMBIGUOUS` label precedence when any contributing backend reports ambiguity.
- The release workflow publishes only from a tag push and consumes the exact wheel, sdist, npm, Claude plugin, and MCP Registry bytes produced by one verified preflight job.
- `tree-sitter>=0.25,<0.26` remains the supported range. Mneme rejects 0.26 before native parsing because the full graph suite reproduces a Windows native access violation with that runtime.

### Fixed

- Concurrent Stop writers on slower Windows filesystems now use the documented five-second session-log lock budget instead of timing out after 0.5 seconds and dropping session blocks. The fail-soft contention test injects a short test-only deadline so the normal Stop latency gate remains independent of the production burst budget.
- Redaction is reapplied before FTS5, telemetry, compression, connectors, sync, KG, Graphiti, migration, and export sinks. Private mapping keys and migration metadata are redacted before hashing, indexing, frontmatter, tags, or audit field paths are produced.
- Python and TypeScript audit writers share a lock, sequence, chained record format, and keyed daily head seal. Cross-language appends advance the same seal, tail truncation is detectable, and partial seal writes restore both snapshots. Rollback refuses to overwrite content whose current hash no longer matches the journaled state.
- Vault and proposal writes fail closed on symlink, reparse point, parent replacement, partial rename, stale lock, and process interruption boundaries.
- Proposal queue writers use a bounded 30-second contention budget with a 60-second stale-lock threshold, preventing record loss during slow durable flushes on Windows.
- Migration rollback canonicalizes stable source aliases for new manifests. Signed legacy aliases without a signed canonical restore target fail closed and preserve the archive for manual hash-verified recovery.
- Capture and audit failures are visible. Stop uses one UTC timestamp source and performs no network or LLM call.
- The console consumes a bounded refused request body before returning `405`, avoiding intermittent Windows connection resets without accepting the request.

### Benchmarks

- The synthetic release candidate gate uses production FTS5 as the regression headline. At seed 42 it reports Recall@10 1.0, Precision@10 0.1, MRR 0.7337, nDCG@10 0.8006, and retrieval p95 3.62 ms on the local Windows host.
- Feature hashing remains explicitly labeled a lexical vector surrogate. Its RRF ablation underperformed production FTS5 and is not presented as semantic or dense retrieval.

### Compatibility

- Markdown remains durable ground truth and is not migrated or rewritten.
- Existing 3.5 vaults remain valid. Run `mneme index rebuild` before concrete scope reads if the existing FTS5 index has no scope metadata.
- Node 22 remains the minimum supported Node version. Python 3.11 through 3.14 remain supported.

## [3.5.0] - 2026-06-29

### Added

- **Multi-scope memory isolation.** A `scope` dimension (e.g. user / project / session / case) now partitions memory so distinct contexts stay isolated. The `documents` table gains a `scope` column (DB schema version 2 → 3, populated on reindex from each note's frontmatter via the fallback chain `scope:` → `project:` → `'default'`). Every read tool (`recall`, `search`, `prime`, `summarize`, `timeline`) accepts an optional `scope` argument and applies a scope predicate; omitting it uses the configured default scope, and the literal `scope: "*"` opts into a cross-scope read. Reads remain backward-compatible against a pre-3.5 index (the predicate is skipped with a one-time warning until the next reindex). Writes (`write`, `propose`) stamp the active scope, and the Graphiti layer filters by scope on read. Configurable via the `MNEME_SCOPE` environment variable or a `default_scope` key in `~/.mneme/config.toml`. See `docs/PRIVACY.md`. **Scope isolation today applies to FTS5 (full-text) retrieval, the default path. Graphiti knowledge-graph results are not yet scope-isolated**: write-side scope stamping in the Python engine is deferred, so until it ships, KG entity and fact results remain visible across scopes regardless of the requested scope. The `scope`/`scope IS NULL` read filter is in place so isolation activates automatically once KG nodes carry a scope.
- **Obsidian knowledge-graph extraction** (`mneme-graph`): deterministic, zero-API extraction of an Obsidian/markdown vault into a typed knowledge graph — wikilinks, tags, embeds, and headings as nodes and edges — with modularity clustering, a content-free query, and a rebuildable graph report.
- **P2 forward-capability design docs** (`docs/design/P2-001…008`): documented seams for a heavier local dense-retrieval adapter (with a default-off stub), richer code-graph PR-impact reports, GitHub-connector hardening, console UX, locale expansion, migration diagnostics, a temporal-blame UI, and CCE compaction-recall diagnostics.

### Security

- **recall type scoping** (S1): `recall` now filters to `frontmatter_type = 'session'`, so it no longer returns arbitrary note types.
- **checkpoint path-traversal guard** (S2): `working_set_load` validates a checkpoint's stored path with `assertWithinVault` (resolve-then-assert) before any filesystem access; out-of-vault paths resolve to not-found.
- **untrusted-output fencing** (S3): checkpoint bullets and Graphiti entity/fact strings returned by `working_set_load`, `summarize`, and `timeline` are redacted and wrapped in the untrusted-memory fence.
- **input size limits** (S4): `write`, `propose`, `search`, and `prime` enforce conservative `.max()` bounds, rejecting oversize input at parse.
- **atomic-write integrity** (S5): a cleanup failure after a committed write is surfaced as an error instead of being silently swallowed, with a Windows EPERM/EBUSY retry/backoff.
- **telemetry hashing** (S6): the telemetry `query_hash` is now a per-vault keyed HMAC-SHA256 rather than a plain (reversible) SHA256.

### Changed

- **Documentation truth-up**: README now reports 9 MCP tools (was 7), 7 benchmark suites (was 5), and the correct ADR count; stale per-package `0.2.0` version qualifiers removed. `docs/MCP.md` documents `mneme_checkpoint_list` and `mneme_working_set_load`.
- **Build / CI**: the Makefile installs, tests, and lints `mneme-code` and lints `mneme-graph` (using the correct `mneme-mcp-server` pnpm filter); CI runs the parity suite; `spec_verify` covers `user_prompt_submit`; `repo_integrity` enforces the nine-tool count.

## [3.2.0] - 2026-06-14

### Added

- **Context Continuity Engine (`mneme_core.cce`)**: opt-in (default off), zero-LLM hot path. Proactive working-set checkpoints at a configurable context-fill threshold (default 65%) or on salient events (explicit keyword, git commit in a Bash response, large tool response >8 KB). Post-compaction loss detection on `SessionStart` loads the latest checkpoint, identifies items the host summary dropped via normalized string search, and re-injects them salience-ranked within a configurable token budget (default 4 000 tokens). Checkpoints are plain markdown in `vault/.mneme/checkpoints/`, indexed by a JSONL sidecar, git-visible and Obsidian-browsable. Seven engine modules: `config.py`, `checkpoint.py`, `budget.py`, `salience.py`, `triggers.py`, `build.py`, `loss_detect.py`.
- **`UserPromptSubmit` hook** (`hooks/user_prompt_submit.py`): evaluates checkpoint triggers on every user prompt; gated behind `CceConfig.enabled` (default off); exits 0 immediately when CCE is not enabled.
- **CCE integration in existing hooks**: `PostToolUse` sets trigger flags on large tool responses and detected git commits; `SessionStart` runs post-compaction loss detection and rehydration after the normal preflight injection; `PreCompact` builds and persists a working-set snapshot unconditionally before the host compacts. All CCE paths are gated behind `CceConfig.enabled` and fail-soft.
- **Two new MCP tools**: `mneme_checkpoint_list` returns the checkpoint JSONL index as structured data; `mneme_working_set_load` re-injects a named checkpoint's items into the session, salience-ranked, within the token budget.
- **Benchmark F** (`benchmarks/compaction_recall/`): synthetic seeded fixture (`MNEME_BENCH_SEED=42`). Baseline recall 0.40 (no CCE) vs self-heal recall 1.00 (CCE enabled); gain +0.60; zero invented facts verified. Regression anchor only — not a real-world quality claim.

## [3.1.0] - 2026-06-12

### Security

- The web console now refuses requests whose Host header does not name
  a loopback alias, closing the DNS-rebinding read path that a bare
  loopback bind leaves open. `--unsafe-expose` disables the check
  together with the bind guard.

### Added

- Team-sync pulls trust-mark every imported markdown file (`source:
  team-sync`, `trust: external`, `payload_sha256`) and redact it on
  arrival; re-pulls are idempotent against the recorded payload hash,
  so local edits no longer risk conflict noise.
- `mneme memory policy init` scaffolds a documented zero-autonomy
  `policy.json` (never overwrites) and `mneme memory policy validate`
  surfaces misspelled class names that the loader drops silently.
- `docs/UPGRADING.md` covers the 2.x to 3.x runtime changes; the
  cookbook gains five recipes for autonomy, team sync, memory blame,
  the web console, and localized deterministic summaries.
- mneme-graph and mneme-code join the release train: 3.x versions,
  `mneme-core>=3.0.0,<4` floors, lockstep sources, preflight gates,
  and first PyPI publishes under the pypi3/pypi4 environments.
- `server.json` is tracked at the repo root (lockstep sources 17 and
  18) and the MCP Registry entry publishes automatically from the
  release workflow via GitHub OIDC.
- Dependabot (npm, pip, actions; weekly, grouped) and CodeQL (python,
  javascript-typescript) workflows.

### Changed

- CI tests Python 3.14 alongside 3.11 to 3.13; all four Python
  packages declare the 3.14 classifier.
- Dev Status classifiers: mneme-core and mneme-cc-plugin move to
  Production/Stable, mneme-graph and mneme-code to Beta.
- Governance documents the interim single-maintainer release rule;
  the security policy states its best-effort response posture.

## [3.0.1] - 2026-06-12

### Fixed

- Restore the `mcpName` field in the npm package manifest. The 3.0.0 release
  tree was snapshotted from the development line, which never carried the
  2.0.1/2.0.2 public-main-only commits that introduced `mcpName`; npm
  metadata is immutable, so the MCP Registry entry required this patch
  release. `tools/repo_integrity.py` now guards the field.
- Correct stale "six tools" claims to seven (`mneme_propose` shipped in
  3.0.0) across the npm package description, the MCP/codex/antigravity
  plugin READMEs, `docs/MCP.md`, and `docs/INTEGRATIONS.md`. The integrity
  gate now rejects any tracked six-tools claim.

### Changed

- Node engines floor raised from `>=20` to `>=22`. Node 20 reached
  end-of-life in April 2026, better-sqlite3 12.x ships no Node 20 Windows
  prebuild, and the CI matrix now tests Node 22 and 24.
- CI runs on Node 24 action runtimes (checkout v6, setup-node v6,
  setup-python v6, pnpm-setup v6, artifact v7/v8, gh-release v3) ahead of
  the June 16 forced-runtime cutover, with pnpm resolved from the
  `packageManager` pin.
- The README status line is now the fourteenth lockstep version source
  (`semver-prose` flavor in `tools/version_bump.py`), so the front-page
  version claim can no longer drift.

## [3.0.0] - 2026-06-12

The next-level release: the remaining capability-matrix gaps close on mneme's
own local-first terms, and the project relicenses to Apache-2.0.

### Changed

- **License: Apache-2.0** from this release onward (`LICENSE` + new `NOTICE`,
  ADR-015). Published 1.x and 2.x artifacts permanently remain MIT.
- **Stop-hook session log entries now carry a deterministic extractive summary
  by default** (files touched, tool activity, opening intent) computed from
  already-redacted staging records — zero LLM, zero network, zero key. Disable
  or localize via `.mneme/summary.json`; the editable placeholder returns when
  disabled. The opt-in LLM compression pipeline is unchanged.
- **Temporal claim lifecycle de-gated**: valid-from/to, supersedes, as-of, and
  contradiction queries are built in on every profile (pure SQLite). Graphiti
  export and LLM claim extraction stay gated and off the critical path.

### Added

- **`temporal blame` + `temporal contradictions` CLI** — provenance
  time-travel: where a claim came from, what it supersedes, what superseded
  it, and its same-key rivals, with cycle-safe lineage walks.
- **Policy-graduated autonomous memory edits**: `.mneme/policy.json` declares
  which low-risk edit classes (dedup-merge, typo-fix, tag-normalize,
  supersede-link, stale-archive) the agent may apply autonomously. Every edit
  is journalled for `memory rollback <id>`, recorded in a tamper-evident HMAC
  audit chain shared byte-compatibly between the Python and TypeScript
  writers, and durable categories (identity, preference, clinical, legal,
  financial) always require human approval. New CLI:
  `memory policy|changes|rollback|drain`.
- **`mneme_propose` MCP tool (seventh tool)**: agents queue redacted edit
  proposals — the server never applies them directly; the SessionEnd hook
  drains the queue deterministically under the operator's policy.
- **Localized presets**: Turkish compression rubric (`compress-tr.md`) and
  localized deterministic-summary templates (`summary-en.md`,
  `summary-tr.md`), selected via the `language` knob in `compression.json` /
  `summary.json` with English fallback.
- **`mneme-graph impact` (PR-impact)**: file-seeded reverse-BFS over the code
  graph with `--diff` git integration; external ghost nodes resolve onto
  their unambiguous local definitions at query time. Self-verification test:
  the package analyses its own source.
- **Branch-aware failure notes**: `mneme-code parse-trace` records the
  vault's git branch (metadata-only; identity hashes unchanged) with
  `--branch` / `--no-branch` overrides.
- **`mneme-console --serve`**: loopback-only, GET-only, dependency-free web
  console — interactive explorer for the vault audit, code graph, temporal
  claims (supersedes chains), autonomous-edit journal, and audit-chain
  verification. Non-loopback binds refused without `--unsafe-expose`.
- **`mneme-core sync` (self-hosted team memory)**: share the vault over any
  plain git remote with redaction-before-share (a surviving `<private>` span
  aborts the push), optional `age` end-to-end encryption, per-member share
  trees, and a never-overwrite `.conflict`-sidecar merge policy. New CLI:
  `sync status|push|pull`.

### Verified

- Benchmark anchors re-verified post-3.0 (seed 42): Benchmark A
  nDCG@5 = 0.893, Recall@10 = 1.0; Benchmark B Stop p95 = 2.0 ms — the new
  surfaces stay off the Stop hot path.

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
