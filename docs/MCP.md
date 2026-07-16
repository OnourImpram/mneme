# MCP Tool Reference

mneme-mcp exposes nine tools over stdio. All names are prefixed with `mneme_` to avoid namespace clash with other MCP servers in the same client. The JSON Schema returned by MCP `tools/list` is generated from the same Zod schema that validates tool calls. There is no separate hand-maintained public schema.

The scope-aware tools are `mneme_search`, `mneme_recall`, `mneme_summarize`, `mneme_timeline`, `mneme_prime`, and `mneme_propose`. Omit `scope` to use the configured default. Pass `"*"` only when an explicit cross-scope operation is intended.

## Tools

### mneme_search

Retrieval across the vault. **Shipped**: FTS5 BM25 only. **Gated**: summarize and timeline can add Graphiti fields when full-profile KG state and local Neo4j are active. **Roadmap**: packaged semantic dense retrieval inside MCP search. The feature-hashed lexical-vector adapter and RRF protocol are experimental Python-library and benchmark surfaces. They are not executed by `mneme_search`.

**Input schema**:

```json
{
  "query": "string (required)",
  "top_k": "integer (default 10)",
  "filters": {
    "date_from": "ISO8601 date (optional)",
    "date_to": "ISO8601 date (optional)",
    "type": "session|topic|reference|pattern|trajectory|compressed|observation|session_summary|user_prompt|claim|failure (optional)"
  },
  "min_query_length": "integer (default 0)",
  "scope": "string (optional; '*' is explicit cross-scope)"
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

**Input**: any combination of `session_id`, `date_from`, `date_to`, `top_n`, `include_body`, and `scope`. With no session or date filter, the tool returns the newest documents in the selected scope.

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

**Input**: `{ "task_description": "string", "budget_tokens": "integer (default 4000)", "recent_session_count": "integer", "topic_doc_count": "integer", "session_id": "string (optional)", "scope": "string (optional)" }`.

**Output**: prepared markdown ready for inclusion in the session preamble.

### mneme_summarize

Summarize a topic across multiple sessions. Shipped default groups FTS5 matches by directory. Gated full-profile KG enrichment can add `related_entities` when the KG active flag and local Neo4j are present. The tool does not call an LLM in v1.0.

**Input**: `{ "topic": "string", "date_range": ["ISO8601 date", "ISO8601 date"], "top_k": "integer", "scope": "string (optional)" }`.

**Output**: a topic markdown document with citations to source sessions.

### mneme_timeline

Temporal query for a subject. Shipped default returns FTS5 hits sorted by mtime. Gated full-profile KG enrichment can add bi-temporal Graphiti facts and apply `as_of` semantics when the graph is active.

**Input**:

```json
{
  "subject": "string (entity or concept)",
  "valid_from": "ISO8601 date (optional)",
  "valid_to": "ISO8601 date (optional)",
  "as_of": "ISO8601 date (optional; Graphiti facts only)",
  "top_k": "integer (default 25)",
  "scope": "string (optional; '*' is explicit cross-scope)"
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
  "edit_class": "dedup-merge | typo-fix | tag-normalize | supersede-link | stale-archive (optional)",
  "scope": "string (optional)"
}
```

**Output**: `{ "proposal_id", "status": "queued", "auto_eligible", "redactions_applied", "note" }`. `auto_eligible` reports whether the current policy would apply the edit autonomously at the next drain. Rollback: `mneme-core memory rollback <change_id>`.

### mneme_checkpoint_list

List recent Context Continuity Engine (CCE) checkpoints from the append-only index at `<vault>/.mneme/checkpoints/index.jsonl`. Returns up to `limit` entries newest-first. Use this to discover available anchors before calling `mneme_working_set_load`. A missing index file returns an empty list rather than an error.

**Input schema**:

```json
{
  "limit": "integer (default 20, maximum 200)"
}
```

**Output (found)**:

```json
{
  "ok": true,
  "data": {
    "entries": [
      {
        "anchor": "abc123",
        "id": "cp-001",
        "created": "2026-06-14T10:00:00Z",
        "session_id": "s-2026-06-14",
        "prev_anchor": null,
        "path": "checkpoints/2026-06-14-abc123.md",
        "item_count": 3,
        "top_salience": 0.92
      }
    ],
    "total_in_index": 1
  }
}
```

**Output (no index)**:

```json
{
  "ok": true,
  "data": { "entries": [], "total_in_index": 0 }
}
```

**Resolution strategy**: reads `<vault>/.mneme/checkpoints/index.jsonl`, skips malformed lines, returns the last `limit` entries sorted newest-first by the line order in the append-only file.

### mneme_working_set_load

Load the working-set items from a CCE checkpoint for cross-agent handoff or JIT context re-injection after compaction. Resolves the anchor via the checkpoint index, reads the checkpoint markdown, parses salience bullets, and returns items sorted by descending salience. An unknown anchor returns a `found: false` result, not an error.

**Input schema**:

```json
{
  "anchor": "string (required) — checkpoint anchor, e.g. 'abc123'",
  "top_k": "integer (optional, maximum 500) — return only the top_k items by salience"
}
```

**Output (found)**:

```json
{
  "ok": true,
  "data": {
    "anchor": "abc123",
    "items": [
      { "salience": 0.92, "text": "Use FTS5 BM25 for retrieval baseline", "section": "Core Decisions" },
      { "salience": 0.75, "text": "Turkish casefold normalization required", "section": "Core Decisions" }
    ],
    "total_items": 3,
    "truncated": false,
    "frontmatter": { "type": "checkpoint", "anchor": "abc123", "session_id": "s-2026-06-14" }
  }
}
```

**Output (not found)**:

```json
{
  "ok": true,
  "data": { "found": false, "reason": "anchor 'xyz' not found in index or checkpoint files" }
}
```

**Resolution strategy**: first looks up the anchor in `<vault>/.mneme/checkpoints/index.jsonl`; if found, reads the `path` field directly; if not in the index, falls back to a glob over `<vault>/checkpoints/*-<anchor>.md`. A missing markdown file after a successful index lookup returns `found: false`.

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

An empty query is rejected by Zod. `min_query_length` defaults to zero and can be raised by the caller as an explicit context-saving gate. Independently, FTS5 query construction discards stopwords and tokens shorter than two normalized characters. A non-empty query containing no surviving token returns an empty successful result with `backends_used: []`.

## Error Handling

All tools return a structured error envelope on failure rather than exposing a raw exception.

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
