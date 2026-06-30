# P2-002 — Code Graph PR-Impact Richer Reports

## Capability

Extend `mneme-graph` PR-impact analysis to emit severity weighting per
affected node, a scalar blast-radius metric, and a suggested-reviewer list
derived from a caller-supplied owner map.

## Current State

`packages/mneme-graph/mneme_graph/analytics.py` ships:

- `ImpactResult` (lines 471-486) — frozen dataclass with two fields:
  `changed: tuple[str, ...]` and `affected: tuple[tuple[str, int], ...]`
  (node_id × BFS distance).
- `pr_impact()` (lines 487-545) — pure BFS over reversed edges; returns
  `ImpactResult`.  No severity, no blast-radius scalar, no reviewer hint.
- `changed_nodes_for_paths()` (lines 548-562) — maps file paths to node ids.

The existing interface is deliberately minimal and pure (no I/O, no network,
deterministic), which is the invariant to preserve.

## Proposed Design

Extend `ImpactResult` with three optional fields (backward-compatible because
`ImpactResult` is frozen with `field(default=...)`):

```python
@dataclass(frozen=True)
class ImpactResult:
    changed: tuple[str, ...]
    affected: tuple[tuple[str, int], ...]
    # New (all optional / computed on request):
    severity: tuple[tuple[str, str], ...] = ()
    # ^ (node_id, "HIGH"|"MEDIUM"|"LOW") — severity per affected node
    blast_radius: int = 0
    # ^ len(affected) as a convenience scalar for dashboards
    suggested_reviewers: tuple[str, ...] = ()
    # ^ callers inject an owner_map: dict[str, str] (node_id -> owner)
```

Add a `score_severity(node: GraphNode, distance: int) -> str` helper that
applies a simple tiered rule: distance 1 → HIGH, distance 2 → MEDIUM,
distance ≥ 3 → LOW.  The rule table is a module-level dict so callers can
override it without subclassing.

Extend `pr_impact()` signature:

```python
def pr_impact(
    nodes, edges, changed_node_ids, *,
    max_depth=None,
    owner_map: dict[str, str] | None = None,   # NEW
    severity_thresholds: dict[str, int] | None = None,  # NEW
) -> ImpactResult:
```

When `owner_map` is `None` (default), `suggested_reviewers` is `()`.
When `severity_thresholds` is `None`, the default tier table applies.
This keeps the function signature backward-compatible — all new kwargs
are keyword-only with `None` defaults.

## Extension Point

`analytics.py:487` — `pr_impact()` function signature and `ImpactResult`
(lines 471-486).  No other file needs to change for the design; the
`mneme-graph` CLI (`cli.py`) formats the output and would need a
`--owner-map FILE` flag added separately.

## Feature-Flag / Rollout Plan

No flag needed: new fields default to `()` / `0`, so existing callers
receive identical results.  Callers opt in by passing `owner_map`.
The severity scoring helper can be separately enabled via a
`MNEME_GRAPH_SEVERITY=1` env var in the CLI formatter until the API
stabilizes.
