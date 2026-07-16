# Cookbook

Ten worked recipes demonstrating mneme in realistic Claude Code sessions. Each recipe shows the command, the expected output, and the vault artifact produced. Transcripts are illustrative, not literal byte-for-byte captures.

Profiles required per recipe are marked in the heading. Recipes assume `MNEME_VAULT` is set or that a `.mneme/` marker exists in the working directory.

---

## 1. First Install and Prime (lite)

Install the lite profile, then run `/mneme:prime` to inject preflight context for a new session.

```bash
pipx install mneme-cc-plugin
mneme install --profile=lite
mneme doctor
```

Expected `doctor` output:

```
mneme doctor
============
profile: lite                              OK
vault:   /home/u/mneme-vault               OK
indexes: fts5.sqlite (0 docs)              OK
hooks:   5 of 5 registered in settings.json  OK
```

In Claude Code, type `/mneme:prime` at the top of a fresh session. The hook injects up to `budget_tokens` (default 4000) of relevant context from the vault. On a fresh install with an empty vault, the injection is a one-line marker. As the vault fills, `mneme_prime` selects across sessions, topics, patterns, and trajectories using the Adaptive Context Layer.

---

## 2. Migrate from claude-mem (lite)

One-command import with idempotent re-run and audit trail.

```bash
mneme-migrate migrate-from-claude-mem \
  --source ~/.claude-mem/db.sqlite \
  --vault ~/mneme-vault \
  --archive copy
```

Output:

```
[mneme-migrate] reading from /home/u/.claude-mem/db.sqlite
[mneme-migrate] 1754 observations, 312 sessions, 4180 prompts
[mneme-migrate] redacted 27 <private> blocks
[mneme-migrate] wrote 1754 observations to vault/imported/claude-mem/
[mneme-migrate] wrote _manifest.json
[mneme-migrate] archived source to vault/imported/claude-mem/_archive/
[mneme-migrate] done in 4.1s
```

Re-running the same command produces:

```
[mneme-migrate] 0 new, 1754 dedup-skipped (content_hash match)
[mneme-migrate] manifest unchanged
```

Inspect the audit trail:

```bash
cat ~/mneme-vault/imported/claude-mem/_manifest.json | jq .stats
```

---

## 3. Temporal Query Across Decisions (full)

Bi-temporal queries via `mneme_timeline` answer "what was decided about X between dates A and B?".

In Claude Code:

```
/mneme:recall RRF fusion approach
```

Triggers `mneme_timeline` under the hood with `subject="RRF fusion approach"`. Output:

```
2026-03-12  initial proposal: use BM25 only, defer dense
2026-04-02  decision: keep FTS5 shipped, test RRF fusion, prepare dense adapter
2026-05-19  ratified in ADR-003 of mneme architecture record
```

Each row links to the originating session in the vault. Use `valid_from` and `valid_to` filters to scope the timeline:

```json
{"subject": "RRF fusion approach", "valid_from": "2026-04-01", "valid_to": "2026-04-30"}
```

This recipe requires the full profile because temporal queries traverse the Graphiti episode graph.

---

## 4. Multilingual Search with Turkish Casefold (lite)

The pure-Python Turkish casefold normalizer handles the famous edge case where `.lower()` fails on dotless capital I.

```bash
mneme index rebuild --locale=tr
# Then call the MCP tool mneme_search with:
# "KIYASLAMA", "kıyaslama", "kiyaslama"
```

All three queries return the same result set. The indexer normalizes both query and document using `mneme_core.fts5.locale.tr.normalize_tr`, which preserves dotted/dotless I distinction correctly. Standard Python `.lower()` fails on `KIYASLAMA` because it produces `kiyaslama` instead of `kıyaslama`, splitting the result set in two.

Verified by `tests/unit/test_tr_normalize.py` (5 of 5 pass including the `KIYASLAMA` edge case).

---

## 5. Private Content Redaction (lite)

Constraint C4: anything inside `<private>...</private>` tags is stripped at staging write and never reaches the vault, index, or knowledge graph.

In Claude Code:

```
Add a note: my password is <private>hunter2-correct</private> and the API key is <private>sk-redacted</private>.
```

The PostToolUse hook fires `mneme_core.compression.staging.staging_capture`, which:

1. Matches both `<private>...</private>` segments.
2. Writes SHA256-derived hashes of the redacted content to `vault/.mneme/audit/YYYY-MM-DD.jsonl`.
3. Replaces the segments with `<REDACTED>` in the staging JSONL.

Verify zero leakage:

```bash
grep -r "hunter2\|sk-redacted" ~/mneme-vault ~/.mneme  # expected: no matches
mneme audit-log --since=today                           # expected: 2 redaction entries
```

---

## 6. Token-Efficient Re-Injection (lite)

`distill.compressed_format` chooses among `full`, `keypoints`, and `ref` based on context window pressure and whether the content has been injected before.

First mention in a session:

```
[mneme_prime full]
## RRF Fusion Decision (vault/sessions/2026-04-02/...)

Decided to test RRF fusion over FTS5 plus a deterministic surrogate because...
[full markdown body, 420 tokens]
```

Re-injection in the same session, pressure rising:

```
[mneme_prime keypoints]
## RRF Fusion Decision
- Fused FTS5 + BoW surrogate in Benchmark A
- RRF k=60
- Rationale: 9.2-point nDCG@5 improvement
[195 tokens, 46% of full]
```

Third mention, heavy pressure:

```
[mneme_prime ref]
See vault://sessions/2026-04-02/RRF-fusion.md
[50 tokens, 12% of full]
```

Benchmark C measures this: keypoints 46 percent, ref 12 percent vs full.

---

## 7. Background Compression Opt-In (lite, requires API key)

Compression is opt-in by design. Default is off so users never face a surprise bill.

```bash
mneme compress enable --cost-cap-usd-monthly=5
mneme compress status
```

Output:

```
compression_enabled: true
provider: anthropic (lazy-loaded)
monthly_cap_usd: 5.00
month_to_date_spend: 0.00
sessions_pending: 12
```

Compression runs in the background after SessionEnd, never on the Stop critical path. The cost ledger tracks every API call. When the cap is reached, compression pauses until the next month or until the user raises the cap.

```bash
mneme compress dry-run
# prints estimated token count and dollar cost without calling the API
```

---

## 8. Vault Diff Review (lite)

Because the vault is plain markdown, `git diff` works on it.

```bash
cd ~/mneme-vault
git init
git add . && git commit -m "snapshot 2026-05-19"
# work happens
git diff HEAD
```

The diff is human-readable. Reviewing what mneme wrote during a session is the same operation as reviewing a code change. This is the literal meaning of "markdown is ground truth".

Combine with `git log -p --since=yesterday` to audit a day's memory captures.

---

## 9. Multi-Workspace Memory (lite)

Two project directories can share a single vault via the `MNEME_VAULT` environment variable.

```bash
# Project A
cd ~/work/project-a
export MNEME_VAULT=~/shared-mneme-vault
mneme install --profile=lite

# Project B (same vault)
cd ~/work/project-b
export MNEME_VAULT=~/shared-mneme-vault
mneme install --profile=lite
```

Sessions from both projects index into the same FTS5 database. Use frontmatter `tags: [project-a]` or `tags: [project-b]` and a search filter to disambiguate.

To isolate per-project, omit `MNEME_VAULT` and let the parent-directory `.mneme/` marker resolve the vault location, the same way `git` resolves the repository root.

---

## 10. Cross-Tool MCP Use (lite)

`mneme-mcp` is an ordinary MCP stdio server. Any MCP-compatible client (Cursor, Cline, Continue, Goose) can connect to it alongside Claude Code.

Cursor `mcp.json`:

```json
{
  "mcpServers": {
    "mneme": {
      "command": "mneme-mcp",
      "args": [],
      "env": { "MNEME_VAULT": "/home/u/mneme-vault" }
    }
  }
}
```

Both clients now see the same nine `mneme_*` tools. Search results are consistent because they share the same vault and indexes. The `mneme_` prefix on tool names guarantees no namespace clash with other MCP servers registered in the same client.

This is the "vault-native, client-agnostic" half of the thesis: the vault outlives any single client.


## 11. Policy-Graduated Autonomy (lite)

By default the agent applies nothing on its own: no `policy.json`, zero autonomy. Opt in per edit class.

```bash
mneme memory policy init          # writes a documented zero-autonomy starter
# edit .mneme/policy.json, e.g. "auto_approve": ["typo-fix", "tag-normalize"]
mneme memory policy validate      # exit 1 on typos in class names
mneme memory policy               # show the resolved policy
```

Queued proposals (from the `mneme_propose` MCP tool or `propose()`) then drain at session end. Every applied edit lands in the rollback journal and the tamper-evident audit chain:

```bash
mneme memory changes              # journal of autonomous edits
mneme memory rollback <change-id> # one-command undo
```

Durable categories (identity, preference, clinical, legal, financial) always require a human regardless of the policy file.

## 12. Team Vault Sync, Self-Hosted (lite)

Shared memory over any git remote you already trust. No vendor, no account.

```bash
# one-time, per member: .mneme/sync.json
# { "remote_url": "ssh://git.internal/team-vault.git", "member": "alice" }
mneme sync status
mneme sync push                   # redacts EVERY file before it leaves the machine
```

Push builds a separate share tree, redacts each markdown body, rescans it, and aborts fail-closed if a `<private>` span survived. Teammates import with:

```bash
mneme sync pull                   # lands under team/<member>/, never overwrites
```

Imports are redacted again on arrival and trust-marked (`source: team-sync`, `trust: external`, `payload_sha256`), so retrieval treats teammate notes as data, never instructions. A changed remote payload surfaces as a `.conflict` sidecar you resolve in markdown.

## 13. Memory Blame and Time-Travel (lite)

git-blame for memories: where a claim came from, what superseded it, and what was true on a given date.

```bash
mneme temporal index              # build or refresh the claims table
mneme temporal blame notes/decisions/retrieval.md
mneme temporal as-of 2026-05-01T00:00:00Z
mneme temporal contradictions    # claim pairs with overlapping validity
```

`blame` accepts a claim id or a vault path and prints the ancestor chain, descendants, and rival claims sharing the same key.

## 14. Read-Only Web Console (lite)

```bash
mneme-console --serve             # http://127.0.0.1:7421/, Ctrl+C to stop
```

Tabs for the vault audit, the code graph, temporal claims with supersedes chains, the autonomous-edit journal, and audit-chain verification. GET-only, loopback-only, Host-header pinned against DNS rebinding, zero external requests. The static report mode (`mneme-console --json` or plain HTML) still works for air-gapped review.

## 15. Deterministic Session Summaries, Localized (lite)

Session-end summaries are extractive and zero-LLM, on by default. Configure per vault in `.mneme/summary.json`:

```json
{ "deterministic": true, "language": "tr", "max_files": 12 }
```

Set `"deterministic": false` to fall back to the 2.x placeholder behaviour. The opt-in LLM compression layer (`mneme compress`) is independent and accepts the same `language` for its Turkish rubric.

## 16. Enable the Context Continuity Engine (lite)

The CCE is opt-in and off by default. Enable it by writing a config file to your vault.

```bash
# Write the CCE config (all fields shown with their defaults)
cat > "$MNEME_VAULT/.mneme/cce.json" << 'EOF'
{
  "enabled": true,
  "fill_threshold": 0.65,
  "budget_tokens": 4000,
  "max_checkpoints": 20,
  "large_response_bytes": 8192
}
EOF
```

The minimal enable is just `{"enabled": true}` — all other fields default as shown above.

**What to expect**: on every `UserPromptSubmit` the engine estimates context fill from the JSONL transcript. When fill crosses 65% (or a salient event fires first), it writes a checkpoint markdown file:

```
vault/.mneme/checkpoints/2026-06-14-decisions.md
vault/.mneme/checkpoints/2026-06-14-touched-files.md
...
vault/.mneme/checkpoints/checkpoints.jsonl   ← JSONL index sidecar
```

Salient events that trigger a checkpoint regardless of fill:

- An explicit keyword in your prompt: "remember this", "hatirla", or similar.
- A git commit detected in a Bash tool response.
- A tool response larger than 8 KB serialized.
- The `PreCompact` hook firing (the host is about to compact).

Each checkpoint is a plain markdown file with YAML frontmatter and kind-sectioned bullet body. It is git-visible and Obsidian-readable — the same vault-native ground truth as every other mneme artifact.

**Inspect checkpoints** using the MCP tool:

```
mneme_checkpoint_list
```

Returns the JSONL index as structured data: anchor, timestamp, path, and item count for every checkpoint. You can also browse `vault/.mneme/checkpoints/` directly in any text editor, `git log`, or Obsidian.

**Load a checkpoint** to re-inject its working-set items into the current session:

```
mneme_working_set_load  anchor="2026-06-14-decisions"
```

The engine scores each item with `salience.score_item` (a weighted sum clamped to [0, 1]), sorts descending, and injects items into the session within the configured token budget (default 4 000 tokens). Only items not already present in the current transcript are injected.

**What happens automatically after a compaction**: on the next `SessionStart` after a compaction event, the engine loads the latest checkpoint, compares it against the post-compaction transcript using normalized string search, identifies dropped items, and re-injects only the missing ones — salience-ranked, within the token budget. No manual action required.

Expected SessionStart output when dropped items are detected:

```
[mneme CCE] latest checkpoint: 2026-06-14-decisions (14 items)
[mneme CCE] detected 6 dropped items after compaction
[mneme CCE] rehydrated 6 items (1 843 tokens, budget 4 000)
```

If no checkpoint exists yet, or nothing was dropped, the CCE path is silent.

**Disabling**: set `enabled` to `false` in `cce.json` or delete the file entirely. All CCE hooks exit immediately when the config is absent or disabled — zero overhead.
