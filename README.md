<p align="center">
  <img src="assets/mneme-banner.jpg" alt="mneme — vault-native memory for Claude Code" width="100%">
</p>

# mneme

> Vault-native memory for Claude Code. Markdown is ground truth.

FTS5 retrieval, RRF-ready core, gated temporal knowledge graph, zero LLM cost on Stop, token-aware adaptive context budget.

**Status**: v1.0.1 repo hardening. Public package, plugin, runtime, and documentation version sources are aligned for the next GitHub release gate.

## Why mneme

Most Claude Code memory plugins store your conversation history in opaque SQLite blobs and call an LLM every time you finish a session. mneme takes the opposite stance.

- **Markdown is ground truth.** Your vault is a directory of plain `.md` files you can `git diff`, `grep`, edit, and back up.
- **No LLM on the critical path.** The Stop hook appends deterministically. Compression happens in the background, opt-in, with a cost cap.
- **Hybrid-ready retrieval, not just vector search.** Shipped v1.0 uses FTS5 BM25 in the MCP server and exposes the RRF fusion protocol in `mneme-core`. Full-profile knowledge graph enrichment is gated by the local Graphiti and Neo4j setup. The packaged LEANN dense adapter is roadmap, not part of the v1.0 shipped default.
- **Token-efficient by architecture.** Shell output compression, injection deduplication, adaptive top-k, and three injection format levels save 40 to 60 percent on session token consumption.
- **Privacy by default.** Inline `<private>` tag redaction at staging write with SHA256 audit log. Zero outbound network calls except opted-in compression LLM and optional local Neo4j.
- **Temporal reasoning.** Gated full-profile Graphiti support can enrich summarize and timeline queries when the KG active flag and local Neo4j are present. Lite installs fall back to FTS5 and mtime ordering.
- **Pattern and trajectory memory.** First-class vault-markdown primitives for Signal/Action/Outcome patterns and per-session step recorders, queryable via the same retrieval pipeline.

## Reproducible Numbers

These come from the in-repo benchmark suite, seeded with `MNEME_BENCH_SEED=42`. Benchmark A uses a 500-document corpus. Benchmark E uses its default 300-document, 30-query fixture. Reproduce with `make bench-all`.

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

# Standard: lite + optional ONNX runtime slot and RRF extension points.
# Packaged LEANN dense retrieval is roadmap, not shipped in v1.0.
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

## What v1.0 Ships

- 6 MCP tools: `mneme_search`, `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`, `mneme_timeline`. Shipped default search is FTS5. Full-profile summarize and timeline can add KG fields when the local graph is active.
- 5 Claude Code hooks: `PostToolUse`, `SessionStart`, `Stop`, `PreCompact`, `SessionEnd`.
- 3 slash commands: `/mneme:prime`, `/mneme:recall`, `/mneme:migrate`.
- 2 skills: `mneme-prime`, `mneme-search`.
- 5-benchmark suite (`make bench-all`) including a head-to-head adapter for claude-mem v13.2.0.
- One-command migration: `mneme-migrate migrate-from-claude-mem` with tri-state archive flag and idempotent re-run.
- Adaptive Context Layer: `distill.shell_compress`, `distill.injection_dedup`, `distill.adaptive_topk`, `distill.compressed_format`, plus `mneme audit` for token reports and `mneme audit-log` for redaction audit entries.
- Pattern memory: `mneme patterns {store, search, list, show, delete}` writing vault-markdown Signal/Action/Outcome documents.
- Trajectory recorder: `mneme trajectory {start, step, end, show, list}` capturing per-session decision trails under `vault/trajectories/`.
- Background AI compression (opt-in, default off): `mneme compress {enable, disable, status, dry-run, run}` with monthly cost cap ledger.

## What v1.0 Does Not Ship Yet

A credible "best in market" claim requires honest scope acknowledgment.

- No tree-sitter codebase priming. Planned for v1.2 as a separate `mneme-code` package.
- No packaged LEANN dense adapter in the v1.0 install path. The RRF protocol and benchmark surrogate are shipped. The real dense adapter remains roadmap.
- No dense or KG leg inside `mneme_search` by default. MCP search is FTS5 in v1.0. KG enrichment is gated for summarize and timeline.
- No localized observation modes. English-default at v1.0, Turkish casefold is a utility not a mode preset.
- No cloud SaaS option. mneme is local-first by architectural conviction.
- No web-based knowledge graph visual explorer. Planned for v1.2 dashboard.
- No multi-user team features with merge conflict resolution. Read-only shared vaults via git remote work, full team support arrives at Team v1.0.

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
