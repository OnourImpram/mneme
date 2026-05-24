# /mneme:migrate

Import existing memory data from another memory plugin into the vault.

## Usage

```
/mneme:migrate from=claude-mem [path=<custom path>]
```

## What it does

Invokes `mneme migrate-from-<source>` under the hood. The migration:

1. Reads the source memory store at the default or supplied path.
2. Maps each record into a markdown document with frontmatter that
   matches the vault contract.
3. Writes the converted documents under `vault/migrated/<source>/`.
4. Rebuilds the FTS5 index incrementally so the imported documents
   are searchable immediately.
5. Reports per-record success and any skipped or malformed entries.

The original source store is not modified. If the migration is
interrupted, re-running is safe: existing destination files are
detected by content hash and not overwritten.

## Supported sources at v1.0

- `claude-mem` (SQLite observation database).

Future sources (`mem0`, `supermemory`, `episodic-memory`, `letta`,
`zep`) ship in v1.1 and later.

## Verification

After migration, run `/mneme:search <known fact>` to confirm the
imported records are indexed. Or use the standalone CLI:

```
mneme doctor --vault <your-vault>
```
