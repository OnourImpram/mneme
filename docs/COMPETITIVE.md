# Competitive Landscape

A living document tracking other memory tools in the Claude Code, MCP, and Python agent ecosystems. Updated monthly. **Last reviewed: 2026-06-02.**

## What Changed in the v1.0 Release Line

- Phase H benchmark suite is in place and published. Concrete numbers in `docs/BENCHMARKS.md`: Benchmark A nDCG@5 = 0.893 on the shipped RRF path with a BoW surrogate, Stop hook p95 = 2 ms, shell_compress 88 percent reduction. Comparing tools without seeded reproducible numbers is now an apples-to-oranges conversation.
- Phase G migration tool ships. Lossless one-command import from claude-mem v13.2.0 with tri-state archive and idempotent re-run. The switching cost from claude-mem to mneme is one command.
- Phase F.6 adds pattern memory and trajectory recorder as vault-markdown primitives. Adds two axes that were previously implicit in the table.

## What Changed in the 2.0 Advanced Line

The 2.0 line builds out the Full and Power profile capabilities as gated, local-first modules. Every one ships with redaction-before-store, provenance, and confidence labels, and none runs on the Stop or critical path.

- **Project graph**: tree-sitter extraction for Python, JavaScript, and TypeScript, plus community detection, PR-impact analysis, and content-hash-discriminated ghost-duplicate detection.
- **Code memory**: AGENTS.md procedural parsing, test-runner output to failure memory, and a fix modelled as a temporal claim that supersedes the failure.
- **Domain modes**: vault-config-loaded user modes with a CLI. Clinical and security-review modes block external extraction and artifact upload, and user config can never weaken a built-in privacy mode or disable redaction.
- **Agent security**: a capability firewall (retrieved or tainted content gets only non-mutating capabilities), data-flow taint tracking, a human-approval gate for durable memory edits, and a poisoned-vault benchmark.
- **Read-only console**: a self-contained, offline, injection-safe HTML audit report. No server, no network.
- **Dense retrieval**: a local-first hashing-embedding backend fused with FTS5 via RRF on a shared document id. Sentence-transformers is an opt-in seam, never a default dependency.
- **Temporal extraction and Graphiti export**: rule-based inferred claim extraction with an optional LLM seam, plus a Graphiti episode bridge.
- **Connectors**: Obsidian (local) and GitHub (injected transport) external sources, default off, redaction-before-ingest, revocation by disabling.

### Benchmark head-to-head

The evaluation harness (`mneme_core.bench.harness`) is shipped. It provides LongMemEval and LoCoMo dataset adapters, a system-versus-system runner over the existing recall, MRR, and nDCG metrics, and a head-to-head comparator. The datasets and the claude-mem retrieval function are supplied by the operator at run time. No head-to-head superiority claim is published here. A public statement that mneme is best or beats another tool requires an operator-run, published benchmark with full experimental controls. The harness measures. It does not assert.

## How to Read This Document

We rate each competitor on the six axes mneme commits to (see `docs/ARCHITECTURE.md`). Numbers are honest assessments, including dimensions where mneme is not the leader.

| Axis | mneme | claude-mem v13.2.0 | mem0 | letta | zep | supermemory | episodic-memory |
|---|---|---|---|---|---|---|---|
| Vault-native transparency | strong (markdown) | weak (SQLite) | weak (vectors) | medium | weak | weak (cloud) | weak |
| Hybrid retrieval depth | strong (FTS5 plus local dense fused via RRF, shipped) | medium (FTS5 OR ChromaDB) | weak (vector only) | medium | strong | strong | weak |
| Zero-LLM-Stop latency | strong (under 1s, seeded p95 ~3 ms) | weak (LLM summarization at session end) | n/a | n/a | n/a | n/a | n/a |
| Privacy redaction | strong (built-in) | absent | absent | absent | absent | absent | absent |
| Temporal reasoning | gated strong (claim lifecycle plus rule/LLM extraction plus Graphiti export, shipped) | absent | weak | medium | strong | weak | absent |
| Adaptive context layer | strong (built-in) | absent | absent | absent | absent | absent | absent |
| Agent security (capability firewall, taint, approval gate) | strong (built-in) | absent | absent | absent | absent | absent | absent |

## Detailed Notes

### claude-mem (v13.2.0)

Mature, well-known, Apache-2.0 licensed (relicensed from AGPL-3.0 in the v13.0 line, confirmed against the installed v13.2.0 package manifest). Strongest competitor on tree-sitter codebase priming, which mneme defers to a separate package at v1.2+. Different design philosophy: SQLite-blob storage, and LLM-based session-end summarization rather than a deterministic append. Users who prefer auto-summarization may stay with claude-mem.

### mem0

Python agent memory library. Vector-only retrieval, no hook layer to compress, no Claude Code plugin. Cloud option exists. Good fit if you need agent SDK integration outside Claude Code.

### letta (formerly MemGPT)

Self-editing agent memory architecture. Lets the agent call `memory_edit()` autonomously. Different scope: agent-managed memory rather than user-managed vault. If your priority is autonomous memory curation by the agent, letta leads.

### zep

Knowledge graph backed memory, cloud product. Strong temporal reasoning. No Claude Code plugin. Good fit for hosted team memory with web-based visual graph exploration.

### supermemory

Cloud-first memory product with browser extension and API. Server-side processing means per-session compression cannot be done by the user.

### episodic-memory

Lightweight episodic memory plugin in the OMC ecosystem. Smaller scope than mneme, no hybrid retrieval, no compression.

## Update Cadence

This document is reviewed monthly. If you maintain a competing tool and our characterization is unfair or outdated, please open an issue. We will correct promptly and credit the correction.

## Where mneme Is Not the Best Fit

- You want auto-summarization at session end and don't mind the 30 to 120 second latency: use claude-mem.
- You need a cloud-hosted shared team memory with a web UI today: use zep or supermemory.
- You want the agent to manage its own memory architecture autonomously: use letta.
- You need tree-sitter codebase priming today (mneme ships this at v1.2+): use claude-mem.
- You are not using Claude Code or an MCP-compatible client at all: use mem0 in your agent stack.
- You need 26 language-tuned observation prompts today (mneme is English-default with Turkish casefold utility, localized modes planned for v1.1): use claude-mem.
- You want a web-based knowledge graph explorer (mneme v1.2 dashboard): use zep cloud.
- You need enterprise team features with merge conflict resolution, per-user access control, and team dashboards (mneme Team v1.0 is post-v1.0): use a hosted product.

In all of those cases, the right tool for the job is not mneme. We list these honestly because long-term credibility beats short-term install count.

## Dimensions Where mneme Clearly Leads

To balance the honest non-fit list, the dimensions where mneme is currently the only or strongest option.

- **Zero LLM cost on Stop with seeded p95 = 2 ms**. Verifiable, not a marketing claim. CI gates the budget at 1000 ms.
- **Markdown vault with `git diff` review**. Every other CC memory tool stores in opaque format.
- **Adaptive Context Layer measured in `benchmarks/cost/`**. No other tool treats token efficiency as a first-class constraint.
- **MIT license, three-tier install with 60-second lite path**. Both mneme and claude-mem are permissively licensed (MIT and Apache-2.0), so the differentiator is the lite install path with zero heavy default dependencies, which matters in constrained or enterprise environments.
- **Public CI regression guards on retrieval quality and latency**. Locked baseline numbers committed to repo.
