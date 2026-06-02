# mneme-mcp-server

TypeScript MCP server for [mneme](https://github.com/TheGoatPsy/mneme). The npm package is `mneme-mcp-server`; it installs the `mneme-mcp` command.

Provides six tools to any MCP-compatible client (Claude Code, Cursor, Cline, Continue, Goose):

| Tool | Purpose |
|---|---|
| `mneme_search` | Hybrid retrieval across vault with RRF fusion. |
| `mneme_recall` | Recall a specific session by id or date. |
| `mneme_write` | Append a structured section into the vault. |
| `mneme_prime` | Inject preflight context at session start. |
| `mneme_summarize` | Summarize a topic across multiple sessions. |
| `mneme_timeline` | Temporal query against the knowledge graph. |

## Installation

```bash
npm install -g mneme-mcp-server   # installs the `mneme-mcp` command
```

Or use via the Claude Code plugin (recommended): see `packages/mneme-cc-plugin`.

## Configuration

Add to your MCP client config:

```json
{
  "mcpServers": {
    "mneme": {
      "command": "mneme-mcp",
      "args": []
    }
  }
}
```

The server uses stdio transport and reads vault location from the standard mneme config search order.

## License

MIT. See LICENSE in the repository root.
