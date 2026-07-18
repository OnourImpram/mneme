# Competitive Landscape

**Release scope:** Mneme 3.6.0 release candidate

**Evidence review date:** 2026-07-18

**Source policy:** Official project documentation, official repositories, and primary papers only.

This document is a deployment and evidence map, not a leaderboard. It separates behavior that is active in a documented open source default from optional open source features, hosted services, research results, and claims that the reviewed sources do not establish.

## Evidence Rules

1. **OSS default** means behavior active in the documented normal open source installation or default constructor. A feature present in source but disabled by default is not counted here.
2. **OSS opt-in** means a documented flag, extra, provider, profile, extension, or alternate self-hosted deployment is required.
3. **Hosted** means a vendor-operated service or cloud control plane. An open source client for a hosted API does not make the service local.
4. **Research** means a paper, benchmark, or reproducible evaluation surface. Vendor-authored results are identified as such and are not treated as independent replication.
5. **Not evidenced** does not mean false. It means the reviewed primary sources do not justify the claim.
6. Missing documentation is not evidence that a privacy, security, retrieval, or lifecycle control is absent.
7. GitHub repository evidence is pinned to the reviewed commit. Relative Mneme links resolve within the commit that contains this document.

## Mneme 3.6.0 Release Position

### OSS Default

Mneme 3.6.0 keeps Markdown files in the user-owned vault as durable ground truth. Derived indexes remain rebuildable. The base retrieval path is FTS5 BM25. The Stop path is deterministic and must not call a network service or an LLM. Deterministic session summaries and the temporal claim lifecycle remain available without a model dependency. See the official [Mneme README](../README.md) and [architecture record](ARCHITECTURE.md).

The 3.6.0 release candidate adopts a deterministic temporal planner. Existing `valid_from`, `valid_to`, and transaction-time `as_of` inputs are applied as query semantics across the temporal claim and graph-enabled paths. Supersession and contradiction visibility are evaluated at the requested point in time. This is an implementation commitment for the 3.6.0 candidate, not a claim that Mneme is categorically better than another temporal system.

### Retrieval Boundary

The base 3.6.0 package does **not** ship a real semantic model. It does not download an embedding model, bundle model weights, or expose feature hashing as a semantic or dense retrieval backend.

The existing feature-hashed vector code is a deterministic lexical-vector experiment used by library tests and synthetic benchmark plumbing. It must be described as **feature-hashed lexical retrieval** or a **synthetic lexical-vector surrogate**. It must not be described as semantic search, dense retrieval, an embedding model, or evidence of semantic equivalence.

A real semantic backend remains outside the 3.6.0 base package until model acquisition, license compatibility, package size, cache location, offline behavior, update policy, and reproducible evaluation are specified and verified. The published Mneme benchmark document already distinguishes its bag-of-words surrogate from a future semantic backend. See [Mneme Benchmarks](BENCHMARKS.md).

### OSS Opt-In

Mneme documents background LLM compression, the Context Continuity Engine, connectors, and Graphiti plus Neo4j enrichment as opt-in surfaces. Graphiti enrichment belongs to the full profile and is not part of default FTS5 search. Optional age encryption applies to self-hosted git sync. These options do not change the base package statement above.

### Hosted

No Mneme-operated managed memory service was evidenced in the reviewed official sources for 3.6.0. The documented team synchronization path is an operator-managed git workflow, not a vendor cloud.

## Deployment Surface Matrix

| Project | OSS default | OSS opt-in or self-hosted | Hosted | Evidence boundary |
|---|---|---|---|---|
| **[Mneme 3.6.0 candidate](../README.md)** | Markdown ground truth, FTS5 BM25, deterministic Stop, deterministic summaries, temporal claims, scope isolation | LLM compression, CCE, connectors, Graphiti plus Neo4j, age-encrypted git sync | None evidenced in the reviewed official sources | No real semantic model in the base package. No head-to-head superiority result was evidenced in the reviewed official sources |
| **[Claude-Mem](https://github.com/thedotmack/claude-mem/blob/f5633c1f84181673896c038cbe285131c6d669a3/README.md)** | Local worker, SQLite plus FTS5, Chroma-backed hybrid retrieval, lifecycle hooks, AI-generated observations and summaries | Alternate provider configuration | Hosted Server is documented as beta. cmem.ai Pro cloud sync is an opt-in hosted service | Official docs document `<private>` storage-exclusion tags. They contradict the old claim that privacy controls are absent |
| **[Mem0](https://github.com/mem0ai/mem0/blob/ddaa655edf41e3ed375b263fb227da0bcd42ccb9/README.md)** | The OSS library defaults to OpenAI `gpt-5-mini`, OpenAI `text-embedding-3-small`, local Qdrant, and SQLite history | Configurable LLMs, embedders, vector stores, rerankers, `mem0ai[nlp]` BM25 plus entity enhancement, self-hosted server with Postgres plus pgvector | Mem0 Platform | Default OSS execution is self-controlled but not model-local because the documented defaults call OpenAI |
| **[Letta Code](https://github.com/letta-ai/letta-code/tree/80e83751f829ca6875066f38718f80e795a65209)** | Runs an agent with memory on the local computer | Alternate model providers | None evidenced for the CLI surface in the reviewed official sources | Letta Code is a local stateful-agent runtime, not only a retrieval component |
| **[Letta Agent SDK](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/README.md#get-started-with-the-letta-agent-sdk)** | The official quickstart selects `backend: "cloud"` | Explicit `backend: "local"` or a self-hosted App Server | Constellation agent cloud | SDK cloud, local subprocess, and self-hosted server paths are distinct deployment surfaces |
| **[Graphiti and Zep](https://github.com/getzep/graphiti/blob/0b4bcf1284ee5fba56b77ed9961568a541e0d418/README.md)** | Graphiti is an OSS temporal context graph with provenance and hybrid graph retrieval. Its quick start requires a graph database and defaults to OpenAI for LLM inference and embeddings | Alternate graph databases, model providers, local OpenAI-compatible endpoints, and the Graphiti MCP server | Zep is the managed context-graph product | Graphiti is Mneme's optional graph dependency, not Mneme's default retrieval engine |
| **[Supermemory](https://github.com/supermemoryai/supermemory/blob/bec73e28ad70dfad717c7a771839aeed27c52af6/README.md)** | The developer quickstart targets the hosted API. No separate zero-selection OSS default was evidenced | `npx supermemory local`, local `Xenova/bge-base-en-v1.5` embeddings by default, optional providers, and fully offline Ollama operation | Supermemory app, API, dashboard, and hosted MCP | Current official docs contradict the old characterization of Supermemory as cloud-only |
| **[episodic-memory](https://github.com/obra/episodic-memory/blob/10757690210574421f1df5f35835af8d0c74d984/README.md)** | Local Transformers.js embeddings, SQLite plus sqlite-vec, transcript sync, and MCP search for Claude Code and Codex | Custom summarization endpoint, explicit plugin hook enablement and trust | None evidenced in the reviewed official repository | It focuses on conversation-history recall. Current official docs contradict the old single-client and no-semantic-search characterization |
| **[claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian/blob/cb93ff6d82f9c35a08bf6010e7fac36dfddc827b/README.md)** | Plain Markdown Obsidian wiki, LLM-driven ingestion and organization, and a hot cache | Hybrid retrieval setup with BM25, optional local Ollama reranking, consent-gated contextual prefixes, optional MCP transport, methodology modes, and DragonScale Memory | None evidenced in the reviewed official repository | A Pro community and private repository mirror are documented, but neither is evidence of a managed memory service. It is a PKM and research-wiki workflow, not the same product category as an agent-memory SDK |

## Documented Tradeoffs

### Mneme

Mneme provides inspectable Markdown ground truth, a deterministic no-network Stop path, explicit scope isolation, and rebuildable derived state. Its default retrieval capability is lexical. A production semantic model is outside the 3.6.0 base release surface.

### Claude-Mem

Claude-Mem provides an observation-generation pipeline around Claude Code, a local worker and web viewer, SQLite and FTS5 storage, and Chroma hybrid retrieval. Its summaries and observations are AI-generated. It also documents storage-exclusion tags and early hosted paths. This differs from Mneme's deterministic base path.

### Mem0

Mem0 offers an application library, a self-hosted server, and a managed platform. Its default OSS constructor uses hosted OpenAI inference and embeddings even though storage defaults are local. Its documented surface is an application memory SDK with configurable providers and a managed platform path, rather than a coding-agent Markdown vault.

### Letta

Letta treats memory as part of persistent agent state. Its documented model allows agents to modify memory blocks and supports local, self-hosted, and cloud execution. The unit of deployment is a stateful agent runtime rather than only a memory sidecar.

### Graphiti and Zep

Graphiti provides a dedicated temporal graph engine with source episodes, validity windows, provenance, and hybrid graph retrieval. Its quick start requires a graph database plus model inference and embeddings. Mneme's base FTS5 path does not. Zep provides the managed counterpart. Mneme 3.6.0 uses Graphiti only as an explicit optional enrichment path.

### Supermemory

Supermemory documents both a hosted API and a separately invoked local runtime with real local embeddings. Its API covers memory, RAG, profiles, connectors, and file processing. Its published benchmark rankings remain vendor claims until reproduced under shared controls.

### episodic-memory

episodic-memory focuses on semantic recall over prior Claude Code and Codex conversations. It uses a real local embedding model through Transformers.js and stores vectors in sqlite-vec. Its documented retrieval corpus is conversation history rather than a general curated Markdown vault.

### claude-obsidian

claude-obsidian is an Obsidian-first, LLM-maintained research wiki. It offers Markdown ownership, autonomous filing, PKM methodology modes, and a visual knowledge workflow. Mneme documents an agent-memory substrate. The systems overlap on Markdown knowledge retention but expose different workflow and deployment boundaries.

## Research Surface

### Benchmark Definitions

[LongMemEval](https://proceedings.iclr.cc/paper_files/paper/2025/file/d813d324dbf0598bbdc9c8e79740ed01-Paper-Conference.pdf), published at ICLR 2025, evaluates information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention over long interaction histories. [LoCoMo](https://aclanthology.org/2024.acl-long.747/), published at ACL 2024, evaluates very long-term conversational memory across question answering, event summarization, and multimodal dialogue generation.

These benchmarks are not interchangeable with a retrieval-only test. End-to-end results depend on indexing, retrieval, reading or generation, model choice, prompt, judge, corpus version, and temporal treatment. A result is comparable only when these controls are disclosed.

### Project-Authored Papers and Evaluations

No independent reproduction is evidenced for the project-specific results below.

| Project | Evidence type | Official source | Supported statement | Boundary |
|---|---|---|---|---|
| MemGPT, historical predecessor to Letta | Project-authored preprint | [MemGPT preprint v2](https://arxiv.org/abs/2310.08560v2) | Introduces virtual context management and evaluates long-document and multi-session chat use cases | It does not establish current Letta product superiority across present-day systems |
| Mem0 | Project-authored preprint | [Mem0 preprint v1](https://arxiv.org/abs/2504.19413v1) | Reports LoCoMo results, latency, and token-cost comparisons for the authors' evaluated configurations | It is not independent replication of the current OSS defaults or hosted platform |
| Zep | Project-authored preprint | [Zep preprint v1](https://arxiv.org/abs/2501.13956v1) | Reports temporal-graph architecture and author-run DMR and LongMemEval comparisons | It is not an independent leaderboard for Graphiti, Zep, and Mneme defaults |
| Supermemory | Vendor benchmark claim | [Official repository benchmark section](https://github.com/supermemoryai/supermemory/blob/bec73e28ad70dfad717c7a771839aeed27c52af6/README.md#benchmarks) | Publishes vendor results and links an open benchmark harness | The claimed number-one rank is not carried into this document without a shared, reproducible run |
| Mneme | Internal synthetic evaluation documentation | [Mneme benchmark documentation](BENCHMARKS.md) | Publishes internal retrieval, latency, cost, migration, and adapter surfaces, with synthetic conditions labeled | It does not publish a controlled head-to-head result that establishes superiority over another product |

## Not Evidenced

The following claims from earlier revisions are removed or deliberately not repeated because current primary sources do not establish them:

1. Mneme is the only, strongest, or clear leader on any market-wide dimension.
2. Every other Claude Code memory tool stores data in an opaque format.
3. Competitors have no privacy controls, redaction, approval boundary, audit mechanism, or temporal behavior. Proving absence requires a scoped code and configuration audit, not a missing README sentence.
4. Mneme is the only default-on deterministic summarizer, the only memory-blame implementation, or the only redaction-before-share design.
5. Claude-Mem always takes 30 to 120 seconds at session end.

## Contradicted by Current Official Sources

1. Claude-Mem has no privacy exclusion or is limited to one retrieval mode. Its docs describe `<private>` storage-exclusion tags and hybrid retrieval.
2. Mem0 is vector-only. Its official documentation describes optional BM25 and entity-enhanced hybrid search.
3. Supermemory is cloud-only or prevents operator-controlled local processing. Its current official repository documents a local runtime and offline configuration.
4. episodic-memory has no semantic retrieval or supports only Claude Code. Its official repository documents local vector search and Codex support.
5. claude-obsidian targets only Claude Code. Its current README documents multiple model and client surfaces.

## Editorial and Implementation Boundaries

1. Old star counts, language counts, version pins, and adoption ranks are omitted unless they are necessary for an architectural decision.
2. Cross-project latency, retrieval-quality, privacy, and security rankings are omitted when they are assembled from non-equivalent vendor benchmarks.
3. Feature hashing in Mneme is never described as semantic, dense, or an embedding model.

## Sources

All sources below were accessed on **2026-07-18**.

### Mneme

1. Official repository README: [README](../README.md)
2. Official architecture record: [ARCHITECTURE](ARCHITECTURE.md)
3. Official benchmark record: [BENCHMARKS](BENCHMARKS.md)

### Claude-Mem

1. Official repository README at `f5633c1f84181673896c038cbe285131c6d669a3`: <https://github.com/thedotmack/claude-mem/blob/f5633c1f84181673896c038cbe285131c6d669a3/README.md>
2. Official database architecture: <https://docs.claude-mem.ai/architecture/database>
3. Official search architecture: <https://docs.claude-mem.ai/architecture/search-architecture>
4. Official private tags documentation: <https://docs.claude-mem.ai/usage/private-tags>
5. Official hosted server beta documentation: <https://docs.claude-mem.ai/hosted-server>
6. Official cloud sync documentation: <https://docs.claude-mem.ai/cloud-sync>

### Mem0

1. Official OSS overview and defaults: <https://docs.mem0.ai/open-source/overview>
2. Official repository README at `ddaa655edf41e3ed375b263fb227da0bcd42ccb9`: <https://github.com/mem0ai/mem0/blob/ddaa655edf41e3ed375b263fb227da0bcd42ccb9/README.md>
3. Project-authored research preprint v1: <https://arxiv.org/abs/2504.19413v1>

### Letta

1. Official repository README at `b76da9092518cbaa2d09042e52fdcbde69243e18`: <https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/README.md>
2. Official Letta Code repository at `80e83751f829ca6875066f38718f80e795a65209`: <https://github.com/letta-ai/letta-code/tree/80e83751f829ca6875066f38718f80e795a65209>
3. Official stateful-agent concepts: <https://docs.letta.com/guides/core-concepts/stateful-agents>
4. Official App Server documentation: <https://docs.letta.com/letta-agent/app-server>
5. Official Constellation documentation: <https://docs.letta.com/letta-agent/constellation>
6. MemGPT research preprint v2: <https://arxiv.org/abs/2310.08560v2>

### Graphiti and Zep

1. Official Graphiti repository README at `0b4bcf1284ee5fba56b77ed9961568a541e0d418`: <https://github.com/getzep/graphiti/blob/0b4bcf1284ee5fba56b77ed9961568a541e0d418/README.md>
2. Official Zep concepts documentation: <https://help.getzep.com/concepts>
3. Project-authored Zep research preprint v1: <https://arxiv.org/abs/2501.13956v1>

### Supermemory

1. Official repository README at `bec73e28ad70dfad717c7a771839aeed27c52af6`: <https://github.com/supermemoryai/supermemory/blob/bec73e28ad70dfad717c7a771839aeed27c52af6/README.md>
2. Official self-hosting documentation: <https://supermemory.ai/docs/self-hosting/overview>

### episodic-memory

1. Official repository README at `10757690210574421f1df5f35835af8d0c74d984`: <https://github.com/obra/episodic-memory/blob/10757690210574421f1df5f35835af8d0c74d984/README.md>

### claude-obsidian

1. Official repository README at `cb93ff6d82f9c35a08bf6010e7fac36dfddc827b`: <https://github.com/AgriciDaniel/claude-obsidian/blob/cb93ff6d82f9c35a08bf6010e7fac36dfddc827b/README.md>

### Benchmarks

1. LongMemEval, ICLR 2025 paper: <https://proceedings.iclr.cc/paper_files/paper/2025/file/d813d324dbf0598bbdc9c8e79740ed01-Paper-Conference.pdf>
2. LongMemEval official repository at `9e0b455f4ef0e2ab8f2e582289761153549043fc`: <https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc>
3. LoCoMo, ACL 2024 paper: <https://aclanthology.org/2024.acl-long.747/>
4. LoCoMo official repository at `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`: <https://github.com/snap-research/locomo/tree/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376>

## Maintenance Rule

Update a row only from a current primary source. Record the access date. Preserve deployment-surface distinctions. Do not convert a vendor benchmark into a categorical market claim. When evidence is missing, use `not evidenced` instead of inferring absence. GitHub evidence must be commit-pinned. Official documentation pages without versioned URLs remain mutable and are qualified by the access date rather than treated as immutable snapshots.
