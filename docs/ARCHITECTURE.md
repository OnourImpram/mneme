# Architecture

This document captures the design philosophy and the Architecture Decision Records (ADRs) that shape mneme.

## Design Philosophy

mneme is built on three convictions:

1. **Markdown is ground truth.** All durable memory lives as plain markdown files in a user-owned directory called the vault. Shipped indexes such as FTS5, and gated or roadmap artifacts such as knowledge graph and dense indexes, are derived artifacts that can be rebuilt at any time. The user owns the data, not the tool.

   **Vault independence.** A vault is a plain directory of markdown files — nothing more. mneme requires no specific editor, no external application, and no Obsidian installation. The term "vault" is borrowed convention, not an Obsidian dependency; the same directory works equally well with `grep`, `git`, VS Code, or any text editor. Obsidian is fully optional: users who already use it can point it at the vault directory and get rendered notes, backlinks, and graph-view navigation over the wikilinks mneme writes, but Obsidian is a viewer and navigator, not part of mneme's capture, indexing, or retrieval path. The two tools are designed to coexist without interference: mneme stores all derived state in a `.mneme` directory that Obsidian ignores as a dot folder, and mneme's indexer excludes the `.obsidian` settings folder from indexing. Obsidian is not a system requirement and must not appear as an installation prerequisite.

2. **Determinism on the critical path.** The Stop hook must finish in under one second p95 and must never depend on a network call or an LLM API. Compression and AI processing happen in the background, opt-in, with cost caps.

3. **Hybrid-ready retrieval beats single-leg retrieval.** No one signal is sufficient. mneme ships BM25 lexical recall and an RRF-ready protocol at `k=60`. Full-profile KG enrichment is gated. Packaged dense retrieval is roadmap.

## Component Map

```
                       Claude Code session
                                |
                                v
                   +------------------------+
                   |  mneme-cc-plugin       |
                   |  (5 hooks, 3 commands) |
                   +------------+-----------+
                                |
                                v
                   +------------+-----------+
                   |  mneme-mcp (TS stdio)  |
                   |  (6 mneme_* tools)     |
                   +------------+-----------+
                                |
                                v
                   +------------+-----------+
                   |  mneme-core (Python)   |
                   |  retrieval pipeline    |
                   +------+----+-----+------+
                          |    |     |
                          v    v     v
                   +-------+ +---------+ +-----------+
                   | FTS5  | | Dense   | | Graphiti  |
                   | BM25  | | roadmap | | Neo4j KG  |
                   +---+---+ +----+----+ +-----+-----+
                       \      |       /
                        \     v      /
                         \  RRF k=60/
                          v
                  +--------+--------+
                  |  Vault (.md)    |
                  |  user-owned     |
                  +-----------------+
```

The diagram above is the Claude Code path, mneme's native origin. Everything from `mneme-mcp` down is client-neutral. Codex drives the same `mneme-mcp` core and the same `mneme hook` entry through the Codex plugin (`packages/mneme-codex-plugin`). See ADR-014 and `docs/CODEX.md`.

## Architecture Decision Records

Each ADR has the form: Context, Decision, Consequences. Decisions live in the codebase and on disk, not in a wiki.

### ADR-001: Markdown vault, not SQLite blob

**Context**: Competing tools (claude-mem, supermemory) store memory in opaque binary formats (SQLite, cloud).

**Decision**: mneme stores all durable memory as plain markdown files with YAML frontmatter, in a user-defined directory tree.

**Consequences**: Users can `git diff`, `grep`, back up, and edit memory directly. Indexes are derived and can be rebuilt. Trade-off: index rebuild cost on first install or schema migration.

### ADR-002: Zero LLM call on Stop hook

**Context**: claude-mem and similar tools call an LLM at Stop with a 120s timeout. This is slow, costly, and a critical path dependency.

**Decision**: mneme's Stop hook appends a deterministic markdown record in under one second p95. Compression is async, opt-in, and bounded by a cost cap.

**Consequences**: Stop is instant, but raw session output is verbose until compression catches up. Mitigated by `distill.shell_compress` reducing raw size 60 to 90 percent at capture time.

### ADR-003: RRF fusion at k=60

**Context**: Single-backend retrieval leaves recall on the table, but v1.0 must not advertise dense retrieval before the packaged adapter is present.

**Decision**: Run all available backends in parallel, fuse with Reciprocal Rank Fusion `score = sum(1 / (k + rank_i))` at `k=60`. v1.0 ships FTS5 and the injectable backend protocol. Benchmark A uses a deterministic BoW surrogate to guard the fusion path.

**Consequences**: The fusion path is tested before the packaged dense adapter lands. Trade-off: public docs must label dense retrieval as roadmap until the real adapter ships.

### ADR-004: Three-tier install profile

**Context**: Graphiti requires Docker and Neo4j. Dense retrieval requires additional runtime and adapter packaging. These are heavy dependencies that block casual adoption.

**Decision**: Three install tiers (lite, standard, full) with progressive feature unlock and in-place upgrade. v1.0 standard reserves the dense runtime slot. Full gates Graphiti and Neo4j.

**Consequences**: 60-second first install for lite users, full power available for production deployments.

### ADR-005: Token efficiency as first-class architecture

**Context**: Every retrieval, every observation capture, every hook firing consumes user-paid context window tokens. No competitor treats this as a first-class concern.

**Decision**: Adaptive Context Layer is a required component of all install tiers. Shell output compression, injection deduplication, adaptive top-k, and three injection format levels ship in v1.0.

**Consequences**: 40 to 60 percent session token reduction. Trade-off: layer adds complexity to the hook and retrieval paths. Retrofitting this into a tool not built for it costs 2 to 3 months of engineering, so it is a real moat.

### ADR-006: MIT license, not AGPL-3.0 (superseded by ADR-015)

**Context**: claude-mem ships under AGPL-3.0, which creates enterprise adoption friction.

**Decision**: MIT license. Maximum permissiveness consistent with the project's goal of becoming the default Claude Code memory plugin.

**Consequences**: Enterprises can adopt freely. Trade-off: forks can be closed-source. Mitigation: maintainer velocity and ecosystem (plugin manifest, marketplace) keep upstream the canonical implementation.

**Status**: Superseded by ADR-015 for the 3.0 line. Published 1.x and 2.x artifacts remain MIT.

### ADR-007: `mneme_` prefix on all MCP tools

**Context**: Generic tool names (search, write, recall) collide in multi-server MCP setups.

**Decision**: All exposed MCP tools use the `mneme_` prefix: `mneme_search`, `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`, `mneme_timeline`.

**Consequences**: No namespace clash with other MCP servers in the same client. Marginal verbosity in tool call syntax.

### ADR-008: Anthropic-default Protocol-pluggable LLM provider

**Context**: Background compression needs an LLM, but hardcoding Anthropic forecloses on OpenAI, local Ollama, and future providers, while shipping with a generic provider abstraction risks slow first-impression latency for the dominant use case.

**Decision**: `mneme_core.compression.llm` defines an `LlmProvider` Protocol. The bundled `AnthropicProvider` is the default and the only one wired at v1.0. The Anthropic SDK is lazy-imported behind the provider call so lite and standard installs never load it.

**Consequences**: v1.0 ships with one well-tested provider. Third parties or v1.1 can drop in OpenAI, Bedrock, Ollama by implementing the Protocol with no core changes. Trade-off: the Protocol surface is locked at v1.0 and future signature changes are breaking.

### ADR-009: Migration tool tri-state `--archive` with two-factor delete

**Context**: Users migrating from claude-mem need a safe path that preserves the source SQLite, but also want a clean cutover option without manual file deletion.

**Decision**: `mneme-migrate migrate-from-claude-mem` exposes three archive modes. `preserve` (default) leaves the source untouched. `copy` snapshots the SQLite into `vault/imported/claude-mem/_archive/`. `move` performs the copy and then deletes the source, but only when accompanied by the explicit `--confirm-delete` flag (two-factor gate). Idempotency comes from SHA256 content_hash dedup with stable filenames `cm-{obs,sess,prompt}-{id}.md`.

**Consequences**: Default behavior is non-destructive. Power users can do a full cutover in one command. Re-running the migrator is safe and produces zero duplicates. Trade-off: the two-factor delete adds one extra flag to learn, but the surface is documented in the cookbook recipe and the audit trail in `_manifest.json` makes it reviewable.

### ADR-010: Per-session knowledge graph episode boundary with hybrid bi-temporal source

**Context**: Graphiti expects "episodes" but a Claude Code session is more granular than a typical Graphiti episode, and bi-temporal fact timestamps need both vault frontmatter (authoritative for valid_from) and filesystem mtime (for fallback).

**Decision**: Each Claude Code session becomes one Graphiti episode, boundary marked by SessionStart and Stop hooks. Bi-temporal source is hybrid: frontmatter `created` field is authoritative when present, file mtime is fallback. testcontainers provides ephemeral Neo4j for integration tests so contributors never need a long-lived local Docker.

**Consequences**: Session-granular timeline queries are accurate. The test suite runs in CI without Neo4j install steps. Trade-off: long sessions become large episodes, and Graphiti episode size can affect community detection quality. Mitigation: pre-compaction hook snapshots state before context window flushes.

### ADR-011: Pattern memory and trajectory recorder as vault-native markdown primitives

**Context**: Several agent-memory systems treat patterns (Signal/Action/Outcome heuristics) and trajectories (per-session decision trails) as opaque database tables. This forecloses on user transparency and `git diff` review.

**Decision**: Both primitives are first-class vault-markdown documents. Patterns live under `vault/patterns/` with sectioned `Signal`/`Action`/`Outcome` bodies. Trajectories live under `vault/trajectories/{YYYY-MM-DD}/{id}.md` with per-step appends. Both are retrievable through the same hybrid retrieval pipeline as sessions and topics.

**Consequences**: Pattern reuse and trajectory review become `grep`-able and `git`-history-friendly. Filename slug sanitization blocks path traversal. Trade-off: append-heavy trajectory recording adds disk writes during long sessions. Mitigation: writes are atomic and the file count stays bounded by session count.

### ADR-012: Seeded synthetic benchmark corpus with locked baselines

**Context**: Benchmarks that drift between runs cannot serve as CI regression guards, and benchmarks that depend on private user data cannot be reproduced by contributors.

**Decision**: All five benchmarks (retrieval quality, latency, cost, migration, head-to-head) consume a deterministic synthetic corpus built from `MNEME_BENCH_SEED=42`. Baseline numbers are committed to `benchmarks/*/baseline.json` and CI guards compare every run against them with a tolerance window (nDCG@5 must not drop by more than 0.02, Stop hook p95 must stay under 1000 ms). The head-to-head benchmark defines a `MemoryAdapter` Protocol so claude-mem and future competitors can be measured on identical fixtures.

**Consequences**: Contributors can reproduce numbers locally and CI catches regressions automatically. Trade-off: synthetic corpus does not validate real-world quality, which is why Phase J dogfood week against operator vault is a launch gate.

### ADR-013: Adopt spec-kit (specify-cli) for spec-driven audit trail

**Context**: Constitution Principle VI (Spec-Driven Audit Trail) was added in the 2026-05-20 amendment, requiring every architectural decision to land as an ADR and every release candidate to run `tools/spec_verify.py`. The adoption of spec-kit (specify-cli 0.8.13.dev0 from github.com/github/spec-kit) as the workflow engine for Principle VI is itself an architectural decision and must be documented as an ADR to avoid recursive self-violation of the constitution. Finding F9 in `.specify/specs/001-v1.0.0-launch/analyze.md` surfaced this gap on 2026-05-20.

**Decision**: Adopt spec-kit as the spec-driven workflow tool for mneme v1.0.0 and beyond. The repository ships `.specify/memory/constitution.md` as the canonical constitution, `.specify/specs/NNN-<feature>/{spec,plan,tasks,analyze}.md` as the per-feature workflow artifacts, and `.claude/skills/speckit-*/SKILL.md` as the Claude Code slash-command integration. Slash commands include `/speckit-constitution`, `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`, `/speckit-clarify`, `/speckit-analyze`, `/speckit-checklist`. The 2026-05-20 v1.0.0-launch artifact set (`001-v1.0.0-launch/`) is the first production application and surfaced 12 cross-artifact findings within hours, validating the audit-trail thesis.

**Consequences**: All future architectural decisions go through spec-kit. Constitution amendments require a corresponding ADR. Trade-off: small upfront cost (operator must learn the eight slash commands and the constitution + spec + plan + tasks + analyze ladder), large payoff (audit-trail-driven development discipline is now tool-enforced, not memory-dependent). Retroactive coverage: this ADR opens before the v1.0.0 tag push, satisfying constitution v1.0.0 pin without recursive self-violation. Lesson learned during the first production application: `/analyze` "missing X" claims must be backed by a fresh `Glob` in the same pass rather than relying on memory.

### ADR-014: Multi-client architecture, Claude-Code-native with Codex as an additive layer

**Context**: mneme was built Claude-Code-native. The retrieval core, the MCP server, and the vault contract are client-neutral, while the hooks, the slash commands, and the installer were Claude-Code-specific. OpenAI Codex CLI added a plugin system (skills, MCP servers, lifecycle hooks) whose skill and MCP formats are close to Claude Code's, which makes a second front-end cheap without touching the core.

**Decision**: Keep Claude Code as the primary, native client. Add Codex support as a parallel layer, never by genericizing the core or diluting the Claude-Code-native identity. Concretely: (1) the MCP server and core stay untouched and serve every MCP client; (2) a shared `mneme hook <event>` CLI subcommand gives both clients one OS-agnostic, client-agnostic hook entry; (3) the Claude Code native plugin lives at `packages/mneme-cc-plugin` with `.claude-plugin/` manifests and a repo-root `.claude-plugin/marketplace.json`; (4) the Codex plugin lives at `packages/mneme-codex-plugin` with `.codex-plugin/` manifests and a repo-root `.agents/plugins/marketplace.json`; (5) `mneme install --client=claude-code|codex|all` wires whichever clients are requested, default `claude-code`.

**Consequences**: Codex users get the same six MCP tools, the same two skills, and four of the five lifecycle hooks (SessionStart, PostToolUse, Stop, PreCompact). SessionEnd folds into Stop because Codex has no SessionEnd event. The Claude Code experience is unchanged and stays full-fidelity. Trade-off: two plugin manifests and two marketplace files to keep in sync, and the PostToolUse Bash-output compression is tuned to Claude Code tool names so Codex capture is generic until a Codex tool-name variant lands. The shared core means a retrieval or vault improvement benefits both clients at once.

### ADR-015: Apache License 2.0 from the 3.0 line (supersedes ADR-006)

**Context**: ADR-006 chose MIT for maximum permissiveness. Two pressures changed the calculus for 3.0. First, every directly comparable open-source memory tool in the competitive set (claude-mem's ecosystem peers, Mem0, Letta, Graphiti) ships under Apache-2.0, and enterprise procurement increasingly treats Apache-2.0's explicit patent grant as the baseline for adopting AI-agent infrastructure. Second, 3.0 introduces team-facing surfaces (self-hosted sync, web console) where the patent-retaliation and contribution clauses of Apache-2.0 give adopters and contributors clearer protection than MIT's silence.

**Decision**: License the 3.0 line under the Apache License 2.0. `LICENSE` carries the verbatim license text, `NOTICE` carries the copyright statement. Published 1.x and 2.x artifacts permanently remain MIT. Contributions are inbound = outbound under Apache-2.0. The relicensing was decided unanimously by the maintainers on 2026-06-10 (sole maintainer and sole copyright holder at the time; the lone third-party commit was an automated image recompression with no copyrightable code) and announced ahead of the 3.0.0 release per the GOVERNANCE.md notice requirement.

**Consequences**: Explicit patent grant for users and contributors, license parity with the competitive set so license choice is never an adoption blocker, and unchanged permissiveness in practice (Apache-2.0 remains enterprise-friendly and fork-friendly). Trade-off: slightly heavier compliance surface (NOTICE propagation in redistributions) and GPLv2-incompatibility, which does not affect any current integration.

## Out of Scope for v1.0

See README's "What v1.0 does not ship" section and `docs/COMPETITIVE.md` for the explicit non-goals.
