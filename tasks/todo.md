# Mneme 3.6.0 Ten Loop Engineering Checklist

## Execution boundary

- [x] Use a fresh checkout from `main@02641d9c59c354c53733cc065c081fdd9d25836b`.
- [x] Work only on `sol-ultra/mneme-ten-loop-upgrade-20260717-r3`.
- [x] Record local toolchain and repository baseline.
- [x] Confirm latest published release is `v3.5.0`.
- [x] Confirm no tag, release, publish, merge, or user vault mutation is in scope.
- [x] Keep the worktree free of secrets and generated local state. Secret diff scan found no credential pattern, and generated benchmark and package outputs remain ignored.

## Loop 1. Repository truth and recovery

- [x] Remove four temporary 3.6.0 workflows and three ready markers.
- [x] Preserve the normal `release.yml` workflow.
- [x] Add pinned `actionlint`, repository integrity, license, Node, tool, version, and client manifest gates.
- [x] Add a Claude plugin validator.
- [x] Run all Loop 1 local gates after the ten commits were partitioned. Remote matrix remains a Loop 10 gate.
- [x] Commit the verified Loop 1 change set with a Conventional Commit.

## Loop 2. API and functional correctness

- [x] Generate public Draft 7 tool schemas from authoritative Zod schemas.
- [x] Verify all nine MCP tool contracts and runtime acceptance behavior.
- [x] Add optional scope to `mneme_checkpoint_list` and `mneme_working_set_load`.
- [x] Reject wildcard scope for durable writes and preserve explicit wildcard reads.
- [x] Add a shared Python and TypeScript scope contract.
- [x] Run all Loop 2 local gates after the ten commits were partitioned.
- [x] Commit the verified Loop 2 change set with a Conventional Commit.

## Loop 3. Security and privacy

- [x] Harden guarded Python and TypeScript atomic writes against symlink and parent identity races.
- [x] Add a shared Python and TypeScript proposal queue lock and atomic claim protocol.
- [x] Bind proposal identity, application, rollback, and journaling to scope.
- [x] Add shared audit locking, monotonic sequence, keyed head seals, truncation detection, and stale rollback checks.
- [x] Reapply redaction at compression, FTS5, telemetry, connector, sync, KG, and Graphiti boundaries.
- [x] Complete adversarial review and final security regression run.
- [x] Commit the verified Loop 3 change set with a Conventional Commit.

## Loop 4. Retrieval correctness

- [x] Fail closed for concrete-scope reads against legacy unscoped FTS5 indexes.
- [x] Distinguish attempted, succeeded, failed, and contributed backend telemetry.
- [x] Reverify Turkish `I`, `İ`, `ı`, `i`, snippets, deduplication, confidence, provenance, and error classes.
- [x] Run retrieval benchmarks and ablations.
- [x] Commit the verified Loop 4 change set with a Conventional Commit.

## Loop 5. Temporal and graph isolation

- [x] Add deterministic valid-time and transaction-time Graphiti query planning.
- [x] Isolate claims by composite scope and claim identity.
- [x] Keep contradiction and supersession visibility within scope.
- [x] Bind live KG ingestion to deterministic Graphiti `group_id` values.
- [ ] Run the real Neo4j integration service test.
- [x] Commit the verified Loop 5 change set with a Conventional Commit.

## Loop 6. Lifecycle and CCE

- [x] Make capture failures visible.
- [x] Use one UTC source for Stop document naming and metadata.
- [x] Scope CCE checkpoint production and lookup.
- [x] Treat checkpoint rehydration as untrusted memory input.
- [x] Verify poisoned, stale, duplicate, and low-salience lifecycle scenarios.
- [x] Prove Stop performs no network or LLM calls.
- [x] Commit the verified Loop 6 change set with a Conventional Commit.

## Loop 7. Performance and resilience

- [x] Measure Stop p95 and enforce less than 1000 ms.
- [x] Measure retrieval, indexing, memory, artifact size, and build time.
- [x] Fault-inject concurrent writes, lock contention, corrupt SQLite and JSONL, invalid config, partial rename, and process crash.
- [x] Preserve `tree-sitter>=0.25,<0.26` and verify the known 0.26 crash boundary.
- [x] Commit the verified Loop 7 change set with a Conventional Commit.

## Loop 8. Integration and packaging

- [x] Verify clean Claude, Codex, Antigravity, and generic MCP installs.
- [x] Verify install, uninstall, profile switching, and claude-mem migration idempotency and rollback.
- [x] Implement and verify `mneme doctor --verify-isolation` using a temporary read-only fixture.
- [x] Build and install four wheels and sdists, npm tarball, Claude plugin tarball, and registry metadata.
- [x] Commit the verified Loop 8 change set with a Conventional Commit.

## Loop 9. Competitive capability decision

- [x] Update `COMPETITIVE.md` from official primary sources only.
- [x] Separate OSS default, OSS opt-in, hosted, research, and not-evidenced surfaces.
- [x] Remove categorical leadership claims.
- [x] Keep real semantic models out of the 3.6.0 base package.
- [x] Ensure feature hashing is never described as semantic or dense.
- [x] Commit the verified Loop 9 change set with a Conventional Commit.

## Loop 10. Release candidate

- [x] Complete independent release integrity, privacy, retrieval, and OSS maintainer reviews.
- [x] Resolve all P0 and P1 findings and document remaining P2 risks.
- [x] Upgrade all 18 version sources to 3.6.0 only after preceding local gates pass.
- [x] Complete changelog, upgrading guide, ADRs, benchmarks, and engineering report for the precommit candidate.
- [ ] Run full final matrices and release dry run at the exact final SHA.
- [x] Commit the verified Loop 10 change set with a Conventional Commit.

## Delivery and governance

- [ ] Push the branch and open `Release: complete Mneme 3.6.0 ten-loop engineering hardening`.
- [ ] Record unique diffs and close PRs 29, 31, 32, 33, and 34 as superseded.
- [ ] Close PR 35 with verified tree-sitter 0.26 crash evidence.
- [ ] Leave PR 28 open as a separate dependency change.
- [x] Enable Dependabot vulnerability alerts.
- [x] Remove administrator bypass from required checks.
- [x] Record repository settings before and after.
- [ ] Produce a technical `GO` or `NO GO` decision.
- [ ] Leave human approval, merge, tag, and publish as external gates.

## Final verification commands

- [ ] `make install-dev`. GNU Make is unavailable on the local Windows host. The five underlying Makefile commands passed manually, including frozen pnpm installation.
- [ ] `make test`. GNU Make is unavailable locally. The exact Python and Node test surfaces passed manually and must still run through CI on the final SHA.
- [ ] `make lint`. GNU Make is unavailable locally. Ruff, strict mypy, Biome, and TypeScript build passed manually and must still run through CI on the final SHA.
- [x] `python tools/version_bump.py --check 3.6.0`
- [x] `python tools/spec_verify.py`
- [x] `python tools/repo_integrity.py`
- [x] All plugin validators
- [ ] Python 3.11 through 3.14 matrix
- [ ] Node 22 and 24 matrix
- [ ] Linux, macOS, and Windows matrix
- [x] Coverage at or above 80 percent for all four Python packages and Node business logic on local Python 3.14 and Node 24
- [x] Seven benchmark surfaces and official-schema adapter fixtures
- [ ] `release.yml` dry run with `target_version=3.6.0` at the exact PR SHA
- [x] Clean git status and secret scan after commit partitioning. Generated evidence remained ignored.

## Review notes

Local integration and four independent reviews are complete. All identified P0 and P1 findings are closed. Final SHA matrices, release dry run, clean delivery state, and external GitHub checks remain open.
