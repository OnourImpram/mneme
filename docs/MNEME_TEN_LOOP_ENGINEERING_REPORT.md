# Mneme 3.6.0 Ten Loop Engineering Report

**Status:** In progress.

**Technical decision:** `NO GO` until the release candidate passes the complete GitHub matrix and `release.yml` dry run. The full local gate set passes after commit partitioning, but remote platform evidence remains mandatory.

## Execution identity

| Field | Current value |
|---|---|
| Repository | `OnourImpram/mneme` |
| Starting branch | `main` |
| Starting SHA | `02641d9c59c354c53733cc065c081fdd9d25836b` |
| Upgrade branch | `sol-ultra/mneme-ten-loop-upgrade-20260717-r3` |
| Started | 2026-07-18, Europe/Istanbul |
| Latest published release at start | `v3.5.0` |
| Target | One unmerged 3.6.0 release candidate PR |
| Explicitly excluded | Merge, tag, package publish, release publish, history rewrite, user vault mutation |

This run replaces the stale PR 30 report that was present on the starting tree. Evidence from that older branch is not treated as evidence for this release candidate unless reproduced on this branch.

## Baseline truth

The branch was created from a clean fresh checkout of the exact starting SHA. The target branch did not exist remotely before this run. The normal release workflow was retained. Four temporary 3.6.0 workflows and three ready markers were present on the starting tree and are removed in Loop 1.

At start, the latest release was `v3.5.0`. Pull requests 28, 29, 31, 32, 33, 34, and 35 were open. PR 31 was conflicting and is used only as a hunk-level donor after local tests. PR 35 remains a separate tree-sitter dependency failure lane. Its native crash boundary is now reproduced and recorded below, but the PR remains open until the release-candidate delivery phase.

The primary GitHub CI run was red across the declared Python and Node matrix. The local starting tree also contained stale temporary release automation and did not provide a complete ten-loop evidence report.

## Local environment

| Component | Baseline value |
|---|---|
| Operating system | Windows, PowerShell |
| Python | 3.14.3 |
| Node | 24.14.0 |
| pnpm | 9.15.9 |
| Git | 2.53.0.windows.1 |
| GitHub CLI | 2.88.1 |
| GNU Make | Not installed locally |
| Docker CLI | Available, daemon unavailable during initial Neo4j check |
| Python packages | Editable installs in branch-local `.venv` |
| Node packages | Installed from frozen lockfile |

## Baseline command record

| Command or gate | Baseline result | Current branch evidence |
|---|---:|---|
| Python core Ruff | Pass | Re-run after each Python change |
| Python core strict mypy | Pass | Re-run after each Python change |
| TypeScript build | Pass | Re-run after each TypeScript change |
| TypeScript Biome | Fail at start | Full source and test tree clean before commit partitioning |
| Python core tests | Two environment-bound failures at start | 1664 passed, 14 skipped, 84 percent coverage |
| MCP tests | Four environment-bound failures at start | 616 passed, 5 skipped. Statements 87.75 percent, branches 80.94 percent, functions 93.15 percent, lines 88.77 percent |
| `spec_verify.py` | Pass | Pass, 6 hooks checked against 7 forbidden roots |
| `repo_integrity.py` | Pass before strengthened rules | Strengthened rules pass locally |
| Plugin validators | Existing validators pass | Claude, Codex, and Antigravity validators pass |
| Version sources | 18 sources at 3.5.0 | All 18 sources agree on 3.6.0 |

## Invariant ledger

| Invariant | Enforcement state |
|---|---|
| Markdown remains durable ground truth | Preserved. Derived index rebuilds do not rewrite ground truth |
| Concrete scope reads never widen on legacy derived data | Implemented and verified for Python FTS5, Node prime, and CCE |
| Cross-scope reads require exact explicit `*` | Shared Python and TypeScript scope contracts implemented |
| Durable writes reject `*` | Write, proposal, queue, checkpoint, temporal export, and KG staging paths reject it |
| Private spans are redacted before every durable or provider sink | Compression, FTS5, telemetry, connector, sync, KG, Graphiti, and proposal paths covered |
| Vault writes fail closed on symlink, reparse, and parent identity changes | Guarded Python and TypeScript atomic writes implemented with adversarial tests |
| Python and TypeScript serialize audit and proposal writes | Shared O_EXCL lock paths, sequence, and keyed seal verified across both runtimes |
| Audit tail truncation is detectable | Cross-language append and truncation fixture verifies the shared keyed daily seal |
| Rollback requires current hash equality | Implemented and regression tested |
| Stop performs no network or LLM calls | Runtime spies reject socket, HTTP, provider, and compression calls. The full-profile test passed |
| Stop p95 is less than 1000 ms | Full handler over real temporary files remains below budget. Exact candidate Stop proxy p95 is 4.704 ms |
| Temporal visibility is deterministic and scope isolated | Implemented for SQLite claims and Graphiti query planning. Live Neo4j service proof pending |
| Documentation describes reachable behavior only | Capability classes, temporal behavior, lexical surrogate limits, and package boundaries independently reviewed |
| No tag, release, publish, or merge occurs in this run | Enforced by execution boundary |

## Issue ledger

| ID | Severity | Loop | Finding | Current disposition |
|---|---:|---:|---|---|
| MNEME-R3-001 | P1 | 1 | Temporary release workflows and ready markers made repository truth invalid | Removed. Actionlint 1.7.7 and strengthened integrity gates pass after commit partitioning |
| MNEME-R3-002 | P1 | 2 | Public MCP schemas could drift from runtime Zod behavior | Registry now derives Draft 7 schemas from Zod and tests all nine tools |
| MNEME-R3-003 | P1 | 2 | CCE read tools lacked scope contracts | Optional scope and legacy default-only rules implemented |
| MNEME-R3-004 | P1 | 3 | Atomic writes did not fully guard parent replacement races | Descriptor and parent identity checks added in Python and TypeScript |
| MNEME-R3-005 | P1 | 3 | Proposal queue append and drain could lose concurrent records | Shared lock, bounded append, atomic claim, and preservation archives added |
| MNEME-R3-006 | P1 | 3 | Audit truncation at the tail was not detectable | Sequence and keyed daily seal added |
| MNEME-R3-007 | P1 | 3 | Stale rollback could overwrite a newer file | Current hash comparison required before rollback |
| MNEME-R3-008 | P1 | 4 | Concrete-scope FTS5 queries trusted legacy unscoped indexes | They now fail closed and require rebuild. Exact `*` remains explicit |
| MNEME-R3-009 | P2 | 4 | Retrieval telemetry conflated attempted and contributing backends | Four backend states implemented |
| MNEME-R3-010 | P1 | 5 | Graph reads had scope clauses without a bound live ingestion group | KG staging and worker now bind deterministic Graphiti `group_id` |
| MNEME-R3-011 | P1 | 5 | `as_of_applied` could describe configuration rather than a successful snapshot query | It is true only after a successful transaction-time graph query |
| MNEME-R3-012 | P1 | 6 | Checkpoint rehydration could select another scope and was injected outside the untrusted-memory fence | Scope-aware bounded lookup and untrusted wrapping implemented |
| MNEME-R3-013 | P2 | 6 | Capture and audit failures could be silent | Closed. Failure visibility and accountable failure paths pass lifecycle and privacy review |
| MNEME-R3-014 | P2 | 7 | Local Docker daemon unavailable for live Neo4j proof | CI service proof remains required |
| MNEME-R3-015 | P2 | 8 | Clean artifact and migration rollback proof was incomplete | Closed. Final 3.6.0 verifier passed 21 required checks across 12 artifacts |
| MNEME-R3-016 | P2 | 9 | Competitive and semantic capability claims require official-source correction | Closed. Official-source classes, immutable repository links, evidence types, and terminology pass OSS maintainer re-review |
| MNEME-R3-017 | P1 | 7 | Expanding the tree-sitter upper bound to 0.26 admits a native ABI access violation during full graph extraction | Windows exit `0xC0000005` reproduced with the runtime guard intentionally bypassed. The `<0.26` metadata bound and pre-parse guard remain enforced. PR 35 closure pending |
| MNEME-R3-018 | P1 | 10 | A manual release workflow dispatch could reach publish jobs without a tag-push event | Resolve and all seven publish jobs now require a tag push. Independent release review found no remaining P0 or P1 |
| MNEME-R3-019 | P1 | 4 | Python retrieval could widen concrete legacy reads, use an unkeyed query digest, and lose structured RRF fields | Concrete scopes fail closed, per-vault HMAC is used, and canonical dedup preserves hash, trust, confidence, and provenance |
| MNEME-R3-020 | P1 | 4 | Node prime did not enforce the same legacy scope and Turkish normalization rules as search | Concrete legacy reads now fail closed and prime queries both CLDR and ASCII Turkish forms |
| MNEME-R3-021 | P1 | 5 | Temporal current-time evaluation drifted across calls and stale or invalid derived claims remained queryable | One UTC snapshot is reused, invalid windows are rejected, and stale claims are pruned deterministically |
| MNEME-R3-022 | P1 | 5 | Temporal ambiguity and transaction-time provenance were inconsistent across paths | Ambiguity is canonicalized and observation time is no longer inferred from valid time |
| MNEME-R3-023 | P1 | 3 | Private mapping keys could reach compression staging, audit field paths, and KG records | Recursive key redaction with deterministic collision suffixes is applied before every sink |
| MNEME-R3-024 | P1 | 3 | A TypeScript audit suffix did not advance the shared seal and could be truncated undetected | Both runtimes now advance one sequence and seal. Partial writes restore chain and seal snapshots |
| MNEME-R3-025 | P1 | 3 | Python telemetry used an unkeyed query digest | A separate 32-byte per-vault HMAC key is created with exclusive, symlink-rejecting semantics |
| MNEME-R3-026 | P1 | 8 | Migration metadata could bypass redaction before hashes, frontmatter, tags, and body generation | Every observation string column is redacted before derivation. Migration revision 3 preserves rollback compatibility |
| MNEME-R3-027 | P2 | 7 | Refused console POST requests intermittently reset on Windows when the body was left unread | The handler discards at most 1 MiB before `405`. The 21-case file passed five consecutive runs, 105 tests total |
| MNEME-R3-028 | P1 | 9 | Benchmark A treated duplicate logical documents as distinct and overstated fused nDCG | Metrics now use canonical document identities. Production FTS5 is the headline and the lexical surrogate underperforms it |

## Loop scorecard

| Loop | Status | Evidence summary |
|---:|---|---|
| 1 | Locally complete | Repository recovery, actionlint 1.7.7, version, license, Node, tool, and manifest gates pass after commit partitioning. Remote CI pending |
| 2 | Locally complete | Shared scope contracts and nine-tool schema/runtime parity pass |
| 3 | Locally complete | Atomic writes, cross-language audit, rollback, queue, and sink redaction pass adversarial fixtures |
| 4 | Locally complete | Scoped retrieval, Turkish behavior, canonical RRF, telemetry states, and benchmark gate pass |
| 5 | Locally complete with P2 | Deterministic temporal isolation passes locally. Live Neo4j service proof remains a CI requirement |
| 6 | Locally complete | Scope-aware CCE, poisoned and stale input handling, and no-network Stop behavior pass |
| 7 | Locally complete | Performance and fault matrix pass. Tree-sitter 0.26 crash boundary is reproduced and guarded |
| 8 | Locally complete | Final 3.6.0 package verifier passes 21 required checks and verifies 12 artifacts |
| 9 | Locally complete | Official-source capability classes and honest lexical-surrogate language pass independent review |
| 10 | In progress | Four independent reviews and version bump complete. Exact-SHA CI, dry run, and PR governance pending |

## Verified targeted results

1. Guarded Python proposal queue tests passed repeatedly with 100 concurrent writers and no record loss.
2. TypeScript proposal queue build and tests passed, including contention, stale-lock recovery, and non-disclosing errors.
3. Python audit concurrency passed five consecutive stress runs. Tail truncation, missing seal, cross-language suffix, and restoration fault tests passed.
4. CCE loss detection and scoped checkpoint lookup passed 25 tests. Claude checkpoint rehydration passed 20 tests and is fenced as untrusted input.
5. KG staging, worker, provider redaction, deterministic group binding, and graph export passed 65 tests.
6. Final local Python results were core 1664 passed and 14 skipped at 84 percent coverage, CC plugin 236 passed at 82 percent, graph 404 passed and 2 skipped at 87 percent, code 119 passed at 93 percent, parity 52 passed, and tools 22 passed in three consecutive runs.
7. Final local Node results were 616 passed and 5 skipped. Coverage was 87.75 percent statements, 80.94 percent branches, 93.15 percent functions, and 88.77 percent lines. Biome and the strict TypeScript build passed.
8. Lifecycle and Stop tests passed 12 cases. The exact candidate Stop proxy measured p95 4.704 ms, below 1000 ms, and network and LLM spies observed no forbidden call.
9. The exact candidate seven-surface synthetic benchmark gate passed on the production Python FTS5 path. Recall@10 was 1.0, Precision@10 was 0.1, MRR was 0.7336666667, nDCG@10 was 0.8006292454, and retrieval p95 was 3.797255 ms.
10. `actionlint` 1.7.7 was downloaded from its official release, its Windows archive checksum was verified against the official checksum file, and every current workflow passed locally.
11. An isolated tree-sitter 0.26.0 environment with tree-sitter-python 0.25.0, tree-sitter-javascript 0.25.0, and tree-sitter-typescript 0.23.2 reproduced Windows native exit `-1073741819` (`0xC0000005`) while the full graph suite walked `_extract_callee_name`. The probe intentionally bypassed Mneme's runtime guard. The supported 0.25.2 environment remains pinned, and normal execution rejects 0.26 before parsing.
12. The focused fault matrix passed 39 Python tests with 4 Windows-inapplicable skips and 120 Node tests with 1 skip. It covered concurrent writers, lock contention, stale locks, tail truncation, malformed JSONL and keys, invalid scope config, corrupt SQLite, migration rename boundaries, and simulated process crashes.
13. Four independent reviews covered release integrity, privacy boundaries, retrieval and temporal correctness, and open-source maintainability. All reported P0 and P1 findings were fixed and re-fixtured. Targeted final re-reviews reported zero remaining P0, P1, or P2 findings in the reviewed closure sets.
14. Ruff and strict mypy passed all four Python source packages. A wider Ruff pass also covered tools, benchmarks, and parity tests. The console refusal test file passed five consecutive runs after its Windows body-consumption fix.

These results were reproduced after the ten commits were partitioned. The immutable candidate identity is deliberately recorded by the PR head and GitHub check suite rather than embedded in its own commit content.

## Benchmark record

The exact candidate seven-surface gate used deterministic synthetic input with seed 42, 500 documents, 50 queries, cutoff 10, and the production Python FTS5 path. All seven surfaces passed. Retrieval quality measured Recall@10 1.0, Precision@10 0.1, MRR 0.7336666667, and nDCG@10 0.8006292454. Retrieval p95 was 3.797255 ms. Index build took 1185.2386 ms at 421.856 documents per second, produced a 1,175,552 byte index, and Python peak allocation was 4,898,636 bytes. LongMemEval and LoCoMo pinned-schema fixtures scored 1.0 and rejected malformed input. The RRF plus feature-hashing lexical surrogate scored nDCG@10 0.54056 and Recall@10 0.82, below production FTS5. It is therefore an ablation only and is not described as semantic or dense retrieval. Raw local evidence is `benchmarks/_runs/mneme-3.6-rc-gate-exact-sha.json`, which remains ignored as generated state. Results are synthetic regression evidence, not a cross-product comparison.

## Package record

The exact candidate 3.6.0 package verifier passed all 21 required checks and recorded outcome `pass` in ignored local evidence `benchmarks/_runs/package-rc-3.6.0-exact-sha.json`. It built and inspected four wheels, four sdists, one npm tarball, one deterministic Claude plugin tarball, one MCP registry metadata artifact, and one SHA256 manifest, for 12 artifacts total. Clean wheel and npm installation and uninstall, Claude plugin lifecycle, generic client stanza lifecycle, profile switching, claude-mem migration idempotency and rollback, destructive guard, archive safety, metadata agreement, and tree-sitter bounds passed. The first precommit attempt correctly failed because the release workflow prerequisite `hatchling` was absent. After installing `build` and `hatchling`, both the precommit and exact candidate verifiers passed. Official `mcp-publisher` binary validation was unavailable locally and remains an optional external check. No artifact was published.

## Governance record

At start, Dependabot vulnerability alerts were disabled and `main` branch protection allowed administrator bypass. The GitHub API was used to enable vulnerability alerts and set `enforce_admins` to true. A second API read verified HTTP 204 for the alerts endpoint and `enforce_admins: true`. PRs 28, 29, 31, 32, 33, 34, and 35 remain open until the new release candidate PR exists. Exact-SHA release dry-run evidence and PR supersession actions are still pending.

GNU Make is unavailable on the local Windows host. This is a local tool limitation, not a repository failure. The five `make install-dev` constituent commands, including frozen pnpm installation, passed manually. The underlying test and lint commands likewise passed manually. Editable imports for all four Python packages resolve to this worktree at 3.6.0. The global Python installation emits a stale invalid distribution warning for `~neme-cc-plugin`; isolated package installation passed and the warning is classified as a local environment P2.

## Residual risks

1. The live Neo4j integration test has not run locally because the Docker daemon was unavailable.
2. Python 3.11 through 3.13 and Node 22 are not installed locally. The GitHub matrix must provide those proofs.
3. POSIX-only symlink and private-filename cases are skipped on Windows and must pass on Linux or macOS CI.
4. Existing SQLite test helpers emit some `ResourceWarning` messages. Connection lifecycle review remains open.
5. The local rollback manifest retains exact source and vault paths because rollback must identify the original local resources. The public migration manifest and emitted diagnostics are redacted. The local manifest is a documented P2 privacy boundary and must remain local-only.
6. Official documentation pages without versioned URLs remain mutable. `COMPETITIVE.md` records the access date and pins repository sources, but cannot make third-party documentation immutable.
7. Some prerelease tag patterns can start the release workflow before preflight rejects a non-final target version. Publish jobs still require a tag push and verified preflight artifacts.
8. The local global Python site-packages contains a stale invalid distribution warning. Isolated package verification is clean.
9. The official `mcp-publisher` binary was unavailable locally. Repository metadata validation passed, and publisher validation remains an optional external gate.
10. The worktree was clean after ten commit partitions and all generated test, benchmark, coverage, and package outputs remained ignored. A clean state is mandatory again at delivery.

## Final decision

**NO GO pending remote gates.** All known P0 and P1 findings are closed and the complete local candidate gate set passes after commit partitioning. The remote operating-system, Python, and Node matrices and the release dry run have not yet passed. This decision may become `GO` only after those mandatory gates pass at one immutable PR head. Human approval, merge, tag, and publication remain separate external gates.
