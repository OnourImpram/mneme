<p align="center">
  <img src="assets/mneme-banner.jpg" alt="mneme — vault-native memory for Claude Code" width="100%">
</p>

# mneme Record

> Vault-native memory for Claude Code. Markdown is ground truth.

<p align="center">
  <a href="https://pypi.org/project/mneme-core/"><img src="https://img.shields.io/pypi/v/mneme-core?label=mneme-core&color=3776ab&logo=pypi&logoColor=white" alt="mneme-core on PyPI"></a>
  <a href="https://pypi.org/project/mneme-cc-plugin/"><img src="https://img.shields.io/pypi/v/mneme-cc-plugin?label=mneme-cc-plugin&color=3776ab&logo=pypi&logoColor=white" alt="mneme-cc-plugin on PyPI"></a>
  <a href="https://www.npmjs.com/package/mneme-mcp-server"><img src="https://img.shields.io/npm/v/mneme-mcp-server?label=mneme-mcp-server&color=cb3837&logo=npm&logoColor=white" alt="mneme-mcp-server on npm"></a>
  <a href="https://github.com/OnourImpram/mneme/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/OnourImpram/mneme/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://github.com/OnourImpram/mneme/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0"></a>
  <img src="https://img.shields.io/pypi/pyversions/mneme-core" alt="Python versions">
  <a href="https://doi.org/10.5281/zenodo.20674727"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.20674727.svg" alt="DOI"></a>
</p>

Every session starts from zero. You re-explain the same architecture, the same constraints, the same decision you already settled yesterday, and the tools that promise to fix it mostly store your conversation history in opaque SQLite blobs and call an LLM every time you finish a session. The record of your own work ends up somewhere you cannot read, cannot grep, and cannot take with you.

mneme records what happened in each Claude Code session as plain markdown files in a directory you own (the vault) and indexes them with SQLite FTS5. The next session opens with a preflight block — today's headings, a git status summary, and the five most recently modified session documents — and the agent queries the index on demand through `mneme_search`.

Here is the file the Stop hook writes. The frontmatter, heading, and summary shape come from `packages/mneme-cc-plugin/src/mneme_cc_plugin/hooks/stop.py` and `packages/mneme-core/src/mneme_core/distill/templates/summary-en.md`; the contents below are illustrative.

```markdown
---
id: session-2026-07-24
type: session
created: 2026-07-24T09:12:41.087213+00:00
schema_version: 1
---
# Sessions 2026-07-24

## 09:12 session a3f19c2e
transcript: `~/.claude/projects/mneme/a3f19c2e.jsonl`

**Session intent**: make the retrieval guard fail on the negative probe too

**Files touched**

- `benchmarks/retrieval/regression_guard.py`
- `benchmarks/retrieval/baseline.json`

**Tool activity** (34 events, 08:41:02 → 09:12:38)

- Edit: 11
- Bash: 9
- Read: 8

*Deterministic extractive summary (zero-LLM). Edit freely — this file is yours.*
```

```bash
pipx install mneme-cc-plugin && mneme install
```

That installs the plugin and registers the lifecycle hooks; `mneme doctor` verifies the result, and profiles and per-client installs are under [Three-Tier Install](#three-tier-install). Claude Code registers six hook events; Codex and Antigravity map four. Any other MCP client (Kimi, Qwen, Cline, Cursor) gets the nine MCP tools through the open adapter, with no lifecycle hooks and no automatic capture.

- **What it stores is a file you can open.** When a session has actually changed something in the vault, the Stop hook appends a timestamped `## HH:MM session` block to `vault/sessions/YYYY-MM-DD.md` with `type: session` frontmatter, written atomically under a cross-process lock; `mneme index rebuild` reconstructs the FTS5 index over every markdown file in the vault, so the database is derived state you can delete.
- **Closing a session costs nothing and takes 2 ms.** "No LLM call on the critical path" is enforced in CI rather than promised in prose. `tools/spec_verify.py` parses all six lifecycle hook modules and fails the build on any import of seven network-capable roots (`anthropic`, `openai`, `requests`, `httpx`, `urllib.request`, `urllib3`, `aiohttp`), and `packages/mneme-cc-plugin/tests/integration/test_c3_no_network.py` re-checks the full transitive import closure of the three hot-path hooks at runtime, where a static scan cannot see. The Stop-hook proxy benchmark measures 2 ms at p95 over 100 sessions against a 1000 ms ceiling: `benchmarks/latency/p95_guard.py` enforces that ceiling inside the same path-scoped benchmark workflow described below, and `packages/mneme-cc-plugin/tests/unit/test_stop_performance.py` re-checks it over 100 real Stop calls on every CI run, which carries no path filter.
- **Search quality cannot silently degrade.** A pull request that drops production-FTS5 nDCG@5 more than 0.02 below the locked baseline (0.8006, reported as 0.801), drops Recall@10 more than 0.05 below 1.00, or fails the out-of-vocabulary negative probe fails the build — `benchmarks/retrieval/regression_guard.py`, run by `.github/workflows/bench.yml` on every pull request and every push to `main` that touches `packages/mneme-core`, `packages/mneme-mcp`, `benchmarks/`, or the workflow file itself.

## Scope and limits

Those numbers come from the in-repo benchmark suite, seeded with `MNEME_BENCH_SEED=42`. Benchmark A uses a 500-document corpus. Benchmark E uses its default 300-document, 30-query fixture. Reproduce with `make bench-all`. Both figures above — the 0.801 and the 2 ms — carry the note that governs every figure in this README:

> **Note:** All figures below are deterministic regression anchors computed on a seeded synthetic corpus; they are not real-world quality measurements (see ADR-012).

**Retrieval claims follow the reachable path.** The production `mneme_search` path is FTS5 BM25. The Python core contains an experimental feature-hashed lexical-vector backend and an RRF fusion protocol used by unit tests and synthetic benchmarks, but that backend is not wired into the MCP server or installer. Full-profile summarize and timeline can add gated local Graphiti and Neo4j fields. A true semantic embedding backend remains roadmap.

**Privacy and network.** Inline `<private>` tag redaction at staging write with SHA256 audit log. Zero outbound network calls except opted-in compression LLM and optional local Neo4j. Compression happens in the background, opt-in, with a cost cap.

**Temporal reasoning.** The deterministic claim lifecycle (valid-from/to, supersedes, as-of queries, contradiction detection, `temporal blame` provenance time-travel) is built in on every profile — pure SQLite, no extra dependency. Graphiti export and LLM claim extraction remain optional and never run on the Stop or critical path.

**Context Continuity Engine (opt-in).** Checkpoints are plain markdown in the vault, zero-LLM, default off.

**Obsidian is fully optional.** A vault is simply a plain directory of markdown files. mneme requires no specific editor, no external application, and no Obsidian installation. You can work with your vault using `grep`, `git`, VS Code, or any text editor. The term "vault" is borrowed convention for a self-contained markdown directory, not a dependency on any particular tool. Because the vault is plain markdown, a user who already uses Obsidian can point it at the same directory and get rendered notes, backlinks, and graph-view navigation over the wikilinks mneme writes. The two tools coexist cleanly: mneme stores all derived state (indexes, staging, audit logs) inside a `.mneme` directory that Obsidian ignores as a dot folder, and mneme's indexer excludes the `.obsidian` settings folder from indexing, so neither tool disturbs the other. Obsidian is a convenient viewer and navigator for vault content. It is not part of mneme's capture, indexing, or retrieval path, and it must not be treated as an installation prerequisite.

The full shipped / gated / roadmap ledger is in [Implementation Status](#implementation-status); the capabilities mneme does not ship at all are listed under [What 2.0 Does Not Ship Yet](#what-20-does-not-ship-yet).

**Status**: 3.6.1 public release. Package, plugin, runtime, citation, and documentation version sources are kept in lockstep by `tools/version_bump.py` (18 sources including this line, verified in CI), so no single declared version can drift. Upgrading from an earlier line: [`docs/UPGRADING.md`](docs/UPGRADING.md).

## Tools

The MCP server registers nine tools. Every client that speaks MCP gets all nine; lifecycle hooks
and automatic capture are a separate layer that only Claude Code, Codex, and Antigravity provide.
The authoritative list is `packages/mneme-mcp/src/tool_registry.ts`.

| Tool | What it does |
|---|---|
| `mneme_search` | FTS5 BM25 retrieval over the vault with Turkish casefold normalization, plus date, memory-type, and scope filters. Returns ranked hits and EvidenceCards carrying content hashes, trust, confidence, and the backend that actually ran. |
| `mneme_recall` | Retrieves indexed documents by session identifier, date range, and scope. Returns paths, titles, modification times, memory types, and optionally the full markdown body. |
| `mneme_write` | Atomically appends or replaces a markdown section in a vault file. Enforces vault path containment and redacts private spans before storage. |
| `mneme_summarize` | Groups FTS5 matches for a topic by vault directory within optional date and scope filters. Graphiti enrichment appears only when that optional local graph integration is configured. |
| `mneme_timeline` | Returns scope-restricted references for a subject in chronological order. Graphiti facts and bi-temporal filtering appear only when the optional local graph integration is available. |
| `mneme_prime` | Builds a token-budgeted preflight context bundle from recent sessions and topic-relevant matches. A caller session identifier enables per-session injection deduplication and full, keypoints, or reference formatting. |
| `mneme_propose` | Queues a redacted memory-edit proposal for the policy drain. The server does not apply the edit directly; durable categories always require human approval. |
| `mneme_checkpoint_list` | Lists recent Context Continuity Engine checkpoints from the active scope, newest first. Missing checkpoint state returns an empty list. |
| `mneme_working_set_load` | Loads salience-ranked working-set items from a Context Continuity Engine checkpoint. Unknown and out-of-scope anchors return the same neutral not-found result. |

## How mneme compares

Memory tools in the Claude Code and agent ecosystem make different trade-offs. The table below compares architectural capabilities across the dimensions mneme commits to, and it deliberately includes the rows where another tool leads. These cells describe design properties that are publicly verifiable from each tool's documentation. They are not a benchmarked ranking. For mneme's own reproducible numbers see [Reproducible Numbers](#reproducible-numbers); for per-tool detail and an honest "where mneme is not the best fit" list see [`docs/COMPETITIVE.md`](docs/COMPETITIVE.md).

Legend: **✓** built in · **gated** shipped, needs an opt-in dependency or flag · **~** partial · **—** not available · **n/a** the dimension does not apply.

| Dimension | mneme | claude-mem | mem0 | Letta | Zep | Supermemory |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Plain-markdown store you can `git diff` and grep | **✓** | — | — | ~ | — | — |
| Built-in `<private>` redaction with SHA256 audit | **✓** | — | — | — | — | — |
| Deterministic Stop capture, no LLM call | **✓** | — | n/a | n/a | n/a | n/a |
| Hybrid retrieval in the normal user path | ~ | ~ | ~ | ~ | ✓ | ✓ |
| Temporal claim lifecycle (valid-from/to, supersedes, blame) | **✓** | — | ~ | ~ | ✓ | ~ |
| Project and code graph (tree-sitter, PR-impact) | gated | ~ | — | — | — | — |
| Adaptive token and context budget | **✓** | — | — | — | — | — |
| Agent security: capability firewall, taint, approval gate | **✓** | — | — | — | — | — |
| One-command lossless migration from claude-mem | **✓** | n/a | — | — | — | — |
| Local-first, no cloud account required | **✓** | ✓ | ~ | ✓ | — | — |
| Runs in Claude Code, Codex, Antigravity, any MCP client | **✓** | ~ | ~ | ~ | ~ | ~ |
| License | Apache-2.0 | Apache-2.0 | Apache-2.0 | Apache-2.0 | cloud | open source |
| Team memory with a web graph UI (mneme: self-hosted git sync + local console) | **✓** | — | ~ | — | **✓** | **✓** |
| Agent autonomously rewrites its own memory (mneme: policy-graduated, rollback, audit chain) | **✓** | — | ~ | **✓** | — | — |
| Auto-summarization at session end, on by default (mneme: deterministic zero-LLM) | **✓** | **✓** | — | — | ~ | ~ |
| Localized observation-prompt presets (mneme: en + tr) | ~ | **✓** | — | — | — | — |

The 3.0 line closed the former gap rows on mneme's own terms. Team memory is self-hosted (any git remote, redaction-before-share, optional age end-to-end encryption) with a loopback-only web console rather than a vendor cloud. Autonomy is policy-graduated: the agent applies operator-allowed low-risk edit classes on its own, every change is journalled for one-command rollback and chained into a tamper-evident HMAC audit log, and durable categories always keep a human in the loop. The default-on session summary is deterministic and zero-LLM — no key, no cost, no latency — with LLM compression as the opt-in richer layer. Localized presets ship for English and Turkish today (claude-mem still leads on raw language count, hence the honest ~). Where a hosted product is genuinely the better fit, `docs/COMPETITIVE.md` says so.

## Implementation Status

An honest, at-a-glance map of what is shipped today versus what is gated behind optional infrastructure or still on the roadmap. **Shipped** means present in the default install path and covered by CI. **Gated** means implemented but inactive until you provide the optional dependency or flag. **Roadmap** means designed (often with a seam or protocol already in place) but not yet packaged.

| Capability | Status | Detail |
|---|---|---|
| FTS5 BM25 retrieval (`mneme_search`) | Shipped | default MCP search path |
| RRF fusion protocol | Experimental | Python API plus synthetic benchmark harness; not wired into `mneme_search` |
| `<private>` redaction + SHA256 audit | Shipped | Python + TypeScript mirror; staging write |
| Zero-LLM deterministic Stop capture | Shipped | `Stop` hook appends a typed session doc |
| Adaptive context layer (shell compress, injection dedup, adaptive top-k) | Shipped | `distill.*` subsystem |
| Pattern + trajectory memory | Shipped | vault-markdown primitives |
| Claude Code / Codex / Antigravity native plugins | Shipped (native) | Claude Code registers 6 hook events; Codex and Antigravity map 4; 2 skills + MCP |
| Open MCP adapter (Kimi, Qwen, any MCP client) | Shipped (non-native) | MCP tools only, no auto-capture |
| Background AI compression | Shipped (opt-in, default off) | monthly cost-cap ledger |
| Feature-hashed lexical-vector retrieval | Experimental, disconnected | Implemented in Python core and benchmarks; no documented installer or MCP user path |
| Temporal claim lifecycle + rule-based claim extraction + `temporal blame` | Shipped | Graphiti export gated; LLM extraction optional, never on the Stop/critical path |
| Project + code graph (mneme-graph) | Shipped (separate package) | tree-sitter Python/JavaScript/TypeScript extraction, community detection, PR-impact, entity canonicalization |
| Code memory (mneme-code) | Shipped (separate package) | AGENTS.md procedural parsing, test-output to failure memory, fix-trajectory |
| Domain modes | Shipped | vault-config user modes + CLI; clinical and security-review modes block external extraction and artifact upload; user config can never weaken a built-in privacy mode or disable redaction |
| Agent security | Shipped | capability firewall, data-flow taint tracking, human-approval gate for durable edits, poisoned-vault benchmark |
| Read-only console | Shipped | self-contained, offline, injection-safe HTML audit report |
| Connectors (Obsidian local + GitHub injected-transport) | Shipped (opt-in, default off) | redaction-before-ingest; revoke by disabling |
| KG temporal enrichment via live Neo4j/Graphiti writes (summarize/timeline) | Gated | full profile: Docker + Neo4j |
| Packaged semantic embedding adapter | Roadmap | adapter protocol exists; no packaged semantic model or production MCP wiring |
| Web-based knowledge-graph visual explorer | Roadmap | planned |
| Multi-user team features (merge-conflict resolution, per-user ACL, dashboards) | Roadmap (Team) | read-only shared vaults via git remote work today |

## Reproducible Numbers

These come from the in-repo benchmark suite, seeded with `MNEME_BENCH_SEED=42`. Benchmark A uses a 500-document corpus. Benchmark E uses its default 300-document, 30-query fixture. Reproduce with `make bench-all`.

**Note:** All figures below are deterministic regression anchors computed on a seeded synthetic corpus; they are not real-world quality measurements (see ADR-012).

| Benchmark | Metric | Result |
|---|---|---|
| A. Retrieval quality | nDCG@5, production FTS5 | **0.801** (Recall@10 1.00, MRR 0.734) |
| B. Stop hook latency | p95 | **2 ms** (constraint budget 1000 ms) |
| B. Retrieve latency | p95 | **3 ms** on indexed 500-doc corpus |
| C. Shell output compression | reduction | **88 percent** on redundant Bash logs |
| C. Injection deduplication | skip rate | **95 percent** in tight 20-turn sessions |
| C. Compressed format | savings | keypoints **46 percent**, ref **88 percent** vs full |
| D. Migration tool | assertions | **4 of 4** pass (migrated, idempotent, dedup, redaction) |
| E. Head-to-head adapter | mneme leg | nDCG@5 **0.831**, MRR **0.772** on 300-doc fixture |

CI regression guards lock the path-scoped benchmark surface. Pull requests touching benchmarked code run the benchmark workflow. Any run that drops production FTS5 Benchmark A nDCG@5 by more than 0.02 or breaches the 1000 ms Stop hook p95 fails the build. The BoW RRF condition is reported only as a lexical-surrogate ablation.

## Three-Tier Install

```bash
# Lite: FTS5 + Stop hook + privacy redaction + 9 MCP tools (Python + Node only)
pipx install mneme-cc-plugin
mneme install --profile=lite

# Standard: lite plus the standard optional dependency profile.
# The normal MCP search path remains FTS5. No --enable-dense installer flag ships.
mneme install --profile=standard

# Full: standard + gated Graphiti temporal knowledge graph enrichment (Docker + Neo4j)
mneme install --profile=full
```

Upgrade in place without losing data.

```bash
mneme upgrade --profile=standard
```

Verify a healthy install.

```bash
mneme doctor
```

## Using mneme with Codex

mneme is Claude-Code-native by origin. Because its retrieval core (`mneme-core`), its MCP server (`mneme-mcp`), and its vault contract are client-neutral, mneme also runs inside the OpenAI Codex CLI as an additive layer, with no loss of fidelity.

```bash
# Plugin: skills, MCP server, and lifecycle hooks together
codex plugin marketplace add OnourImpram/mneme

# Or wire just the MCP server into ~/.codex/config.toml
mneme install --client=codex
```

Codex gets the same nine MCP tools, the same two skills, and the same vault. Four of mneme's six registered Claude Code hook events map to native Codex lifecycle events (SessionStart, PostToolUse, Stop, PreCompact), and SessionEnd folds into Stop. UserPromptSubmit has no Codex mapping. See `docs/CODEX.md` for the full coverage table and ADR-014 in `docs/ARCHITECTURE.md` for the multi-client design.

## Using mneme with Antigravity

Antigravity (Google's agentic IDE) uses the Gemini-CLI extension model, and mneme ships a native extension for it.

```bash
mneme install --client=antigravity
```

This installs the `mneme` extension into `~/.gemini/extensions/`, wiring the same nine MCP tools, the same two skills, a `GEMINI.md` rules file, and lifecycle hooks (SessionStart, PostToolUse, Stop, PreCompact) that map to the same `mneme hook <event>` core path Claude Code and Codex use. Because Antigravity exposes a Stop hook, session capture has full native parity.

## Other MCP clients (open adapter)

Any MCP-capable client (Kimi, Qwen, Cline, Cursor, and others) can use mneme through the open adapter. This is the non-native tier: the nine MCP tools are available for the model to call, but there are no lifecycle hooks and no automatic capture.

```bash
mneme install --client=mcp --config <path-to-your-clients-mcp-config.json>
```

mneme merges only its own server entry and leaves every other server in the config untouched. See `docs/INTEGRATIONS.md` for the client-tiering details and `examples/` for a config snippet and a portable AGENTS.md template.

## What 2.0 Ships

- 9 MCP tools: `mneme_search`, `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`, `mneme_timeline`, `mneme_propose`, `mneme_checkpoint_list`, `mneme_working_set_load`. Default search is FTS5. Full-profile summarize and timeline can add KG fields when the local graph is active. `mneme_checkpoint_list` and `mneme_working_set_load` support the Context Continuity Engine (CCE): list available working-set checkpoints and load a checkpoint's salience-ranked items for JIT context re-injection.
- 5 Claude Code hooks: `PostToolUse`, `SessionStart`, `Stop`, `PreCompact`, `SessionEnd`.
- 3 slash commands: `/mneme:prime`, `/mneme:recall`, `/mneme:migrate`.
- 2 skills: `mneme-prime`, `mneme-search`.
- 7-benchmark suite (`make bench-all`): retrieval quality (A), Stop/retrieve latency (B), adaptive-context cost (C), claude-mem migration (D), head-to-head adapter (E), LongMemEval (F), CCE compaction-recall (G).
- One-command migration: `mneme-migrate migrate-from-claude-mem` with tri-state archive flag, idempotent re-run, and a per-run hash-checked rollback manifest.
- Adaptive Context Layer: `distill.shell_compress`, `distill.injection_dedup`, `distill.adaptive_topk`, `distill.compressed_format`, plus `mneme audit` for token reports and `mneme audit-log` for redaction audit entries.
- Pattern memory: `mneme patterns {store, search, list, show, delete}` writing vault-markdown Signal/Action/Outcome documents.
- Trajectory recorder: `mneme trajectory {start, step, end, show, list}` capturing per-session decision trails under `vault/trajectories/`.
- Background AI compression (opt-in, default off): `mneme compress {enable, disable, status, dry-run, run}` with monthly cost cap ledger.

### The 2.0 advanced line

Eight modules extend mneme's core for specialized workloads. All are gated or shipped as separate packages. All ship with redaction-before-store, provenance on every record, and confidence labels on every extracted claim. None runs on the Stop or critical path.

- **Project graph** (mneme-graph): tree-sitter extraction for Python, JavaScript, and TypeScript; community detection; PR-impact analysis; entity canonicalization.
- **Code memory** (mneme-code): AGENTS.md procedural parsing, test-output to failure memory, fix-trajectory capture.
- **Domain modes**: clinical and security-review modes block external extraction and artifact upload at config layer. A user config can never weaken a built-in privacy mode or disable redaction.
- **Agent security**: capability firewall, data-flow taint tracking, human-approval gate for durable edits, poisoned-vault benchmark.
- **Read-only console**: self-contained, offline, injection-safe HTML audit report requiring no server.
- **Experimental feature-hashed lexical-vector retrieval**: a Python-core backend and RRF seam used by tests and synthetic benchmarks. It is not connected to the installer or production MCP search path.
- **Temporal extraction + Graphiti export**: rule-based claim extraction, valid-from/to lifecycle, supersedes links, and export to a local Graphiti instance. LLM extraction is optional and never on the critical path. Live Neo4j writes are gated on the full Docker + Neo4j profile.
- **Connectors** (Obsidian local + GitHub injected-transport): opt-in, default off. Redaction runs before every ingest. Revoke by disabling in config.

## What 2.0 Does Not Ship Yet

A credible "best in market" claim requires honest scope acknowledgment.

- No packaged semantic embedding backend, and no installed dense-retrieval user path. The feature-hashed lexical-vector implementation remains an experimental Python API.
- No dense or KG leg inside `mneme_search`. MCP search is FTS5. KG enrichment is gated to summarize and timeline when local full-profile graph state is active.
- No cloud SaaS option. mneme is local-first by architectural conviction.
- No web-based knowledge graph visual explorer. Planned.
- No multi-user team features with merge-conflict resolution, per-user ACL, or team dashboards. Read-only shared vaults via git remote work today. Full team support is roadmap.

See `docs/COMPETITIVE.md` for the full landscape and which tools may suit those needs better.

## Documentation

- `docs/ARCHITECTURE.md`: design philosophy and the 16 Architecture Decision Records (ADR-001 through ADR-016, with ADR-006 superseded by ADR-015).
- `docs/CONSTRAINTS.md`: six sacred constraints and how to verify each.
- `docs/VAULT.md`: vault contract, frontmatter specification, atomic write pattern.
- `docs/HOOKS.md`: hook integration guide, timing budgets, fail-soft contract.
- `docs/MCP.md`: tool API reference with JSON schemas and example calls.
- `docs/RELEASE.md`: GitHub tag, release, and metadata checklist.
- `docs/COOKBOOK.md`: ten worked recipes with full Claude Code transcripts.
- `docs/MIGRATION-FROM-CLAUDE-MEM.md`: one-command migration with tri-state archive and hash-checked rollback walkthrough.
- `docs/BENCHMARKS.md`: methodology and the locked baseline numbers.
- `docs/COMPETITIVE.md`: living landscape document (monthly refresh).
- `docs/PRIVACY.md`: outbound network call audit and telemetry policy (zero by default).
- `docs/GOVERNANCE.md`: maintenance model, release authority, succession.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`. Releases up to and including the 2.x line were published under MIT and remain so.

## Acknowledgments

Maintained by Onour Impram ([@OnourImpram](https://github.com/OnourImpram)). The Adaptive Context Layer and the pattern and trajectory primitives draw conceptually from token-compression and agent-DB patterns proven in production internal tooling. The architecture is mneme-native, the lineage is operator experience.
