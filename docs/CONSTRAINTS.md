# Sacred Constraints

mneme commits to five public constraints. Each has a definition, a rationale, and a machine-verifiable test. Any pull request that breaks any of these without an accompanying ADR amendment is rejected.

These constraints are client-neutral. Where a constraint names a Claude Code lifecycle hook (Stop, SessionStart, PreCompact), it applies equally to the corresponding Codex lifecycle hook, since both clients drive the same `mneme hook` entry over the same core. See ADR-014 in `docs/ARCHITECTURE.md`.

## C1: Vault as Single Source of Truth

**Definition**: All durable memory state must be reconstructible from the vault directory alone. Indexes are derived and rebuildable.

**Rationale**: User owns the data. Tool migration must be lossless. Backup is `tar czf backup.tar.gz vault/`.

**Verification**: `mneme index rebuild` reconstructs FTS5 from vault markdown. Migration benchmark D verifies the same source fixture can be migrated, re-run idempotently, and rebuilt without structural loss.

## C2: Stop Hook Latency Under 1 Second p95

**Definition**: The Claude Code Stop hook (session end) completes in under 1000 ms at p95 over 100 sessions on commodity hardware (4 cores, 16 GB RAM, SSD).

**Rationale**: User waits for the hook to release. Hook latency directly degrades editing experience.

**Verification**: `benchmarks/latency/run.py` records distribution. `benchmarks/latency/p95_guard.py` fails CI when `p95 >= 1000 ms`. Current seeded reference: **Stop p95 = 2 ms** on operator hardware (well within budget).

## C3: No LLM Call on the Critical Path

**Definition**: SessionStart, Stop, and PreCompact hooks must not make any LLM API call, must not depend on network availability, and must not block on background compression workers.

**Rationale**: LLM calls add seconds of latency and dollars of cost. Network calls are unreliable. Critical path must be deterministic.

**Verification**: Static analysis pass at `tools/spec_verify.py` greps hook source for `anthropic.`, `openai.`, `requests.`, `httpx.`, `urllib.request`. Any match without an explicit `pragma: no cover (offline-only)` comment fails CI.

## C4: Privacy Redaction at Staging Write

**Definition**: Content matched by `<private>...</private>` tags or by configured PII patterns must be stripped before reaching staging JSONL, telemetry, knowledge graph, FTS5 index, or vault markdown. Staging and telemetry redactions must write an audit entry.

**Rationale**: Privacy is a non-negotiable user expectation. Leakage is hard to recall.

**Verification**: `packages/mneme-core/tests/integration/test_staging.py` and `packages/mneme-core/tests/integration/test_telemetry.py` assert recursive redaction and audit-log entry creation. MCP write redaction returns `redactions_applied` and is covered in the Node test suite.

## C5: Token Efficiency on by Default

**Definition**: `distill.shell_compress`, `distill.injection_dedup`, and `distill.adaptive_topk` are enabled by default. `distill.compressed_format=keypoints` is the default for re-injection.

**Rationale**: Hidden token costs erode user trust and break the cost model. Default-on prevents accidental waste.

**Verification**: `benchmarks/cost/run.py` measures the shipped primitives directly. Current seeded reference numbers: `shell_compress` 88 percent reduction on redundant Bash output, `injection_dedup` 95 percent skip rate in tight 20-turn sessions, `compressed_format=keypoints` 46 percent size vs full, `compressed_format=ref` 12 percent of full.
