"""Derived and inferred analytics over the extracted mneme code graph.

All functions in this module are PURE: they operate on list[GraphNode] and
list[GraphEdge], never mutate their inputs, perform no IO, make no network
calls, and are fully deterministic.

Confidence-label invariant
--------------------------
Nodes and edges stored in the graph carry ``confidence="EXTRACTED"`` because
they were directly observed from source-code ASTs.  The outputs of this
module are one level removed:

* ``Community`` objects are **INFERRED** — connected-component membership is
  derived from structural patterns, not directly observable in any single
  source location.
* ``MergeCandidate`` objects are **AMBIGUOUS** — duplicate detection is based
  on heuristics (same name/kind/path, different ``line_start``); the evidence
  is present but may be contradictory or incomplete.
* ``ImpactResult`` and ``apply_merge`` return plain node/edge data; callers
  retain whatever confidence labels the underlying objects carry.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from .schema import ConfidenceLabel, GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Community detection (connected components)
# ---------------------------------------------------------------------------

_COMMUNITY_EDGE_KINDS = frozenset({"calls", "inherits", "imports"})


@dataclass(frozen=True)
class Community:
    """A connected component in the undirected graph projection.

    Attributes:
        community_id  Lexicographically smallest ``node_id`` in the component.
        node_ids      Sorted tuple of all ``node_id`` values in the component.
        confidence    Always ``"INFERRED"`` — membership is derived, not
                      directly observed.
    """

    community_id: str
    node_ids: tuple[str, ...]
    confidence: ConfidenceLabel = field(default="INFERRED")


def detect_communities(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> list[Community]:
    """Return connected components over the undirected projection of *nodes*.

    Only edges whose ``kind`` is in ``{"calls", "inherits", "imports"}`` are
    considered; ``"defines"`` edges are ignored.  Edges that reference a
    ``node_id`` not present in *nodes* are silently ignored.  Every node in
    *nodes* participates: an isolated node becomes a singleton community.

    Output is sorted by ``community_id``; ``node_ids`` within each community
    is sorted.  Calling this function twice with the same inputs produces
    identical results.
    """
    node_ids: set[str] = {n.node_id for n in nodes}

    # Build undirected adjacency list (only within known nodes).
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        if edge.kind not in _COMMUNITY_EDGE_KINDS:
            continue
        if edge.src_id not in node_ids or edge.dst_id not in node_ids:
            continue
        adj[edge.src_id].append(edge.dst_id)
        adj[edge.dst_id].append(edge.src_id)

    # BFS to find connected components.
    visited: set[str] = set()
    communities: list[Community] = []

    for start in sorted(node_ids):  # deterministic traversal order
        if start in visited:
            continue
        component: list[str] = []
        queue: deque[str] = deque([start])
        visited.add(start)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbour in adj[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
        sorted_ids = tuple(sorted(component))
        communities.append(
            Community(
                community_id=sorted_ids[0],
                node_ids=sorted_ids,
            )
        )

    return sorted(communities, key=lambda c: c.community_id)


# ---------------------------------------------------------------------------
# PR impact analysis (reverse BFS)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpactResult:
    """Result of a PR impact query.

    Attributes:
        changed   Sorted tuple of ``changed_node_ids`` that actually exist in
                  *nodes* (unknown ids are silently dropped).
        affected  Sorted tuple of ``(node_id, distance)`` pairs — nodes
                  reachable upstream from the changed set, excluding the
                  changed nodes themselves.  Sorted by ``(distance, node_id)``.
    """

    changed: tuple[str, ...]
    affected: tuple[tuple[str, int], ...]


def pr_impact(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    changed_node_ids: Iterable[str],
    *,
    max_depth: int | None = None,
) -> ImpactResult:
    """Return the nodes transitively affected by changes to *changed_node_ids*.

    Edge semantics: ``src -> dst`` means "``src`` depends on ``dst``".
    Changing ``dst`` therefore affects ``src``.  The traversal follows edges
    in the reverse direction (``dst -> src``), i.e. it finds all nodes that
    (directly or transitively) depend on any changed node.

    Args:
        nodes:             Full node list; defines the valid node universe.
        edges:             Full edge list.
        changed_node_ids:  Seed set.  Ids not found in *nodes* are ignored.
        max_depth:         Maximum hop distance to explore (``None`` = unbounded).

    Returns:
        ``ImpactResult`` with ``changed`` and ``affected`` fields.
    """
    node_ids: set[str] = {n.node_id for n in nodes}
    changed_set: set[str] = {nid for nid in changed_node_ids if nid in node_ids}

    # Build reverse adjacency: dst -> list[src] (who depends on dst?).
    rev_adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        if edge.src_id in node_ids and edge.dst_id in node_ids:
            rev_adj[edge.dst_id].append(edge.src_id)

    # BFS from changed_set, traversing reverse edges.
    distance: dict[str, int] = {}
    queue: deque[str] = deque()
    for nid in sorted(changed_set):  # deterministic seed order
        distance[nid] = 0
        queue.append(nid)

    while queue:
        current = queue.popleft()
        current_dist = distance[current]
        next_dist = current_dist + 1
        if max_depth is not None and next_dist > max_depth:
            continue
        for upstream in rev_adj[current]:
            if upstream not in distance:
                distance[upstream] = next_dist
                queue.append(upstream)

    affected: list[tuple[str, int]] = sorted(
        [(nid, dist) for nid, dist in distance.items() if nid not in changed_set],
        key=lambda t: (t[1], t[0]),
    )

    return ImpactResult(
        changed=tuple(sorted(changed_set)),
        affected=tuple(affected),
    )


def changed_nodes_for_paths(
    nodes: list[GraphNode],
    paths: Iterable[str],
) -> list[str]:
    """Return sorted, deduplicated ``node_id`` values for nodes in *paths*.

    Args:
        nodes:  Full node list to search.
        paths:  Iterable of ``source_path`` strings to match against.

    Returns:
        Sorted list of matching ``node_id`` values.
    """
    path_set: set[str] = set(paths)
    return sorted({n.node_id for n in nodes if n.source_path in path_set})


# ---------------------------------------------------------------------------
# Ghost-duplicate / merge candidate detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeCandidate:
    """A set of nodes that appear to represent the same logical symbol.

    Ghost duplicates arise when ``line_start`` drift causes the extractor to
    assign a new ``node_id`` to a symbol that has not meaningfully changed.
    Only **local** nodes (``source_path != "<external>"``) are considered.

    Attributes:
        key       ``(source_path, name, kind)`` grouping key.
        node_ids  Sorted tuple of duplicate ``node_id`` values (len >= 2).
        confidence  Always ``"AMBIGUOUS"`` — the heuristic may be wrong.
    """

    key: tuple[str, str, str]
    node_ids: tuple[str, ...]
    confidence: ConfidenceLabel = field(default="AMBIGUOUS")


def find_merge_candidates(nodes: list[GraphNode]) -> list[MergeCandidate]:
    """Detect ghost-duplicate local nodes sharing ``(source_path, name, kind)``.

    External nodes (``source_path == "<external>"``) are excluded because
    their ``node_id`` is already content-addressed without ``line_start`` and
    so they cannot produce duplicates by design.

    FIX 3: A group is only a merge candidate when its nodes span at least two
    DISTINCT ``content_hash`` values.  Nodes that share ``(source_path, name,
    kind)`` but have the SAME ``content_hash`` were extracted from the same
    file version — they are legitimately distinct symbols (e.g. two methods
    both named ``render`` in different classes) and must NOT be flagged.  A
    real ghost-duplicate (PA3) arises only across different file versions,
    which necessarily have different content hashes.

    Returns candidates sorted by ``key``; ``node_ids`` within each candidate
    is sorted.
    """
    # Map key -> list of (node_id, content_hash) pairs.
    groups: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for node in nodes:
        if node.source_path == "<external>":
            continue
        key = (node.source_path, node.name, str(node.kind))
        groups.setdefault(key, []).append((node.node_id, node.content_hash))

    candidates: list[MergeCandidate] = []
    for key, id_hash_pairs in groups.items():
        distinct_hashes = {chash for _, chash in id_hash_pairs}
        # Only emit a candidate when nodes span >= 2 distinct content_hash values
        # (cross-version ghost duplicate).  Same content_hash = same file version
        # = legitimately distinct symbols within that version → NOT a candidate.
        if len(distinct_hashes) < 2:
            continue
        deduped_ids = sorted({nid for nid, _ in id_hash_pairs})
        if len(deduped_ids) >= 2:
            candidates.append(
                MergeCandidate(
                    key=key,
                    node_ids=tuple(deduped_ids),
                )
            )

    return sorted(candidates, key=lambda c: c.key)


# ---------------------------------------------------------------------------
# Merge application
# ---------------------------------------------------------------------------


def _resolve_canonical(node_id: str, canonical_map: dict[str, str]) -> str:
    """Follow canonical_map transitively until fixpoint, handling cycles.

    For a simple chain (no cycle) the terminal id (not in canonical_map) is
    returned.  For a cycle (or a rho-shaped chain whose tail enters a cycle)
    the lexicographically smallest id among the cycle members is returned so
    that exactly one node in the cycle survives apply_merge.
    """
    seen_list: list[str] = []
    seen_set: set[str] = set()
    current = node_id
    while current in canonical_map:
        if current in seen_set:
            # Cycle detected — current is the entry point we've looped back to.
            # The cycle members are the suffix of seen_list starting from current.
            cycle_start = seen_list.index(current)
            cycle_members = seen_list[cycle_start:]
            return min(cycle_members)
        seen_list.append(current)
        seen_set.add(current)
        current = canonical_map[current]
    # No cycle — current is the terminal (not in canonical_map).
    return current


def apply_merge(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    canonical_map: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Apply a duplicate-to-canonical mapping, returning new (nodes, edges).

    *canonical_map* maps ``duplicate_node_id -> canonical_node_id``.

    Rules:
    * Nodes whose ``node_id`` is a key in *canonical_map* (and is not itself
      the canonical target after transitive resolution) are dropped.
    * Every edge's ``src_id`` and ``dst_id`` are rewritten through the map
      (transitively resolved).
    * After rewriting, a new ``edge_id`` is derived via ``GraphEdge.make``;
      duplicate edges are deduplicated (last writer wins on same ``edge_id``).
    * Self-loops introduced by merging (``src_id == dst_id``) are dropped.
    * The original *nodes* and *edges* lists are never mutated.
    * Output lists are deterministically sorted (nodes by ``node_id``, edges
      by ``edge_id``).

    Transitive resolution: if the map contains ``a -> b`` and ``b -> c`` then
    ``a`` resolves to ``c``.  Cycles in the map are broken at the first
    repeated node (the node keeps its current id).
    """
    # Resolve each key to its ultimate canonical target.
    resolved: dict[str, str] = {
        dup: _resolve_canonical(dup, canonical_map) for dup in canonical_map
    }

    # Non-canonical node ids: keys whose resolved target differs from themselves.
    non_canonical: set[str] = {dup for dup, canon in resolved.items() if dup != canon}

    # Filter nodes: drop non-canonical duplicates.
    new_nodes: list[GraphNode] = sorted(
        [n for n in nodes if n.node_id not in non_canonical],
        key=lambda n: n.node_id,
    )

    # FIX 1: surviving node ids — only nodes actually present in new_nodes.
    surviving_node_ids: set[str] = {n.node_id for n in new_nodes}

    # Rewrite and deduplicate edges.
    seen_edge_ids: dict[str, GraphEdge] = {}
    for edge in edges:
        new_src = resolved.get(edge.src_id, edge.src_id)
        new_dst = resolved.get(edge.dst_id, edge.dst_id)
        if new_src == new_dst:
            # Drop self-loops created by merging.
            continue
        # FIX 1: drop edges whose rewritten endpoint is not in the surviving set.
        if new_src not in surviving_node_ids or new_dst not in surviving_node_ids:
            continue
        new_edge = GraphEdge.make(
            src_id=new_src,
            dst_id=new_dst,
            kind=edge.kind,
            confidence=edge.confidence,
            valid_at=edge.valid_at,
        )
        seen_edge_ids[new_edge.edge_id] = new_edge

    new_edges: list[GraphEdge] = sorted(seen_edge_ids.values(), key=lambda e: e.edge_id)

    return new_nodes, new_edges
