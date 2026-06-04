<p align="center">
  <img src="assets/mneme-banner.jpg" alt="mneme — vault-native memory for Claude Code" width="100%">
</p>

# mneme

> Vault-native memory for Claude Code. Markdown is ground truth.

<p align="center">
  <a href="https://pypi.org/project/mneme-core/"><img src="https://img.shields.io/pypi/v/mneme-core?label=mneme-core&color=3776ab&logo=pypi&logoColor=white" alt="mneme-core on PyPI"></a>
  <a href="https://pypi.org/project/mneme-cc-plugin/"><img src="https://img.shields.io/pypi/v/mneme-cc-plugin?label=mneme-cc-plugin&color=3776ab&logo=pypi&logoColor=white" alt="mneme-cc-plugin on PyPI"></a>
  <a href="https://www.npmjs.com/package/mneme-mcp-server"><img src="https://img.shields.io/npm/v/mneme-mcp-server?label=mneme-mcp-server&color=cb3837&logo=npm&logoColor=white" alt="mneme-mcp-server on npm"></a>
  <a href="https://github.com/TheGoatPsy/mneme/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/TheGoatPsy/mneme/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://github.com/TheGoatPsy/mneme/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
  <img src="https://img.shields.io/pypi/pyversions/mneme-core" alt="Python versions">
</p>

<p align="center"><code>pipx install mneme-cc-plugin &amp;&amp; mneme install</code></p>


FTS5 retrieval, RRF-fused hybrid core, gated temporal knowledge graph, zero LLM cost on Stop, token-aware adaptive context budget, agent security firewall, domain privacy modes.

**Status**: 2.0.0 public release. Package, plugin, runtime, citation, and documentation version sources are kept in lockstep by `tools/version_bump.py` (13 sources, verified in CI), so no single declared version can drift.

## Why mneme

Most Claude Code memory plugins store your conversation history in opaque SQLite blobs and call an LLM every time you finish a session. mneme takes the opposite stance.

- **Markdown is ground truth.** Your vault is a directory of plain `.md` files you can `git diff`, `grep`, edit, and back up.
- **No LLM on the critical path.** The Stop hook appends deterministically. Compression happens in the background, opt-in, with a cost cap.
- **Hybrid retrieval, shipped and opt-in.** The default MCP search path is FTS5 BM25. A local hashing-embedding dense backend is now a shipped opt-in feature, RRF-fused, activated with a flag. Full-profile knowledge graph enrichment (Graphiti + Neo4j) remains gated. The heavyweight packaged LEANN / sentence-transformers dense adapter is roadmap.
- **Token-efficient by architecture.** Shell output compression, injection deduplication, adaptive top-k, and three injection format levels save 40 to 60 percent on session token consumption.
- **Privacy by default.** Inline `<private>` tag redaction at staging write with SHA256 audit log. Zero outbound network calls except opted-in compression LLM and optional local Neo4j.
- **Temporal reasoning.** Temporal claim lifecycle, rule-based extraction, and Graphiti export ship as a gated feature. LLM extraction is optional and never on the Stop or critical path. Lite installs fall back to FTS5 and mtime ordering.
- **Pattern and trajectory memory.** First-class vault-markdown primitives for Signal/Action/Outcome patterns and per-session step recorders, queryable via the same retrieval pipeline.
- **Agent security and domain modes.** A capability firewall, data-flow taint tracking, and a human-approval gate for durable edits ship in 2.0. Domain privacy modes (clinical, security-review) block external extraction and artifact upload at the config layer. A mode can never weaken built-in privacy guarantees or disable redaction.

### On vaults and Obsidian

A vault is simply a plain directory of markdown files. mneme requires no specific editor, no external application, and no Obsidian installation. You can work with your vault using `grep`, `git`, VS Code, or any text editor. The term "vault" is borrowed convention for a self-contained markdown directory, not a dependency on any particular tool.

Obsidian is fully optional. Because the vault is plain markdown, a user who already uses Obsidian can point it at the same directory and get rendered notes, backlinks, and graph-view navigation over the wikilinks mneme writes. The two tools coexist cleanly: mneme stores all derived state (indexes, staging, audit logs) inside a `.mneme` directory that Obsidian ignores as a dot folder, and mneme's indexer excludes the `.obsidian` settings folder from indexing, so neither tool disturbs the other. Obsidian is a convenient viewer and navigator for vault content. It is not part of mneme's capture, indexing, or retrieval path, and it must not be treated as an installation prerequisite.

## How mneme compares

Memory tools in the Claude Code and agent ecosystem make different trade-offs. The table below compares architectural capabilities across the dimensions mneme commits to, and it deliberately includes the rows where another tool leads. These cells describe design properties that are publicly verifiable from each tool's documentation. They are not a benchmarked ranking. For mneme's own reproducible numbers see [Reproducible Numbers](#reproducible-numbers); for per-tool detail and an honest "where mneme is not the best fit" list see [`docs/COMPETITIVE.md`](docs/COMPETITIVE.md).

Legend: **✓** built in · **gated** shipped, needs an opt-in dependency or flag · **~** partial · **—** not available · **n/a** the dimension does not apply.

| Dimension | mneme | claude-mem | mem0 | Letta | Zep | Supermemory |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Plain-markdown store you can `git diff` and grep | **✓** | — | — | ~ | — | — |
| Built-in `<private>` redaction with SHA256 audit | **✓** | — | — | — | — | — |
| Deterministic Stop capture, no LLM call | **✓** | — | n/a | n/a | n/a | n/a |
| Hybrid retrieval, FTS5 plus local dense, RRF-fused | **✓** | ~ | ~ | ~ | ✓ | ✓ |
| Temporal claim lifecycle (valid-from/to, supersedes) | gated | — | ~ | ~ | ✓ | ~ |
| Project and code graph (tree-sitter, PR-impact) | gated | ~ | — | — | — | — |
| Adaptive token and context budget | **✓** | — | — | — | — | — |
| Agent security: capability firewall, taint, approval gate | **✓** | — | — | — | — | — |
| One-command lossless migration from claude-mem | **✓** | n/a | — | — | — | — |
| Local-first, no cloud account required | **✓** | ✓ | ~ | ✓ | — | — |
| Runs in Claude Code, Codex, Antigravity, any MCP client | **✓** | ~ | ~ | ~ | ~ | ~ |
| License | MIT | Apache-2.0 | Apache-2.0 | Apache-2.0 | cloud | open source |
| Cloud-hosted team memory with a web graph UI today | — | — | ~ | — | **✓** | **✓** |
| Agent autonomously rewrites its own memory | ~ | — | ~ | **✓** | — | — |
| Auto-summarization at session end, on by default | ~ | **✓** | — | — | ~ | ~ |
| Localized observation-prompt presets | — | **✓** | — | — | — | — |

The last four rows are dimensions where another tool leads today. mneme is local-first by conviction, so a hosted team UI and always-on auto-summarization are deliberate non-goals at this stage, the self-edit is intentionally routed through a human-approval gate rather than performed autonomously, and localized prompt presets are planned. We publish these rows because durable credibility outweighs a one-sided table.

## Implementation Status

An honest, at-a-glance map of what is shipped today versus what is gated behind optional infrastructure or still on the roadmap. **Shipped** means present in the default install path and covered by CI. **Gated** means implemented but inactive until you provide the optional dependency or flag. **Roadmap** means designed (often with a seam or protocol already in place) but not yet packaged.

| Capability | Status | Detail |
|---|---|---|
| FTS5 BM25 retrieval (`mneme_search`) | Shipped | default MCP search path |
| RRF fusion protocol | Shipped | `mneme-core/retrieval/rrf.py`; FTS5-fed by default |
| `<private>` redaction + SHA256 audit | Shipped | Python + TypeScript mirror; staging write |
| Zero-LLM deterministic Stop capture | Shipped | `Stop` hook appends a typed session doc |
| Adaptive context layer (shell compress, injection dedup, adaptive top-k) | Shipped | `distill.*` subsystem |
| Pattern + trajectory memory | Shipped | vault-markdown primitives |
| Claude Code / Codex / Antigravity native plugins | Shipped (native) | 5 lifecycle hooks + 2 skills + MCP |
| Open MCP adapter (Kimi, Qwen, any MCP client) | Shipped (non-native) | MCP tools only, no auto-capture |
| Background AI compression | Shipped (opt-in, default off) | monthly cost-cap ledger |
| Local dense retrieval (hashing-embedding, RRF-fused) | Shipped (opt-in) | FTS5 remains the default; sentence-transformers is an opt-in seam, not a default dependency |
| Temporal claim lifecycle + rule-based claim extraction + Graphiti export | Shipped (gated) | LLM extraction is optional and never on the Stop/critical path |
| Project + code graph (mneme-graph 0.2.0) | Shipped (separate package) | tree-sitter Python/JavaScript/TypeScript extraction, community detection, PR-impact, entity canonicalization |
| Code memory (mneme-code 0.2.0) | Shipped (separate package) | AGENTS.md procedural parsing, test-output to failure memory, fix-trajectory |
| Domain modes | Shipped | vault-config user modes + CLI; clinical and security-review modes block external extraction and artifact upload; user config can never weaken a built-in privacy mode or disable redaction |
| Agent security | Shipped | capability firewall, data-flow taint tracking, human-approval gate for durable edits, poisoned-vault benchmark |
| Read-only console | Shipped | self-contained, offline, injection-safe HTML audit report |
| Connectors (Obsidian local + GitHub injected-transport) | Shipped (opt-in, default off) | redaction-before-ingest; revoke by disabling |
| KG temporal enrichment via live Neo4j/Graphiti writes (summarize/timeline) | Gated | full profile: Docker + Neo4j |
| Packaged LEANN / sentence-transformers dense adapter | Roadmap | local hashing-embedding dense backend ships; heavyweight adapter is roadmap |
| Web-based knowledge-graph visual explorer | Roadmap | planned |
| Multi-user team features (merge-conflict resolution, per-user ACL, dashboards) | Roadmap (Team) | read-only shared vaults via git remote work today |

## Reproducible Numbers

These come from the in-repo benchmark suite, seeded with `MNEME_BENCH_SEED=42`. Benchmark A uses a 500-document corpus. Benchmark E uses its default 300-document, 30-query fixture. Reproduce with `make bench-all`.

**Note:** All figures below are deterministic regression anchors computed on a seeded synthetic corpus; they are not real-world quality measurements (see ADR-012).

| Benchmark | Metric | Result |
|---|---|---|
| A. Retrieval quality | nDCG@5, RRF fused | **0.893** (FTS5 baseline 0.801, +9.2 points) |
| B. Stop hook latency | p95 | **2 ms** (constraint budget 1000 ms) |
| B. Retrieve latency | p95 | **3 ms** on indexed 500-doc corpus |
| C. Shell output compression | reduction | **88 percent** on redundant Bash logs |
| C. Injection deduplication | skip rate | **95 percent** in tight 20-turn sessions |
| C. Compressed format | savings | keypoints **46 percent**, ref **88 percent** vs full |
| D. Migration tool | assertions | **4 of 4** pass (migrated, idempotent, dedup, redaction) |
| E. Head-to-head adapter | mneme leg | nDCG@5 **0.831**, MRR **0.772** on 300-doc fixture |

CI regression guards lock the path-scoped benchmark surface. Pull requests touching benchmarked code run the benchmark workflow. Any run that drops Benchmark A nDCG@5 by more than 0.02 or breaches the 1000 ms Stop hook p95 fails the build.

## Three-Tier Install

```bash
# Lite: FTS5 + Stop hook + privacy redaction + 6 MCP tools (Python + Node only)
pipx install mneme-cc-plugin
mneme install --profile=lite

# Standard: lite + opt-in local dense retrieval backend (hashing-embedding, RRF-fused).
# FTS5 remains the default MCP search path. Dense retrieval is a shipped opt-in feature,
# not roadmap. Enable with --enable-dense after install.
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
codex plugin marketplace add TheGoatPsy/mneme

# Or wire just the MCP server into ~/.codex/config.toml
mneme install --client=codex
```

Codex gets the same six MCP tools, the same two skills, and the same vault. Four of mneme's five Claude Code hooks map to native Codex lifecycle events (SessionStart, PostToolUse, Stop, PreCompact), and SessionEnd folds into Stop. See `docs/CODEX.md` for the full coverage table and ADR-014 in `docs/ARCHITECTURE.md` for the multi-client design.

## Using mneme with Antigravity

Antigravity (Google's agentic IDE) uses the Gemini-CLI extension model, and mneme ships a native extension for it.

```bash
mneme install --client=antigravity
```

This installs the `mneme` extension into `~/.gemini/extensions/`, wiring the same six MCP tools, the same two skills, a `GEMINI.md` rules file, and lifecycle hooks (SessionStart, PostToolUse, Stop, PreCompact) that map to the same `mneme hook <event>` core path Claude Code and Codex use. Because Antigravity exposes a Stop hook, session capture has full native parity.

## Other MCP clients (open adapter)

Any MCP-capable client (Kimi, Qwen, Cline, Cursor, and others) can use mneme through the open adapter. This is the non-native tier: the six MCP tools are available for the model to call, but there are no lifecycle hooks and no automatic capture.

```bash
mneme install --client=mcp --config <path-to-your-clients-mcp-config.json>
```

mneme merges only its own server entry and leaves every other server in the config untouched. See `docs/INTEGRATIONS.md` for the client-tiering details and `examples/` for a config snippet and a portable AGENTS.md template.

## What 2.0 Ships

- 6 MCP tools: `mneme_search`, `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`, `mneme_timeline`. Default search is FTS5. Full-profile summarize and timeline can add KG fields when the local graph is active.
- 5 Claude Code hooks: `PostToolUse`, `SessionStart`, `Stop`, `PreCompact`, `SessionEnd`.
- 3 slash commands: `/mneme:prime`, `/mneme:recall`, `/mneme:migrate`.
- 2 skills: `mneme-prime`, `mneme-search`.
- 5-benchmark suite (`make bench-all`) including a head-to-head adapter for claude-mem v13.2.0.
- One-command migration: `mneme-migrate migrate-from-claude-mem` with tri-state archive flag and idempotent re-run.
- Adaptive Context Layer: `distill.shell_compress`, `distill.injection_dedup`, `distill.adaptive_topk`, `distill.compressed_format`, plus `mneme audit` for token reports and `mneme audit-log` for redaction audit entries.
- Pattern memory: `mneme patterns {store, search, list, show, delete}` writing vault-markdown Signal/Action/Outcome documents.
- Trajectory recorder: `mneme trajectory {start, step, end, show, list}` capturing per-session decision trails under `vault/trajectories/`.
- Background AI compression (opt-in, default off): `mneme compress {enable, disable, status, dry-run, run}` with monthly cost cap ledger.

### The 2.0 advanced line

Eight modules extend mneme's core for specialized workloads. All are gated or shipped as separate packages. All ship with redaction-before-store, provenance on every record, and confidence labels on every extracted claim. None runs on the Stop or critical path.

- **Project graph** (mneme-graph 0.2.0): tree-sitter extraction for Python, JavaScript, and TypeScript; community detection; PR-impact analysis; entity canonicalization.
- **Code memory** (mneme-code 0.2.0): AGENTS.md procedural parsing, test-output to failure memory, fix-trajectory capture.
- **Domain modes**: clinical and security-review modes block external extraction and artifact upload at config layer. A user config can never weaken a built-in privacy mode or disable redaction.
- **Agent security**: capability firewall, data-flow taint tracking, human-approval gate for durable edits, poisoned-vault benchmark.
- **Read-only console**: self-contained, offline, injection-safe HTML audit report requiring no server.
- **Local dense retrieval**: hashing-embedding backend, RRF-fused with FTS5, opt-in with a flag. FTS5 is and remains the default MCP search path.
- **Temporal extraction + Graphiti export**: rule-based claim extraction, valid-from/to lifecycle, supersedes links, and export to a local Graphiti instance. LLM extraction is optional and never on the critical path. Live Neo4j writes are gated on the full Docker + Neo4j profile.
- **Connectors** (Obsidian local + GitHub injected-transport): opt-in, default off. Redaction runs before every ingest. Revoke by disabling in config.

## What 2.0 Does Not Ship Yet

A credible "best in market" claim requires honest scope acknowledgment.

- No packaged LEANN or sentence-transformers dense adapter. The local hashing-embedding dense backend does ship as an opt-in feature. The heavyweight pre-packaged adapter remains roadmap.
- No dense or KG leg inside `mneme_search` by default. MCP search is FTS5 by default. Dense retrieval requires the opt-in flag. KG enrichment is gated for summarize and timeline.
- No cloud SaaS option. mneme is local-first by architectural conviction.
- No web-based knowledge graph visual explorer. Planned.
- No multi-user team features with merge-conflict resolution, per-user ACL, or team dashboards. Read-only shared vaults via git remote work today. Full team support is roadmap.

See `docs/COMPETITIVE.md` for the full landscape and which tools may suit those needs better.

## Documentation

- `docs/ARCHITECTURE.md`: design philosophy and the 12 Architecture Decision Records.
- `docs/CONSTRAINTS.md`: six sacred constraints and how to verify each.
- `docs/VAULT.md`: vault contract, frontmatter specification, atomic write pattern.
- `docs/HOOKS.md`: hook integration guide, timing budgets, fail-soft contract.
- `docs/MCP.md`: tool API reference with JSON schemas and example calls.
- `docs/RELEASE.md`: GitHub tag, release, and metadata checklist.
- `docs/COOKBOOK.md`: ten worked recipes with full Claude Code transcripts.
- `docs/MIGRATION-FROM-CLAUDE-MEM.md`: one-command migration with tri-state archive walkthrough.
- `docs/BENCHMARKS.md`: methodology and the locked baseline numbers.
- `docs/COMPETITIVE.md`: living landscape document (monthly refresh).
- `docs/PRIVACY.md`: outbound network call audit and telemetry policy (zero by default).
- `docs/GOVERNANCE.md`: maintenance model, release authority, succession.

## License

MIT. See `LICENSE`.

## Acknowledgments

Maintained by Onour Impram ([TheGoatPsy](https://github.com/TheGoatPsy)). The Adaptive Context Layer and the pattern and trajectory primitives draw conceptually from token-compression and agent-DB patterns proven in production internal tooling. The architecture is mneme-native, the lineage is operator experience.
