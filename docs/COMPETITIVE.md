# Competitive Landscape

A living document tracking other memory tools in the Claude Code, MCP, and Python agent ecosystems. Updated monthly. **Last reviewed: 2026-06-12.**

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

## What Changed in the 3.0 Line

The 3.0 line closes the remaining capability-matrix gaps without giving up the local-first, zero-LLM-Stop identity, and relicenses the project to Apache-2.0 (published 1.x/2.x artifacts remain MIT).

- **Deterministic session summaries, on by default**: every session-log entry now carries an extractive zero-LLM summary (files touched, tool activity, opening intent) computed from already-redacted staging records. No key, no latency, no surprise cost. The opt-in LLM compression pipeline is unchanged and now has localized rubric presets (English and Turkish).
- **Temporal lifecycle de-gated + memory blame**: the deterministic claim lifecycle (valid-from/to, supersedes, as-of, contradictions) is built in on every profile, and `temporal blame` answers "where did this knowledge come from, what replaced it" - git-blame for memories.
- **Policy-graduated autonomy**: the agent can apply low-risk mechanical edits (dedup, typo, tag-normalize, supersede-link, stale-archive) autonomously when the operator's `policy.json` allows the class - every change journalled for one-command rollback and chained into the tamper-evident HMAC audit log. Durable categories never apply autonomously.
- **Self-hosted team memory**: `mneme-core sync` shares the vault over any plain git remote with redaction-before-share (a surviving private span aborts the push), optional age end-to-end encryption, and a never-overwrite conflict-sidecar merge policy. No vendor, no cloud account.
- **Local web console**: `mneme-console --serve` runs a loopback-only, read-only explorer (audit, graph view, claims with supersedes chains, autonomous-edit journal, audit-chain verification) with zero new dependencies.
- **PR-impact analysis**: `mneme-graph impact --diff` reports the nodes and files that transitively depend on a changeset, with external ghost nodes resolved onto their local definitions at query time.

## How to Read This Document

We rate each competitor on the six axes mneme commits to (see `docs/ARCHITECTURE.md`). Numbers are honest assessments, including dimensions where mneme is not the leader.

| Axis | mneme | claude-mem v13.2.0 | mem0 | letta | zep | supermemory | episodic-memory |
|---|---|---|---|---|---|---|---|
| Vault-native transparency | strong (markdown) | weak (SQLite) | weak (vectors) | medium | weak | weak (cloud) | weak |
| Hybrid retrieval depth | strong (FTS5 plus local dense fused via RRF, shipped) | medium (FTS5 OR ChromaDB) | weak (vector only) | medium | strong | strong | weak |
| Zero-LLM-Stop latency | strong (under 1s, seeded p95 ~3 ms) | weak (LLM summarization at session end) | n/a | n/a | n/a | n/a | n/a |
| Privacy redaction | strong (built-in) | absent | absent | absent | absent | absent | absent |
| Temporal reasoning | strong (built-in claim lifecycle, blame/as-of; Graphiti export gated) | absent | weak | medium | strong | weak | absent |
| Adaptive context layer | strong (built-in) | absent | absent | absent | absent | absent | absent |
| Agent security (capability firewall, taint, approval gate) | strong (built-in) | absent | absent | absent | absent | absent | absent |

## Detailed Notes

### claude-mem (v13.2.0)

Mature, well-known, Apache-2.0 licensed (relicensed from AGPL-3.0 in the v13.0 line, confirmed against the installed v13.2.0 package manifest). Strongest competitor on tree-sitter codebase priming, which mneme defers to a separate package at v1.2+. Different design philosophy: SQLite-blob storage, and LLM-based session-end summarization. mneme 3.0 ships deterministic extractive summaries on by default (zero LLM, zero latency cost) with LLM compression as the opt-in richer layer; users who specifically want LLM-written prose summaries by default may still prefer claude-mem.

### mem0

Python agent memory library. Vector-only retrieval, no hook layer to compress, no Claude Code plugin. Cloud option exists. Good fit if you need agent SDK integration outside Claude Code.

### letta (formerly MemGPT)

Self-editing agent memory architecture. Lets the agent call `memory_edit()` autonomously with no policy boundary. mneme 3.0 matches the autonomy for operator-allowed low-risk edit classes (policy-graduated, journalled, rollback-able, audit-chained); letta still leads if you want unconstrained agent self-editing across all memory categories.

### zep

Knowledge graph backed memory, cloud product. Strong temporal reasoning. No Claude Code plugin. Good fit for vendor-hosted team memory with a managed web UI; mneme 3.0 covers the same need self-hosted (git-remote sync plus a localhost console).

### supermemory

Cloud-first memory product with browser extension and API. Server-side processing means per-session compression cannot be done by the user.

### episodic-memory

Lightweight episodic memory plugin in the OMC ecosystem. Smaller scope than mneme, no hybrid retrieval, no compression.

## Update Cadence

This document is reviewed monthly. If you maintain a competing tool and our characterization is unfair or outdated, please open an issue. We will correct promptly and credit the correction.

## Where mneme Is Not the Best Fit

- You want LLM-written prose summaries at session end by default and don't mind the 30 to 120 second latency: use claude-mem (mneme's default summary is deterministic extractive; its LLM compression is opt-in).
- You want a vendor-MANAGED team memory where someone else runs the server, the auth, and the backups: use zep or supermemory (mneme's team sync is self-hosted by design).
- You want the agent to rewrite any memory category without a policy boundary: use letta (mneme's autonomy is policy-graduated and never touches durable categories without a human).
- You are not using Claude Code or an MCP-compatible client at all: use mem0 in your agent stack.
- You need 26 language-tuned observation prompts today (mneme ships English and Turkish presets; more land on demand): use claude-mem.
- You need per-user access control and team dashboards on shared memory: use a hosted product (mneme sync has no ACL layer; everyone with the remote sees the shared redacted trees).

In all of those cases, the right tool for the job is not mneme. We list these honestly because long-term credibility beats short-term install count.

## Dimensions Where mneme Clearly Leads

To balance the honest non-fit list, the dimensions where mneme is currently the only or strongest option.

- **Zero LLM cost on Stop with seeded p95 = 2 ms**. Verifiable, not a marketing claim. CI gates the budget at 1000 ms.
- **Markdown vault with `git diff` review**. Every other CC memory tool stores in opaque format.
- **Adaptive Context Layer measured in `benchmarks/cost/`**. No other tool treats token efficiency as a first-class constraint.
- **Apache-2.0 license, three-tier install with 60-second lite path**. Both mneme and claude-mem are permissively licensed (both Apache-2.0), so the differentiator is the lite install path with zero heavy default dependencies, which matters in constrained or enterprise environments.
- **Public CI regression guards on retrieval quality and latency**. Locked baseline numbers committed to repo.
- **Deterministic zero-LLM session distillation, on by default**. Every session gets a summary with no key, no cost, and no latency; no other tool ships a default-on summarizer that never calls a model.
- **Memory blame and as-of time-travel**. `temporal blame` reconstructs where a piece of knowledge came from, what it superseded, and what replaced it; no competitor exposes provenance lineage as a first-class query.
- **Policy-graduated accountable autonomy**. Agent self-editing inside an operator-declared policy, with a rollback journal and a tamper-evident HMAC audit chain shared across the Python and TypeScript writers.
- **Redaction-before-share team sync**. The only team-memory path in the set where a privacy redactor runs on every shared file and a surviving private span aborts the push, with optional age end-to-end encryption so even the remote host sees no plaintext.
