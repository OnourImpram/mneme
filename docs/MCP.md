# MCP Tool Reference

mneme-mcp exposes six tools over stdio. All names are prefixed with `mneme_` to avoid namespace clash with other MCP servers in the same client.

## Tools

### mneme_search

Retrieval across the vault. **Shipped v1.0 MCP server**: FTS5 BM25 only. **Gated**: summarize and timeline can add Graphiti fields when full-profile KG state and local Neo4j are active. **Roadmap**: packaged dense LEANN retrieval inside MCP search. The RRF fusion protocol is available in `mneme-core` for Python callers and in Benchmark A through a deterministic BoW surrogate.

**Input schema**:

```json
{
  "query": "string (required)",
  "top_k": "integer (default 10)",
  "filters": {
    "date_from": "ISO8601 date (optional)",
    "date_to": "ISO8601 date (optional)",
    "tags": ["string array (optional)"],
    "type": "session|topic|reference|pattern|trajectory|observation (optional)"
  }
}
```

**Output**: ranked list of `{path, score, snippet, frontmatter}` records.

**Example output**:

```json
{
  "ok": true,
  "data": {
    "query": "rrf fusion",
    "hits": [
      {
        "path": "sessions/2026-05-19/14-32-11-rrf-fusion.md",
        "title": "RRF fusion decision",
        "score": -1.72,
        "snippet": "decided to fuse FTS5 and dense embeddings with RRF k=60...",
        "type": "session",
        "mtime": 1789727531
      }
    ]
  }
}
```

### mneme_recall

Recall a specific session by id or date.

**Input**: `{ "session_id": "string" }` or `{ "date": "ISO8601 date" }`.

**Output**: full session markdown with frontmatter.

### mneme_write

Append a structured section into the vault. Honors privacy redaction (constraint C4).

**Input**:

```json
{
  "path": "string (required, must be within vault root)",
  "section": "string (required)",
  "content": "string (required)",
  "frontmatter": "object (optional)"
}
```

`assertWithinVault` is enforced before any disk write to prevent path traversal.

### mneme_prime

Inject preflight context for a session. Combines recent sessions, relevant topics, and active references using the Adaptive Context Layer for format selection.

**Input**: `{ "task_description": "string", "budget_tokens": "integer (default 4000)" }`.

**Output**: prepared markdown ready for inclusion in the session preamble.

### mneme_summarize

Summarize a topic across multiple sessions. Shipped default groups FTS5 matches by directory. Gated full-profile KG enrichment can add `related_entities` when the KG active flag and local Neo4j are present. The tool does not call an LLM in v1.0.

**Input**: `{ "topic": "string", "date_range": ["ISO8601 date", "ISO8601 date"] }`.

**Output**: a topic markdown document with citations to source sessions.

### mneme_timeline

Temporal query for a subject. Shipped default returns FTS5 hits sorted by mtime. Gated full-profile KG enrichment can add bi-temporal Graphiti facts and apply `as_of` semantics when the graph is active.

**Input**:

```json
{
  "subject": "string (entity or concept)",
  "valid_from": "ISO8601 date (optional)",
  "valid_to": "ISO8601 date (optional)",
  "as_of": "ISO8601 date (optional, default now)"
}
```

**Output**: ordered timeline of state changes with provenance to source sessions.

### mneme_propose

Queue a memory-edit proposal (create, update, or delete) for the policy drain. The server never applies an agent-initiated edit directly: the proposal is staged as one JSONL record under `.mneme/proposals/pending.jsonl` and applied later by the policy engine (`mneme-core memory drain`, also run automatically at SessionEnd when a `policy.json` exists). Ephemeral edits whose `edit_class` is in the operator's `policy.json` allow-list apply autonomously with a rollback journal and a tamper-evident audit-chain record; everything else is held for the human approval flow. Durable categories (`identity`, `preference`, `clinical`, `legal`, `financial`) are never auto-applied. Content is redacted before it is queued (C4).

**Input**:

```json
{
  "action": "create | update | delete",
  "path": "string (vault-relative)",
  "content": "string (proposed full file content; ignored for delete)",
  "category": "ephemeral | identity | preference | clinical | legal | financial",
  "edit_class": "dedup-merge | typo-fix | tag-normalize | supersede-link | stale-archive (optional)"
}
```

**Output**: `{ "proposal_id", "status": "queued", "auto_eligible", "redactions_applied", "note" }`. `auto_eligible` reports whether the current policy would apply the edit autonomously at the next drain. Rollback: `mneme-core memory rollback <change_id>`.

## Configuration

```json
{
  "mcpServers": {
    "mneme": {
      "command": "mneme-mcp",
      "args": [],
      "env": {
        "MNEME_VAULT": "/path/to/vault"
      }
    }
  }
}
```

## Empty Results and Short Queries

Very short or empty queries — for example `"hi"` or `"ok"` — return no results by design. The retrieval pipeline gates queries whose stripped length is below `min_query_length` (default 3 characters) **or** that contain fewer than `min_query_words` meaningful tokens after stopword removal (default 1). A blank result on a tiny query is therefore expected and explainable; send a more descriptive query to get hits.

## Error Handling

All tools return a structured error envelope on failure rather than throwing a raw exception. The envelope is callable-by-Zod-schema and stable across v1.x.

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_ARGUMENT | UNKNOWN_TOOL | INDEX_NOT_FOUND | INDEX_STALE_OR_LOCALE_MISMATCH | PATH_OUTSIDE_VAULT | FEATURE_UNAVAILABLE | QUERY_TOO_SHORT | IO_ERROR",
    "message": "human-readable description"
  }
}
```

| Code | Meaning | Recovery |
|---|---|---|
| `INVALID_ARGUMENT` | Zod validation failed or a required argument is missing. | Fix the request payload against the tool schema. |
| `UNKNOWN_TOOL` | The client requested a tool name this server does not expose. | Call `tools/list` and use one of the advertised `mneme_*` names. |
| `INDEX_NOT_FOUND` | FTS5 sqlite is missing. | Run `mneme-core index rebuild`. |
| `INDEX_STALE_OR_LOCALE_MISMATCH` | The index was built with a different locale normalizer than the query path expects. | Run `mneme index rebuild --locale tr` to rebuild with the correct profile. |
| `PATH_OUTSIDE_VAULT` | `mneme_write` target resolved outside the vault root. | Use a relative path inside the vault. |
| `FEATURE_UNAVAILABLE` | The requested feature is unavailable in the current local configuration. | Enable the needed local profile or disable the feature-specific call. |
| `QUERY_TOO_SHORT` | The query is below the configured gating threshold. | Send a longer query or lower the threshold. |
| `IO_ERROR` | Unexpected filesystem, database, or runtime failure. | Inspect the client stderr and local mneme audit files. |

See `packages/mneme-mcp/src/errors.ts` for the canonical Zod schema.

## Companion CLI: mneme-migrate

Distributed alongside the MCP server in the same npm package (`mneme-mcp`), exposed as a second `bin` entry.

```bash
mneme-migrate migrate-from-claude-mem \
  --source ~/.claude-mem/db.sqlite \
  --vault ~/mneme-vault \
  --archive copy
```

Subcommands and exit codes:

- `migrate-from-claude-mem`: full migration, exit 0 on success, 1 on partial failure, 2 on invalid arguments.
- `--archive {preserve|copy|move}`: tri-state archive behavior. `move` additionally requires `--confirm-delete`.
- `--dry-run`: parse and report what would happen without writing to the vault.

See `docs/MIGRATION-FROM-CLAUDE-MEM.md` for the full walkthrough.
