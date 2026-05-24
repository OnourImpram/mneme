# Competitive Landscape

A living document tracking other memory tools in the Claude Code, MCP, and Python agent ecosystems. Updated monthly. **Last reviewed: 2026-05-19.**

## What Changed in the v1.0 Release Line

- Phase H benchmark suite is in place and published. Concrete numbers in `docs/BENCHMARKS.md`: nDCG@5 = 0.893 (RRF fused), Stop hook p95 = 2 ms, shell_compress 88 percent reduction. Comparing tools without seeded reproducible numbers is now an apples-to-oranges conversation.
- Phase G migration tool ships. Lossless one-command import from claude-mem v13.2.0 with tri-state archive and idempotent re-run. The switching cost from claude-mem to mneme is one command.
- Phase F.6 adds pattern memory and trajectory recorder as vault-markdown primitives. Adds two axes that were previously implicit in the table.

## How to Read This Document

We rate each competitor on the six axes mneme commits to (see `docs/ARCHITECTURE.md`). Numbers are honest assessments, including dimensions where mneme is not the leader.

| Axis | mneme | claude-mem v13.2.0 | mem0 | letta | zep | supermemory | episodic-memory |
|---|---|---|---|---|---|---|---|
| Vault-native transparency | strong (markdown) | weak (SQLite) | weak (vectors) | medium | weak | weak (cloud) | weak |
| Hybrid retrieval depth | strong (RRF fused) | medium (FTS5 OR ChromaDB) | weak (vector only) | medium | strong | strong | weak |
| Zero-LLM-Stop latency | strong (under 1s) | weak (120s timeout) | n/a | n/a | n/a | n/a | n/a |
| Privacy redaction | strong (built-in) | absent | absent | absent | absent | absent | absent |
| Temporal reasoning | strong (Graphiti) | absent | weak | medium | strong | weak | absent |
| Adaptive context layer | strong (built-in) | absent | absent | absent | absent | absent | absent |

## Detailed Notes

### claude-mem (v13.2.0)

Mature, well-known, AGPL-3.0 licensed. Strongest competitor on tree-sitter codebase priming, which mneme defers to a separate package at v1.2+. Different design philosophy: SQLite-blob storage, LLM-on-Stop with 120s timeout. Users who prefer auto-summarization and don't mind the latency may stay with claude-mem.

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
- **MIT license, three-tier install with 60-second lite path**. claude-mem's AGPL-3.0 and heavy default dependencies are real enterprise blockers.
- **Public CI regression guards on retrieval quality and latency**. Locked baseline numbers committed to repo.
