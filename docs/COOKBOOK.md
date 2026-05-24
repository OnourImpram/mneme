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
2026-04-02  decision: add LEANN dense, fuse with RRF k=60
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
mneme search "KIYASLAMA"
mneme search "kıyaslama"
mneme search "kiyaslama"
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
2. Writes SHA256 hashes of the redacted content to `~/.mneme/audit.log`.
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

Decided to fuse FTS5 and dense embeddings with RRF k=60 because...
[full markdown body, 420 tokens]
```

Re-injection in the same session, pressure rising:

```
[mneme_prime keypoints]
## RRF Fusion Decision
- Fused FTS5 + LEANN dense
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
mneme compress enable --monthly-cap-usd=5
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

Compression runs in the background after Stop, never on the critical path. The cost ledger tracks every API call. When the cap is reached, compression pauses until the next month or until the user raises the cap.

```bash
mneme compress dry-run --session=2026-05-19T14-32-11
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

Both clients now see the same six `mneme_*` tools. Search results are consistent because they share the same vault and indexes. The `mneme_` prefix on tool names guarantees no namespace clash with other MCP servers registered in the same client.

This is the "vault-native, client-agnostic" half of the thesis: the vault outlives any single client.
