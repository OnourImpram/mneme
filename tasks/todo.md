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
- [x] Run the real Neo4j integration service test. The service-container job passed on pre-final code SHA `c237247`.
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
- [x] Close the follow-up P1 review findings for canonical migration rollback, signed schema v2 aliases, and diagnostic path redaction. Independent re-review found zero P0 or P1 and one documented cosmetic P2.
- [x] Close the Python 3.11 Windows proposal-queue lock timeout found by the final CI matrix. The 30-second wait and 60-second stale contract passed the Python 3.11 Windows core suite.
- [x] Upgrade all 18 version sources to 3.6.0 only after preceding local gates pass.
- [x] Complete changelog, upgrading guide, ADRs, benchmarks, and engineering report for the precommit candidate.
- [x] Run full final matrices and release dry run at candidate evidence SHA `17d5651`. The report closeout head must pass the same gates before merge.
- [x] Commit the verified Loop 10 change set with a Conventional Commit.

## Delivery and governance

- [x] Push the branch and open `Release: complete Mneme 3.6.0 ten-loop engineering hardening` as PR 36.
- [x] Record unique diffs and close PRs 29, 31, 32, 33, and 34 as superseded.
- [x] Close PR 35 with verified tree-sitter 0.26 crash evidence.
- [x] Leave PR 28 open as a separate dependency change.
- [x] Enable Dependabot vulnerability alerts.
- [x] Remove administrator bypass from required checks.
- [x] Record repository settings before and after.
- [x] Produce a technical `GO` decision with zero open P0 or P1 findings and disclosed P2 risks.
- [x] Leave human approval, merge, tag, and publish as external gates.

## Final verification commands

- [x] `make install-dev`. Passed with GNU Make 4.3 in a fresh native WSL clone using Python 3.12.3, checksum-verified Node 24.14.0, and pnpm 9.15.9.
- [x] `make test`. Passed in the same exact-SHA clean clone. The Linux Node surface ran 625 tests.
- [x] `make lint`. Ruff, strict mypy, and Biome passed in the same exact-SHA clean clone.
- [x] `python tools/version_bump.py --check 3.6.0`
- [x] `python tools/spec_verify.py`
- [x] `python tools/repo_integrity.py`
- [x] All plugin validators
- [x] Python 3.11 through 3.14 matrix
- [x] Node 22 and 24 matrix
- [x] Linux, macOS, and Windows matrix
- [x] Coverage at or above 80 percent for all four Python packages and Node business logic on local Python 3.14 and Node 24
- [x] Seven benchmark surfaces and official-schema adapter fixtures
- [x] `release.yml` dry run with `target_version=3.6.0` at candidate evidence SHA `17d5651`. All publish jobs were skipped.
- [x] Clean git status, `git fsck`, and secret scan in fresh exact-SHA clones. Generated evidence remained ignored.

## Review notes

The first remote candidate passed every job except Python 3.11 on Windows, where concurrent durable queue writes exceeded the one-second lock wait. The queue contract now uses a 30-second wait and a 60-second stale threshold, and the Python 3.11 Windows suite passes. A follow-up independent review found three P1 migration compatibility defects. Stable aliases are now canonicalized, diagnostics redact lexical and canonical path forms, and signed legacy aliases without a signed canonical target fail closed while preserving the archive for manual hash-verified recovery. The independent re-review found zero P0 or P1. Terminal punctuation loss in conservative path redaction remains a disclosed cosmetic P2. Candidate evidence SHA `17d5651` passed CI, CodeQL, benchmarks, release dry run, governance, Make targets, and clean-clone proof. The report closeout head must pass the same hosted checks before merge.

## Website update

- [x] Preserve `v3.5.0` as the latest published release while presenting 3.6.0 only as a release candidate.
- [x] Update the `gh-pages` content with verified 3.6.0 compatibility, isolation, retrieval, lifecycle, and benchmark evidence.
- [x] Remove dense or semantic claims for the lexical feature-hashing fallback.
- [x] Keep the existing self-contained, no-build, self-hosted-font site architecture.
- [x] Validate semantic structure, links, copy controls, responsive layout, overflow, and browser console output.
- [x] Capture desktop and mobile localhost screenshots before publishing the site branch.
- [x] Publish only the verified `gh-pages` tree and confirm the live Pages deployment uses the expected commit.
- [x] Re-run PR 36 required checks and release dry run on the documentation closeout SHA.
- [x] Restore the documented five-second Stop session-log lock budget after the final Windows matrix reproduced lost updates at 0.5 seconds.
- [x] Re-run focused contention, full plugin, lint, exact-head CI, CodeQL, benchmarks, and release dry run after the lock correction.
