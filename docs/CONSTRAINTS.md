# Sacred Constraints

mneme commits to five public constraints. Each has a definition, a rationale, and a machine-verifiable test. Any pull request that breaks any of these without an accompanying ADR amendment is rejected.

## C1: Vault as Single Source of Truth

**Definition**: All durable memory state must be reconstructible from the vault directory alone. Indexes are derived and rebuildable.

**Rationale**: User owns the data. Tool migration must be lossless. Backup is `tar czf backup.tar.gz vault/`.

**Verification**: `mneme rebuild-indexes --from-vault` produces byte-identical retrieval results compared to live indexes, modulo non-deterministic embedding seeds. Tested in `benchmarks/migration/`.

## C2: Stop Hook Latency Under 1 Second p95

**Definition**: The Claude Code Stop hook (session end) completes in under 1000 ms at p95 over 100 sessions on commodity hardware (4 cores, 16 GB RAM, SSD).

**Rationale**: User waits for the hook to release. Hook latency directly degrades editing experience.

**Verification**: `benchmarks/latency/run.py` records distribution. `benchmarks/latency/p95_guard.py` fails CI when `p95 >= 1000 ms`. Current seeded reference: **Stop p95 = 2 ms** on operator hardware (well within budget).

## C3: No LLM Call on the Critical Path

**Definition**: SessionStart, Stop, and PreCompact hooks must not make any LLM API call, must not depend on network availability, and must not block on background compression workers.

**Rationale**: LLM calls add seconds of latency and dollars of cost. Network calls are unreliable. Critical path must be deterministic.

**Verification**: Static analysis pass at `tools/spec_verify.py` greps hook source for `anthropic.`, `openai.`, `requests.`, `httpx.`, `urllib.request`. Any match without an explicit `pragma: no cover (offline-only)` comment fails CI.

## C4: Privacy Redaction at Staging Write

**Definition**: Content matched by `<private>...</private>` tags or by configured PII patterns must be stripped before reaching staging JSONL, telemetry, knowledge graph, FTS5 index, or vault markdown. A SHA256 audit log entry must record the redaction.

**Rationale**: Privacy is a non-negotiable user expectation. Leakage is hard to recall.

**Verification**: `tests/integration/test_privacy_redaction.py` injects ten `<private>` samples and asserts zero appearance in any downstream store. Audit log entry presence confirmed.

## C5: Token Efficiency on by Default

**Definition**: `distill.shell_compress`, `distill.injection_dedup`, and `distill.adaptive_topk` are enabled by default. `distill.compressed_format=keypoints` is the default for re-injection.

**Rationale**: Hidden token costs erode user trust and break the cost model. Default-on prevents accidental waste.

**Verification**: `benchmarks/cost/run.py` compares mneme-distill-on against mneme-distill-off against a no-memory baseline across light, medium, and heavy workloads. Default config must demonstrate 40 to 60 percent reduction on heavy workload. Current seeded reference numbers: `shell_compress` 88 percent reduction on redundant Bash output, `injection_dedup` 95 percent skip rate in tight 20-turn sessions, `compressed_format=keypoints` 46 percent size vs full, `compressed_format=ref` 12 percent of full.
