# Mneme Ten Loop Engineering Report

**Status:** In progress on `sol-ultra/mneme-ten-loop-upgrade-20260716`.

## Repository identity and change boundary

| Field | Value |
|---|---|
| Repository | `OnourImpram/mneme` |
| Original branch | `main` |
| Starting SHA | `36a2da1dbb713da25852ee6cb77f2b5e0a5798a9` |
| Upgrade branch | `sol-ultra/mneme-ten-loop-upgrade-20260716` |
| Pull request | Draft PR 30 |
| Final SHA | Pending final verification |

No commit in this protocol targets `main` directly. No history rewrite, tag change, release deletion, user-vault operation, credential access, or automatic merge is part of the work.

## Baseline environment

The reproducible baseline was captured by the branch-scoped `engineering-preflight` GitHub Actions job before substantive source changes.

| Component | Baseline value |
|---|---|
| Runner | GitHub-hosted Linux, x86_64 |
| Kernel | Linux 6.17 on Azure |
| Python | 3.12.13 |
| pip | 26.1.2 |
| Node | 22.23.1 |
| npm | 10.9.8 |
| pnpm | 9.15.9 |
| Git | 2.54.0 |
| Tracked files | 449 |
| Version sources | 18, all consistent at 3.5.0 |

The public CI additionally declares Python 3.11 through 3.14 and Node 22 and 24 across Linux, macOS, and Windows. Those matrices are verified by the final branch CI rather than inferred from the Linux preflight.

## Baseline verification

| Command or suite | Result | Evidence |
|---|---:|---|
| `make install-dev` | Pass | 20 s |
| `make test` | Fail | `mneme-graph` native crash, status 139 |
| `make lint` | Pass | 7 s |
| `python tools/version_bump.py --check` | Pass | all 18 sources agree |
| `python tools/spec_verify.py` | Pass | Stop critical-path static invariants |
| `python tools/repo_integrity.py` | Pass | existing release-integrity rules |
| Codex validator | Pass | plugin structure accepted |
| Antigravity validator | Pass | extension structure accepted |
| `mneme-core` tests | Pass | 1467 passed, 1 skipped, 85.77 percent coverage |
| `mneme-cc-plugin` tests | Pass | 217 passed, 81.39 percent coverage |
| `mneme-graph` tests | **Crash** | SIGSEGV or SIGBUS in tree-sitter traversal |
| `mneme-code` tests | Pass | 119 passed, 94.24 percent coverage |
| Cross-package parity | Pass | 52 passed |
| Node build | Pass | TypeScript compiler |
| Node tests | Pass | 473 passed across 26 files |
| Node lint | Pass | Biome, 27 source files |
| `make bench-all` | Pass | all committed synthetic guards passed |

The baseline crash was independently reproduced under Python 3.13 with `tree-sitter 0.26.0`. Downgrading only the binding to 0.25.2 made the same self-analysis test pass, establishing an ABI compatibility fault rather than a flaky Python assertion.

## Baseline benchmark anchors

These values are synthetic, seed-42 regression anchors. They are not evidence of real-world superiority.

| Benchmark | Baseline |
|---|---:|
| Synthetic RRF plus BoW surrogate nDCG at 5 | 0.893413 |
| Synthetic RRF plus BoW surrogate Recall at 10 | 1.000000 |
| Stop-hook proxy p95 | 0.636 ms |
| LongMemEval fixture Recall at 1, 5, and 10 | 1.000000 |
| LongMemEval fixture MRR at 10 | 1.000000 |
| CCE synthetic self-heal recall | 1.000000 |
| CCE synthetic gain over baseline | 0.600000 |

## Repository truth model

A capability is classified as shipped only when an ordinary user can reach it through a documented, packaged, and tested path.

| Capability | Classification at start | Reachable path |
|---|---|---|
| Markdown durable store | Shipped | Python core and hooks write vault markdown |
| FTS5 MCP search | Shipped | install, index, `mneme_search`, TypeScript handler |
| Nine MCP tools | Shipped | MCP `ListTools`, call handler, package binary |
| Claude Code integration | Shipped | six registered hook events, commands, two skills, MCP |
| Codex integration | Shipped | four mapped hooks, two skills, MCP |
| Antigravity integration | Shipped | four mapped hooks, two skills, MCP |
| Generic MCP clients | Shipped, non-native | nine tools, no lifecycle capture |
| Graphiti and Neo4j enrichment | Gated | full profile, credentials, local service |
| Deterministic temporal claim lifecycle | Shipped | Python core CLI and SQLite state |
| Context Continuity Engine | Opt in | config-gated hooks plus two MCP tools |
| Feature-hashed lexical-vector backend | Experimental and disconnected | Python API and benchmark harness only |
| Real semantic embedding backend | Roadmap | adapter protocol only, no packaged model |
| RRF in production MCP search | Not shipped | Python API and synthetic benchmark only |
| Team git synchronization | Opt in | Python CLI, operator-supplied git remote |
| Age encryption | Gated | external `age` executable |
| Local web console | Opt in | loopback-only Python server |

## Master issue ledger

| ID | Loop | Severity | Category | Subsystem | Evidence and impact | Planned or completed correction | Status |
|---|---:|---|---|---|---|---|---|
| MNEME-001 | 1 | P1 | Reliability | mneme-graph | `tree-sitter 0.26.0` terminates graph builds with SIGSEGV or SIGBUS | Constrain ABI, add runtime guard and regression tests | Fixed locally, CI pending |
| MNEME-002 | 1 | P1 | API contract | MCP schemas | Hand-authored `ListTools` schema omits runtime-supported `scope` and canonical memory types | Generate public JSON Schema from the authoritative Zod schema and add contract tests | Open |
| MNEME-003 | 1 | P1 | Isolation | FTS5 | A restricted query against a legacy index silently skips scope filtering | Fail closed and require rebuild, while preserving explicit `scope="*"` | Open |
| MNEME-004 | 1 | P1 | Isolation | Graphiti and Neo4j | Restricted Cypher admits legacy `scope IS NULL` nodes | Exclude unscoped nodes from restricted queries and test query construction | Open |
| MNEME-005 | 1 | P1 | Claim integrity | Retrieval | README advertises an installer flag and an MCP dense path that do not exist | Reclassify feature hashing and RRF as experimental, disconnected surfaces | In progress |
| MNEME-006 | 1 | P2 | CI | TypeScript | Vitest thresholds are declared but `pnpm test` does not activate coverage | Add an explicit coverage script and invoke it in CI | Open |
| MNEME-007 | 1 | P2 | Documentation | Clients and MCP | Six, seven, and nine tool claims coexist. Five and six hook claims coexist | Reconcile all current user surfaces and add integrity checks | In progress |
| MNEME-008 | 1 | P2 | Privacy and legal | Privacy documentation | Categorical GDPR status and file or network claims exceed code evidence | Scope technical claims and remove legal conclusions | Open |
| MNEME-009 | 1 | P2 | Error handling | Graph extraction | Public registry swallows containment violations under a broad `Exception` | Preserve fail-soft parsing while propagating security-boundary violations | Open |
| MNEME-010 | 1 | P2 | Observability | Retrieval telemetry | Backend labels can describe interfaces not executed by the MCP path | Derive provenance only from executed results and document path truth | Open |
| MNEME-011 | 1 | P2 | Benchmark integrity | Evaluation | Headline retrieval numbers use a title-anchored synthetic corpus and BoW surrogate | Label every public use as synthetic and publish ablations and limitations | Open |
| MNEME-012 | 1 | P2 | Supply chain | Packaging | Native tree-sitter dependency had no upper compatibility bound | Pin tested ABI range and add a clean-install smoke gate | Fixed locally, CI pending |

Each resolved entry is updated with its implementation commit, test command, benchmark consequence, compatibility effect, and final status before release review.

## Invariant ledger

| Invariant | Enforcement at start | Protocol action |
|---|---|---|
| Markdown is durable ground truth | Architecture plus storage tests | Preserve |
| Derived indexes are rebuildable | FTS5 and graph rebuild commands | Preserve and test clean rebuild |
| Stop has no LLM dependency | `spec_verify.py`, hook tests | Re-run every loop |
| Stop has no external network dependency | static verifier and architecture | Re-run and qualify documentation |
| Stop remains below 1000 ms p95 | latency guard | Measure before and after |
| Private spans do not enter downstream stores | Python and TypeScript tests | Expand sync and connector adversarial coverage |
| Retrieved content remains untrusted | neutralization and capability firewall tests | Red-team Markdown, KG, and checkpoint paths |
| Paths cannot escape the vault | containment utilities and tests | Audit symlinks, race windows, and error swallowing |
| Restricted reads cannot return another scope | partial tests | Fail closed on legacy or unscoped stores |
| Cross-scope reads require explicit `scope="*"` | tool convention | Make schema visible and add public contract tests |
| Durable autonomous edits require policy | proposal queue and Python drain | Verify direct-write distinction and document authorization boundary |
| Sensitive categories require human approval | policy engine tests | Preserve |
| Autonomous edits are journalled and reversible | journal and rollback tests | Fault-inject and re-run |
| Audit-chain tampering is detectable | HMAC chain tests | Re-run and review key permissions |
| Migration is idempotent and non-destructive by default | migration tests | Package smoke and rollback review |
| Public schemas equal runtime validation | Not enforced | Generate from Zod and test |
| Documentation describes reachable behavior | Drift present | Add repository-integrity checks |
| Benchmarks are reproducible and synthetic labels are explicit | Partial | Correct every headline and retain seed and hardware metadata |
| Optional dependencies remain optional | Package extras | Clean lite install smoke test |
| Core works without cloud accounts | Default architecture | Verify offline and qualify installation-network claims |

## Loop 1. Repository truth and architectural consistency

### Inspection and model

The full tracked repository, package manifests, client manifests, workflows, public commands, design documents, benchmark baselines, and documentation surfaces were inventoried. The authoritative starting state contains four Python distributions, one npm package, three native client integrations, nine MCP tools, six registered Claude Code hook events, four mapped Codex hook events, four mapped Antigravity events, five workflows, 24 public documentation files, eight P2 design documents, and 18 lockstep version sources.

### Findings

The baseline was not green because the graph package crashed in native code. Public descriptions also disagreed on tool count, hook count, retrieval wiring, dense terminology, current package status, and license. The production MCP search path was traced from `ListTools` through Zod validation, the TypeScript handler, read-only FTS5, EvidenceCard construction, telemetry, and the returned result. That path executes FTS5 only. The feature-hashed vector implementation and RRF fusion are currently Python-library and benchmark surfaces, not an installed MCP capability.

### Changes

Current public surfaces are being reconciled to nine tools, six registered Claude Code hook events, and FTS5-only production MCP search. The report, issue ledger, invariant ledger, and truth model establish the evidence base for later loops.

### Verification

`python tools/version_bump.py --check`, `python tools/spec_verify.py`, and `python tools/repo_integrity.py` passed at baseline. Updated integrity gates and all documentation links are re-run at the end of the loop.

## Loop 2. Functional correctness and API contracts

Pending completion.

## Loop 3. Security, privacy, and adversarial vault content

Pending completion.

## Loop 4. Retrieval quality and memory semantics

Pending completion.

## Loop 5. Temporal memory, graph memory, and isolation

Pending completion.

## Loop 6. Memory lifecycle and context continuity

Pending completion.

## Loop 7. Performance, concurrency, resilience, and portability

Pending completion.

## Loop 8. Integration, migration, packaging, and developer experience

Pending completion.

## Loop 9. Competitive capability gap closure

Pending completion.

## Loop 10. Release candidate hardening and independent final review

Pending completion.

## Final verification and release decision

Pending completion. No release-readiness claim is made before the clean final matrix, package builds, installation smoke tests, benchmark guards, and four independent adversarial reviews complete.
