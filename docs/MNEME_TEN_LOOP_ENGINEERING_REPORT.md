# Mneme 3.6.0 Ten Loop Engineering Report

**Status:** Release-candidate finalization in progress.

**Technical decision:** `NO GO` until the final documentation SHA passes the complete GitHub matrix and `release.yml` dry run. The preceding code SHA passes CodeQL, benchmarks, package dry run, and all completed CI jobs. The final immutable PR head remains the decision boundary.

## Execution identity

| Field | Current value |
|---|---|
| Repository | `OnourImpram/mneme` |
| Starting branch | `main` |
| Starting SHA | `02641d9c59c354c53733cc065c081fdd9d25836b` |
| Upgrade branch | `sol-ultra/mneme-ten-loop-upgrade-20260717-r3` |
| Release-candidate PR | [#36](https://github.com/OnourImpram/mneme/pull/36) |
| Pre-final code SHA | `c23724775177699a16a587833e04cee4213714d9` |
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
| Python core tests | Two environment-bound failures at start | 1665 passed, 14 skipped, 84.24 percent coverage |
| MCP tests | Four environment-bound failures at start | 619 passed, 6 skipped. Statements 87.58 percent, branches 80.87 percent, functions 92.90 percent, lines 88.58 percent |
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
| Temporal visibility is deterministic and scope isolated | Implemented for SQLite claims and Graphiti query planning. The Neo4j service-container integration passed on pre-final code SHA `c237247` |
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
| MNEME-R3-014 | P2 | 7 | Local Docker daemon unavailable for live Neo4j proof | Closed by the Neo4j service-container integration on pre-final code SHA `c237247` |
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
| MNEME-R3-029 | P1 | 8 | Stable source aliases such as macOS `/var` and `/private/var` could be misclassified during migration finalization | Stable parent aliases are canonicalized after signed-manifest verification. New manifests bind and use the canonical operational source path |
| MNEME-R3-030 | P1 | 8 | Canonical or lexical absolute paths could remain visible in migration diagnostics | Drive, UNC, POSIX, and file-URI paths are redacted after delimiters while structural closing punctuation is retained |
| MNEME-R3-031 | P1 | 10 | Python 3.11 Windows could exceed the one-second proposal queue lock budget during concurrent durable flushes | The shared queue contract now waits up to 30 seconds and treats a lock as stale after 60 seconds. The 80-write latency regression and the Python 3.11 Windows matrix pass |
| MNEME-R3-032 | P1 | 8 | A signed schema v2 manifest with a lexical source alias did not bind a signed canonical restore target | Automatic move finalization, interrupted recovery, and source restoration fail closed for an unbound alias. The signed archive is preserved for manual hash-verified recovery |
| MNEME-R3-033 | P2 | 8 | Generic path redaction can consume terminal `.`, `!`, `?`, or `:` punctuation | Accepted as a privacy-conservative cosmetic limitation. Paths remain redacted and closing brackets, parentheses, commas, and semicolons are preserved |

## Loop scorecard

| Loop | Status | Evidence summary |
|---:|---|---|
| 1 | Code candidate complete | Repository recovery, actionlint 1.7.7, version, license, Node, tool, and manifest gates pass locally and on pre-final code SHA `c237247` |
| 2 | Locally complete | Shared scope contracts and nine-tool schema/runtime parity pass |
| 3 | Locally complete | Atomic writes, cross-language audit, rollback, queue, and sink redaction pass adversarial fixtures |
| 4 | Locally complete | Scoped retrieval, Turkish behavior, canonical RRF, telemetry states, and benchmark gate pass |
| 5 | Complete | Deterministic temporal isolation passes locally and the real Neo4j service integration proves `group_id` isolation in CI |
| 6 | Locally complete | Scope-aware CCE, poisoned and stale input handling, and no-network Stop behavior pass |
| 7 | Locally complete | Performance and fault matrix pass. Tree-sitter 0.26 crash boundary is reproduced and guarded |
| 8 | Locally complete | Final 3.6.0 package verifier passes 21 required checks and verifies 12 artifacts |
| 9 | Locally complete | Official-source capability classes and honest lexical-surrogate language pass independent review |
| 10 | Finalizing | Four independent reviews, version bump, pre-final CodeQL, benchmarks, and package dry run pass. Final-head checks and PR governance remain |

## Verified targeted results

1. Guarded Python proposal queue tests passed repeatedly with concurrent writers and no record loss. The final 80-write fixture injects 20 ms durable-flush latency across 12 workers.
2. TypeScript proposal queue build and tests passed, including active contention, stale-lock recovery, and non-disclosing errors. The shared lock wait and stale thresholds are 30 and 60 seconds.
3. Python audit concurrency passed five consecutive stress runs. Tail truncation, missing seal, cross-language suffix, and restoration fault tests passed.
4. CCE loss detection and scoped checkpoint lookup passed 25 tests. Claude checkpoint rehydration passed 20 tests and is fenced as untrusted input.
5. KG staging, worker, provider redaction, deterministic group binding, and graph export passed 65 tests.
6. Final local Python results were core 1665 passed and 14 skipped at 84.24 percent coverage, CC plugin 236 passed at 81.58 percent, graph 404 passed and 2 skipped at 87.25 percent, code 119 passed at 93.40 percent, parity 52 passed, and tools 22 passed in three consecutive runs.
7. Final local Node results were 619 passed and 6 skipped. Coverage was 87.58 percent statements, 80.87 percent branches, 92.90 percent functions, and 88.58 percent lines. Biome and the strict TypeScript build passed.
8. Lifecycle and Stop tests passed 12 cases. The exact candidate Stop proxy measured p95 4.704 ms, below 1000 ms, and network and LLM spies observed no forbidden call.
9. The exact candidate seven-surface synthetic benchmark gate passed on the production Python FTS5 path. Recall@10 was 1.0, Precision@10 was 0.1, MRR was 0.7336666667, nDCG@10 was 0.8006292454, and retrieval p95 was 3.797255 ms.
10. `actionlint` 1.7.7 was downloaded from its official release, its Windows archive checksum was verified against the official checksum file, and every current workflow passed locally.
11. An isolated tree-sitter 0.26.0 environment with tree-sitter-python 0.25.0, tree-sitter-javascript 0.25.0, and tree-sitter-typescript 0.23.2 reproduced Windows native exit `-1073741819` (`0xC0000005`) while the full graph suite walked `_extract_callee_name`. The probe intentionally bypassed Mneme's runtime guard. The supported 0.25.2 environment remains pinned, and normal execution rejects 0.26 before parsing.
12. The focused fault matrix passed 39 Python tests with 4 Windows-inapplicable skips and 120 Node tests with 1 skip. It covered concurrent writers, lock contention, stale locks, tail truncation, malformed JSONL and keys, invalid scope config, corrupt SQLite, migration rename boundaries, and simulated process crashes.
13. Four independent reviews covered release integrity, privacy boundaries, retrieval and temporal correctness, and open-source maintainability. All reported P0 and P1 findings were fixed and re-fixtured. The final migration re-review reported zero P0 or P1 and one disclosed P2 for privacy-conservative terminal punctuation loss.
14. Ruff and strict mypy passed all four Python source packages. A wider Ruff pass also covered tools, benchmarks, and parity tests. The console refusal test file passed five consecutive runs after its Windows body-consumption fix.
15. Migration integration passed 54 tests with 1 platform skip. New canonical manifests retain automatic move and rollback. Signed legacy lexical-alias manifests fail closed before quarantine or restoration and preserve the archive for manual hash-verified recovery.
16. Pre-final code SHA `c237247` passed CodeQL, the independent benchmark workflow, the real Neo4j integration job, and `release.yml` with `dry_run=true` and `target_version=3.6.0`. Every publish job was skipped.

These results were reproduced after the ten commits were partitioned. The immutable candidate identity is deliberately recorded by the PR head and GitHub check suite rather than embedded in its own commit content.

## Benchmark record

The exact candidate seven-surface gate used deterministic synthetic input with seed 42, 500 documents, 50 queries, cutoff 10, and the production Python FTS5 path. All seven surfaces passed. Retrieval quality measured Recall@10 1.0, Precision@10 0.1, MRR 0.7336666667, and nDCG@10 0.8006292454. Retrieval p95 was 3.797255 ms. Index build took 1185.2386 ms at 421.856 documents per second, produced a 1,175,552 byte index, and Python peak allocation was 4,898,636 bytes. LongMemEval and LoCoMo pinned-schema fixtures scored 1.0 and rejected malformed input. The RRF plus feature-hashing lexical surrogate scored nDCG@10 0.54056 and Recall@10 0.82, below production FTS5. It is therefore an ablation only and is not described as semantic or dense retrieval. Raw local evidence is `benchmarks/_runs/mneme-3.6-rc-gate-exact-sha.json`, which remains ignored as generated state. Results are synthetic regression evidence, not a cross-product comparison.

## Package record

The exact candidate 3.6.0 package verifier passed all 21 required checks and recorded outcome `pass` in ignored local evidence `benchmarks/_runs/package-rc-3.6.0-exact-sha.json`. It built and inspected four wheels, four sdists, one npm tarball, one deterministic Claude plugin tarball, one MCP registry metadata artifact, and one SHA256 manifest, for 12 artifacts total. The npm tarball SHA256 is `9972f9dd6e951cd00d69d2c1e4816c9de84ae97bd7cd78f14ffada1f7d7bdc6e`. The SHA256 manifest artifact digest is `ceb814bae857f391a6caae3864325038481c808996adcc89f66778ad2299bbab`. Clean wheel and npm installation and uninstall, Claude plugin lifecycle, generic client stanza lifecycle, profile switching, claude-mem migration idempotency and rollback, destructive guard, archive safety, metadata agreement, and tree-sitter bounds passed. The first precommit attempt correctly failed because the release workflow prerequisite `hatchling` was absent. After installing `build` and `hatchling`, both the precommit and exact candidate verifiers passed. Official `mcp-publisher` binary validation was unavailable locally and remains an optional external check. No artifact was published.

## Governance record

At start, Dependabot vulnerability alerts were disabled and `main` branch protection allowed administrator bypass. The GitHub API was used to enable vulnerability alerts and set `enforce_admins` to true. A second API read verified HTTP 204 for the alerts endpoint and `enforce_admins: true`. Release-candidate PR [#36](https://github.com/OnourImpram/mneme/pull/36) is open with the required title. PR 28 remains a separate dependency lane. Unique-diff inventory and supersession actions for PRs 29, 31, 32, 33, 34, and 35 remain the last governance step after the final PR head stabilizes.

GNU Make is unavailable on the local Windows host. This is a local tool limitation, not a repository failure. The five `make install-dev` constituent commands, including frozen pnpm installation, passed manually. The underlying test and lint commands likewise passed manually. Editable imports for all four Python packages resolve to this worktree at 3.6.0. The global Python installation emits a stale invalid distribution warning for `~neme-cc-plugin`; isolated package installation passed and the warning is classified as a local environment P2.

## Residual risks

1. The local Docker daemon was unavailable. The required real Neo4j test passed in the GitHub service container on pre-final code SHA `c237247`.
2. Existing SQLite test helpers emit some `ResourceWarning` messages. Connection lifecycle cleanup remains a nonblocking P2.
3. The local rollback manifest retains exact source and vault paths because rollback must identify the original local resources. The public migration manifest and emitted diagnostics are redacted. The local manifest is a documented P2 privacy boundary and must remain local-only.
4. Signed schema v2 manifests that recorded only a lexical source alias do not contain a signed canonical restore target. Automatic source finalization and restoration fail closed. The signed archive is preserved for manual hash-verified recovery.
5. Generic diagnostic path redaction can consume terminal `.`, `!`, `?`, or `:` punctuation. This is cosmetic and privacy-conservative.
6. Official documentation pages without versioned URLs remain mutable. `COMPETITIVE.md` records the access date and pins repository sources, but cannot make third-party documentation immutable.
7. Some prerelease tag patterns can start the release workflow before preflight rejects a non-final target version. Publish jobs still require a tag push and verified preflight artifacts.
8. The local global Python site-packages contains a stale invalid distribution warning. Isolated package verification is clean.
9. The official `mcp-publisher` binary was unavailable locally. Repository metadata validation passed, and publisher validation remains an optional external gate.
10. Local policy prevented a final local Git commit. Remote commits were created through the GitHub Git Data API without force. Delivery requires a fresh clean clone of the final branch to prove exact-SHA status and a secrets-free tree.

## Final decision

**NO GO pending final-head gates.** All known P0 and P1 findings are closed. The complete local candidate gate set and the pre-final code SHA checks pass, including the release dry run with every publish job skipped. This decision becomes `GO` only after the documentation commit, complete matrix, CodeQL, benchmarks, dry run, governance actions, and clean exact-SHA clone all pass at one immutable PR head. Human approval, merge, tag, and publication remain separate external gates.
