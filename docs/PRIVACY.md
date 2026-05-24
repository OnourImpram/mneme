# Privacy and Network Audit

mneme is local-first by architectural conviction. This document audits every outbound network call mneme can make and documents the conditions under which it makes them. Last reviewed 2026-05-20 per spec-kit F1 finding.

## Summary

A fresh `mneme install --profile=lite` makes zero outbound network calls. The two opt-in paths that can introduce network traffic (LLM compression, full-profile Neo4j on localhost) are documented per call below. The user's vault stays on the user's disk. mneme is not a data processor under GDPR Article 4 because no third party receives user data by default. Constitutional principles C2 + C3 (Zero-LLM-Stop Critical Path) and C4 (Privacy Redaction at Staging Write) make this enforceable, not merely promised.

## Default Posture

In a fresh install with default settings, mneme makes zero outbound network calls. No telemetry, no usage pings, no analytics, no version check phone-home, no error reporting service, no third party SDK, no anonymized metrics collection. The default `compression_enabled: false` configuration value jointly with the C2 + C3 hook spec means there is no code path in the lite profile that opens a non-localhost socket.

## Hook-by-Hook Outbound Audit

The five Claude Code hooks mneme installs are deterministic local-only operations by design. None of them issue any network call in the default configuration.

| Hook | Default outbound | Notes |
|---|---|---|
| PostToolUse | None | Appends to staging JSONL. Disk-local. |
| SessionStart | None | Reads vault, runs FTS5 query, injects context. Disk-local. |
| Stop | None | Writes session markdown via atomic rename. Disk-local. p95 < 1s validated in `benchmarks/latency/`. |
| PreCompact | None | Saves Claude Code state pointer. Disk-local. |
| SessionEnd | None | Flushes background queues. Disk-local. |

Constitutional principles C2 + C3 (Zero-LLM-Stop Critical Path) make the absence of outbound calls in Stop, SessionStart, and PreCompact an enforced invariant rather than a documentation promise. `tools/spec_verify.py` static-greps each hook source for `anthropic.`, `openai.`, `requests.`, `httpx.`, `urllib.request` patterns and fails CI on any unsanctioned match. The grep is shipped in the public repo, the CI job runs on every PR, the failure mode is observable.

## Outbound Calls Under Specific Conditions

### 1. LLM API for Compression

**When**: only when the user explicitly sets `compression_enabled: true` in `~/.mneme/config.toml` AND provides an API key (either an Anthropic key or via Claude Code's own auth pass-through).

**Endpoint**: `api.anthropic.com` by default, or the user-configured endpoint via the pluggable `LlmProvider` Protocol (Anthropic ships in `mneme-core`, OpenAI and Ollama adapters land in v1.1).

**Frequency**: at most once per session, in the background, after Stop. Subject to the cost cap ledger.

**Payload**: redacted session staging content (post-C4 privacy redaction). Never raw user prompts, never `<private>`-tagged content.

**Cost ledger**: every API call is recorded with token counts and dollar cost in `vault/.mneme/kg_cost_ledger.jsonl` under `kind: compression`. The user-configurable monthly cap defaults to 25 USD. When the cap is reached, compression pauses until the next month or until the user raises the cap.

**Opt-out**: set `compression_enabled: false` (the default) or run `mneme-core compress disable`.

### 2. Local Neo4j (Full Profile Only)

**When**: only when the user installs the full profile (`mneme install --profile=full`), which provisions a local Docker container running Neo4j on `localhost:7687`.

**Endpoint**: `localhost:7687`. Local only. No external traffic. Bolt protocol.

**Frequency**: continuous for the lifetime of the Neo4j container. Reads and writes from the Graphiti adapter.

**Opt-out**: `mneme upgrade --profile=standard` downgrades and stops the container.

## Tier-by-Tier Outbound Matrix

| Tier | Default outbound | Opt-in outbound | Local subprocess |
|---|---|---|---|
| lite | None | None | None |
| standard | None | LLM compression (if enabled) | ONNX runtime in-process |
| full | None | LLM compression (if enabled) | Neo4j on localhost:7687, ONNX runtime in-process |

No tier introduces a third party network endpoint by default. The lite tier has zero subprocess dependencies.

## Privacy Redaction at Staging Write

mneme enforces redaction at the earliest possible point in the data pipeline (staging-write), not at the read or export point. Redacted content never enters any downstream store.

**Mechanism**: any content matched by `<private>...</private>` tags or by user-configured PII regex patterns is stripped before reaching any of:

- Staging JSONL at `vault/.mneme/staging/`
- Telemetry JSONL at `vault/.mneme/telemetry/`
- Knowledge graph episodes (full profile only)
- FTS5 index
- Vault session markdown at `vault/sessions/YYYY-MM-DD.md`

**Audit trail**: a SHA256 hash of the original (unredacted) content is written to `~/.mneme/audit/YYYY-MM-DD.jsonl`. The audit log lets the user verify that redaction fired without storing the redacted content itself.

**Verification**: `tests/integration/test_privacy_redaction.py` injects ten `<private>` samples and asserts zero appearance in any downstream store. The test runs in CI on every PR per constitutional principle C4.

## Migration Tool Privacy

`mneme-migrate` reads the user's claude-mem SQLite DB and converts it to vault markdown. The tool is fully local. It opens the SQLite file in read-only mode, applies privacy redaction during translation, and writes to the vault directory. No data leaves the user's machine.

The `--archive` tri-state controls source database treatment:

| Mode | Source DB | Snapshot |
|---|---|---|
| `preserve` (default) | left in place | none |
| `copy` | left in place | snapshot saved to vault |
| `move` | deleted from original location | snapshot saved to vault |

The `move` mode requires `--confirm-delete` as a two-factor confirmation per `docs/MIGRATION-FROM-CLAUDE-MEM.md`.

## What mneme Never Does

- No usage analytics or telemetry to any third party.
- No version check or update phone-home.
- No content uploads except the user-opted-in compression call described above.
- No reading of files outside the configured vault directory and the documented config paths.
- No anonymized or aggregated metrics collection.
- No third party SDK. No Sentry, no Datadog, no LogRocket, no Mixpanel, no Segment, no analytics provider of any kind.
- No background daemon that persists outside of `mneme install`-managed components.
- No automatic LLM call on Stop. Compression is background and opt-in. Stop hook latency p95 is bounded under 1000 ms by constitutional principle C2 + C3.

## Local Files Read

In addition to the vault, mneme reads:

- `~/.mneme/config.toml` (if present, user-owned configuration).
- `~/.claude/settings.json` (only when `mneme install` or `mneme uninstall` runs, BOM-safe atomic mutation pattern).
- Python environment detection via `py -3` launcher (Windows) or `which python3` (POSIX), to wire hook launchers correctly.
- The claude-mem SQLite file at `~/.claude-mem/claude-mem.db` (only when `mneme-migrate` is invoked by the user, read-only mode).

No file outside this list is opened.

## Audit Log

Privacy-relevant operations write to `~/.mneme/audit/YYYY-MM-DD.jsonl` with SHA256 hashes of redacted content, not the content itself. Daily rotation. Inspect these JSONL files directly or with your normal log tooling.

Schema per entry:

```json
{
  "ts": "2026-05-20T17:46:24Z",
  "op": "redact",
  "scope": "staging|telemetry|kg|fts5|vault",
  "sha256": "abc123...",
  "bytes_redacted": 412
}
```

## Verify It Yourself

The simplest way to validate the zero-outbound claim is to run mneme inside a network observer and confirm operations still succeed without non-localhost connections.

```bash
# Linux: strace a local diagnostic command to check outbound syscalls.
strace -f -e trace=network -o /tmp/mneme-net.log mneme doctor
grep -E "AF_INET[6]?" /tmp/mneme-net.log | grep -v "127\.0\.0\.1\|::1"

# macOS: lsof to see open network connections during a session.
lsof -i -P -n -p $(pgrep -f mneme) | grep -v LISTEN

# Windows: netstat scoped to the mneme process.
Get-NetTCPConnection | Where-Object { $_.OwningProcess -eq (Get-Process mneme).Id }
```

In the lite profile, and in standard with `compression_enabled: false`, all three commands return zero matches outside localhost.

## Compliance Posture

mneme is not a data controller or data processor under GDPR Article 4 because no personal data is collected, transmitted, or stored by mneme infrastructure (there is none). The user is the controller of their vault. The user is responsible for any opt-in LLM compression endpoint they configure (the endpoint operator becomes a processor under the user's direct contractual relationship, not mneme's).

CCPA, PIPEDA, LGPD, and similar regimes follow the same posture: data residency is on the user's local disk only.

If you are using mneme in a regulated context (clinical records, legal discovery, attorney-client privileged material), configure the `<private>` redaction tags around any restricted text and rely on the audit log for retention compliance documentation. The audit log entries store hashes only, not content, so the audit log itself does not need to be treated as restricted material.

## Reporting Privacy Concerns

If you discover an undocumented network call or unexpected file access, please report via the channels in `SECURITY.md`.
