# MCP Tool Reference

mneme-mcp exposes six tools over stdio. All names are prefixed with `mneme_` to avoid namespace clash with other MCP servers in the same client.

## Tools

### mneme_search

Retrieval across the vault. **v1.0 MCP server**: FTS5 BM25 only. The hybrid RRF fusion documented at the `mneme-core` Python API (FTS5 plus LEANN dense plus Graphiti temporal KG, `k=60`) is consumed by the build-time indexer and benchmark suite; wiring dense and KG backends into the MCP server is scheduled for v1.1. For v1.0, callers needing hybrid retrieval should drive `mneme-core` directly from Python.

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
  "results": [
    {
      "path": "vault/sessions/2026-05-19/14-32-11-rrf-fusion.md",
      "score": 0.873,
      "snippet": "decided to fuse FTS5 and dense embeddings with RRF k=60...",
      "frontmatter": {"type": "session", "tags": ["retrieval", "rrf"]}
    }
  ],
  "rrf_k": 60,
  "backends_used": ["fts5", "leann"]
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

Summarize a topic across multiple sessions. Walks the knowledge graph (full profile) or runs an LLM-assisted summarization if opted in.

**Input**: `{ "topic": "string", "date_range": ["ISO8601 date", "ISO8601 date"] }`.

**Output**: a topic markdown document with citations to source sessions.

### mneme_timeline

Temporal query against the bi-temporal knowledge graph. Available only in the full profile.

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
  "error": {
    "code": "VAULT_NOT_FOUND | PATH_TRAVERSAL | INVALID_INPUT | INDEX_NOT_BUILT | PROFILE_MISMATCH | INTERNAL",
    "message": "human-readable description",
    "context": {"vault_path": "/path/that/was/tried"}
  }
}
```

| Code | Meaning | Recovery |
|---|---|---|
| `VAULT_NOT_FOUND` | Configured vault path does not exist or is unreadable. | Set `MNEME_VAULT` or run `mneme install`. |
| `PATH_TRAVERSAL` | `mneme_write` target resolved outside the vault root. | Use a path inside the vault. |
| `INVALID_INPUT` | Zod validation failed on the request payload. | Inspect `context.zod_issues`. |
| `INDEX_NOT_BUILT` | FTS5 sqlite missing or stale. | Run `mneme rebuild-indexes`. |
| `PROFILE_MISMATCH` | Tool requires standard or full profile but install is lite. | `mneme install --upgrade-profile=...`. |
| `INTERNAL` | Unexpected exception. | Inspect `~/.mneme/audit.log`. |

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
