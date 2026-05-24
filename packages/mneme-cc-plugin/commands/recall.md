# /mneme:recall

Retrieve a specific past session by id or by date range.

## Usage

```
/mneme:recall session=<session_id>
/mneme:recall from=<YYYY-MM-DD> to=<YYYY-MM-DD>
/mneme:recall                          # most recent sessions
```

## What it does

Calls the `mneme_recall` MCP tool. The tool reads only the FTS5 index
and the on-disk markdown files. No LLM call is made. Returns matching
sessions ranked by mtime descending with full markdown bodies when
the `include_body` argument is true (default).

## Use cases

- "What did we decide about authentication last Thursday?"
- "Pull up the session where I sketched the migration plan."
- "List the last five sessions, no bodies, just titles."

## Tips

- Combine with `/mneme:prime` when you want a curated bundle instead
  of raw session text.
- The session_id format follows your vault's frontmatter convention.
  If you do not know it, prefer `from`/`to` date ranges.
