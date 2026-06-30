# P2-003 — GitHub Injected-Transport Connector Hardening

## Capability

Harden `GitHubConnector` with per-repo path allowlists, rate-limit
awareness, and an audit-log entry per successful fetch — without breaking
the injected-transport test contract or the existing `enabled=False` default.

## Current State

`packages/mneme-core/src/mneme_core/connectors_net.py`:

- `GitHubConnector` (lines 162-231) — fetches raw markdown from
  `raw.githubusercontent.com`.  Fields: `repo`, `paths`, `ref`, `token`,
  `transport`, `name`, `enabled`.
- `fetch()` (lines 196-231) — iterates `self.paths`, calls transport per
  path, returns `SourceDocument` list.  Per-path errors are swallowed;
  no allowlist check, no rate-limit, no audit log.
- `Transport = Callable[[str], str]` (line 47) — injected for testing;
  the real transport is `_urllib_transport` (lines 56-73), built lazily.

No allowlist, no rate-limit backoff, no audit trail.

## Proposed Design

### 1. Per-repo path allowlist

Add `allowed_path_patterns: frozenset[str] = field(default_factory=frozenset)`
to `GitHubConnector`.  Before fetching a path, check it against the allowlist
using `fnmatch.fnmatch`.  If the set is empty (default), all paths in
`self.paths` are permitted (current behavior).  If non-empty, only paths
that match at least one pattern proceed.

```python
# Example operator config:
GitHubConnector(
    repo="org/repo",
    paths=("docs/README.md", "docs/CHANGELOG.md"),
    allowed_path_patterns=frozenset({"docs/*.md"}),  # extra guard
)
```

### 2. Rate-limit awareness

Add `requests_per_minute: int = 0` (0 = no limit, current behavior).
When > 0, `fetch()` tracks a per-instance token-bucket (stdlib `time`
only) and sleeps the minimum interval between requests.  The transport
injection contract is unchanged — tests inject a no-sleep fake transport
and set `requests_per_minute=0`.

### 3. Audit-log entries per fetch

Add `audit_log: list[dict[str, str]] | None = field(default=None, ...)`.
After each successful fetch, append `{"ts": ISO, "repo": ..., "path": ...,
"ref": ..., "bytes": str(len(body))}` — no token, no content.  When
`audit_log` is `None` (default), logging is skipped (current behavior).
Callers inject a shared list to collect entries across multiple connectors.

### Extension Point

`connectors_net.py:196` — `GitHubConnector.fetch()`.  All three hardening
additions are confined to this method; the `Connector` Protocol (`name`,
`enabled`, `fetch`) is unchanged.

## Feature-Flag / Rollout Plan

All additions are opt-in via new dataclass fields that default to the
current behavior (`frozenset()`, `0`, `None`).  No env var or config key
required for the hardening itself.  The audit-log injection pattern is the
same as the transport injection already in use for tests.
