# MCP Tool Reference

mneme-mcp exposes six tools over stdio in every install profile. All names are prefixed with `mneme_` to avoid namespace clash with other MCP servers in the same client. Lite uses the local FTS5 baseline. Standard and full profile components add derived indexes and graph state that the core package can consume without changing MCP tool names.

## Tools

### mneme_search

Retrieval across the vault. **v1.0 MCP server**: FTS5 BM25 with Turkish casefold normalization. The hybrid RRF fusion documented at the `mneme-core` Python API (FTS5 plus LEANN dense plus Graphiti temporal KG, `k=60`) is consumed by the build-time indexer and benchmark suite. Callers needing direct index maintenance should use `mneme-core`.

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

Summarize a topic across multiple sessions. v1.0 groups FTS5 hits by directory and returns source-backed sections. The output shape is stable for future graph expansion.

**Input**: `{ "topic": "string", "date_range": ["ISO8601 date", "ISO8601 date"] }`.

**Output**: a topic markdown document with citations to source sessions.

### mneme_timeline

Temporal query for a subject. v1.0 returns FTS5 hits ordered by mtime. Full-profile graph state can add bi-temporal semantics in later releases without renaming the tool.

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

## Error Handling

All tools return a structured error envelope on failure rather than throwing a raw exception. The envelope is callable-by-Zod-schema and stable across v1.x.

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_ARGUMENT | UNKNOWN_TOOL | INDEX_NOT_FOUND | PATH_OUTSIDE_VAULT | FEATURE_UNAVAILABLE | QUERY_TOO_SHORT | IO_ERROR",
    "message": "human-readable description"
  }
}
```

| Code | Meaning | Recovery |
|---|---|---|
| `INVALID_ARGUMENT` | Zod validation failed or a required argument is missing. | Fix the request payload against the tool schema. |
| `UNKNOWN_TOOL` | The client requested a tool name this server does not expose. | Call `tools/list` and use one of the advertised `mneme_*` names. |
| `INDEX_NOT_FOUND` | FTS5 sqlite is missing. | Run `mneme-core index rebuild`. |
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
