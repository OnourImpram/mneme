"""Derived and inferred analytics over the extracted mneme code graph.

All functions in this module are PURE: they operate on list[GraphNode] and
list[GraphEdge], never mutate their inputs, perform no IO, make no network
calls, and are fully deterministic.

Confidence-label invariant
--------------------------
Nodes and edges stored in the graph carry ``confidence="EXTRACTED"`` because
they were directly observed from source-code ASTs.  The outputs of this
module are one level removed:

* ``Community`` objects are **INFERRED** — community membership is derived
  from modularity-maximising structure, not directly observable in any single
  source location.
* ``MergeCandidate`` objects are **AMBIGUOUS** — duplicate detection is based
  on heuristics (same name/kind/path, different ``line_start``); the evidence
  is present but may be contradictory or incomplete.
* ``ImpactResult`` and ``apply_merge`` return plain node/edge data; callers
  retain whatever confidence labels the underlying objects carry.
"""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from .schema import ConfidenceLabel, GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Community detection (greedy modularity-maximising agglomeration — CNM)
# ---------------------------------------------------------------------------

#: Edge kinds included by default.  Covers both the code graph
#: (calls / inherits / imports) and the vault note graph (links_to / tagged).
_COMMUNITY_EDGE_KINDS: frozenset[str] = frozenset(
    {"calls", "inherits", "imports", "links_to", "tagged"}
)


@dataclass(frozen=True)
class Community:
    """A cluster in the modularity-maximising partition of the graph.

    Attributes:
        community_id  Lexicographically smallest ``node_id`` in the cluster.
        node_ids      Sorted tuple of all ``node_id`` values in the cluster.
        confidence    Always ``"INFERRED"`` — membership is derived, not
                      directly observed.
    """

    community_id: str
    node_ids: tuple[str, ...]
    confidence: ConfidenceLabel = field(default="INFERRED")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_simple_adj(
    node_ids: frozenset[str],
    edges: list[GraphEdge],
    edge_kinds: frozenset[str],
) -> tuple[dict[str, list[str]], int]:
    """Return a simple undirected adjacency list and the edge count *m*.

    ``adj[nid]`` is a *sorted* list of distinct neighbours.  Self-loops,
    multi-edges between the same pair, and edges whose ``kind`` is not in
    *edge_kinds* or that reference unknown nodes are all silently ignored.
    *m* counts each undirected edge once.
    """
    adj_set: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for edge in edges:
        if edge.kind not in edge_kinds:
            continue
        if edge.src_id not in node_ids or edge.dst_id not in node_ids:
            continue
        if edge.src_id == edge.dst_id:
            continue
        adj_set[edge.src_id].add(edge.dst_id)
        adj_set[edge.dst_id].add(edge.src_id)

    adj: dict[str, list[str]] = {
        nid: sorted(nbrs) for nid, nbrs in adj_set.items()
    }
    # Each undirected pair (u, v) with u < v counted once.
    m: int = sum(
        1 for nid in sorted(node_ids) for nb in adj[nid] if nid < nb
    )
    return adj, m


def _delta_q(
    c1: str,
    c2: str,
    e_inter: dict[str, dict[str, int]],
    a_map: dict[str, int],
    m: int,
) -> float:
    """Newman modularity gain from merging communities *c1* and *c2*.

    ``dQ = e_c1c2 / m  -  a[c1] * a[c2] / (2 * m^2)``
    """
    eij: int = e_inter[c1].get(c2, 0)
    return eij / m - a_map[c1] * a_map[c2] / (2 * m * m)


def _cnm_partition(
    node_ids: frozenset[str],
    adj: dict[str, list[str]],
    m: int,
) -> list[frozenset[str]]:
    """Greedy Clauset-Newman-Moore modularity-maximising clustering.

    Starts with every node as its own singleton community and repeatedly
    merges the adjacent pair yielding the largest positive dQ.  Stops when
    the heap's best candidate has dQ <= 0.

    Determinism guarantees
    ~~~~~~~~~~~~~~~~~~~~~~
    * Adjacency lists and iteration order are always **sorted** (strings).
    * Heap entries are ``(-dQ, c_small, c_large)``; ties in dQ are broken by
      the lexicographically smaller pair — giving a total deterministic order.
    * When merging, the *survivor* is always the lex-min community id.
    * After each merge, dQ is recomputed for **all** current neighbours of
      the surviving community (not only the absorbed community's neighbours),
      because ``a[survivor]`` changes for every merge regardless.

    When *m* == 0 every node is its own singleton community (no edges to
    guide any merge).
    """
    if not node_ids:
        return []
    if m == 0:
        return [frozenset({nid}) for nid in node_ids]

    # Degree of each node in the simple undirected graph.
    degree: dict[str, int] = {nid: len(adj[nid]) for nid in node_ids}

    # a[c] = sum of degrees of nodes in community c.
    a: dict[str, int] = dict(degree)

    # e_inter[c1][c2] = number of simple undirected edges between c1 and c2.
    # Stored symmetrically; initially between singleton communities = nodes.
    e_inter: dict[str, dict[str, int]] = {nid: {} for nid in node_ids}
    for nid in sorted(node_ids):
        for nb in adj[nid]:
            if nid < nb:
                e_inter[nid][nb] = e_inter[nid].get(nb, 0) + 1
                e_inter[nb][nid] = e_inter[nb].get(nid, 0) + 1

    # Community membership sets.
    comm_members: dict[str, set[str]] = {nid: {nid} for nid in node_ids}
    active: set[str] = set(node_ids)

    # Min-heap of (-dQ, c_small, c_large).
    # best_dq tracks the current dQ for each pair to detect stale entries.
    heap: list[tuple[float, str, str]] = []
    best_dq: dict[tuple[str, str], float] = {}

    def _push(c1: str, c2: str) -> None:
        pair: tuple[str, str] = (c1, c2) if c1 < c2 else (c2, c1)
        dq = _delta_q(pair[0], pair[1], e_inter, a, m)
        best_dq[pair] = dq
        heapq.heappush(heap, (-dq, pair[0], pair[1]))

    # Initialise heap: only adjacent pairs, each counted once (c1 < c2).
    for c1 in sorted(node_ids):
        for c2 in sorted(e_inter[c1]):
            if c1 < c2:
                _push(c1, c2)

    # Greedy merge loop.
    while heap:
        neg_dq, h_c1, h_c2 = heapq.heappop(heap)
        dq = -neg_dq

        # Lazy deletion: skip if either community was already absorbed.
        if h_c1 not in active or h_c2 not in active:
            continue

        # Skip stale entries superseded by a later push for the same pair.
        h_key: tuple[str, str] = (h_c1, h_c2)
        if best_dq.get(h_key) != dq:
            continue

        # No further improvement possible; the heap is sorted by -dQ.
        if dq <= 0.0:
            break

        # Survivor = lex-min, absorbed = lex-max.
        survivor, absorbed = (h_c1, h_c2) if h_c1 < h_c2 else (h_c2, h_c1)

        # Merge membership and degree sum.
        comm_members[survivor].update(comm_members.pop(absorbed))
        a[survivor] += a.pop(absorbed)
        active.discard(absorbed)

        # Remove the internal survivor↔absorbed link.
        e_inter[survivor].pop(absorbed, None)

        # Collect absorbed's external links (everything except back-link to survivor).
        absorbed_nbrs: dict[str, int] = e_inter.pop(absorbed, {})
        absorbed_nbrs.pop(survivor, None)

        # Transfer absorbed's external edge counts to survivor.
        for c3, cnt in sorted(absorbed_nbrs.items()):
            if c3 not in active:
                # c3 itself was already absorbed; clean up if its dict still exists.
                if c3 in e_inter:
                    e_inter[c3].pop(absorbed, None)
                continue
            e_inter[c3].pop(absorbed, None)
            merged_cnt = e_inter[survivor].get(c3, 0) + cnt
            e_inter[survivor][c3] = merged_cnt
            e_inter[c3][survivor] = merged_cnt

        # Recompute dQ for ALL current active neighbours of survivor.
        # Must be all neighbours — not just those from absorbed — because
        # a[survivor] changed for every merge.
        for c3 in sorted(e_inter[survivor]):
            if c3 not in active:
                continue
            _push(survivor, c3)

        best_dq.pop(h_key, None)

    return [frozenset(members) for members in comm_members.values()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_communities(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    edge_kinds: frozenset[str] | None = None,
) -> list[Community]:
    """Return modularity-maximising communities over the undirected graph.

    Uses the greedy Clauset-Newman-Moore (CNM) agglomerative algorithm —
    pure Python stdlib, zero external dependencies, deterministic.

    *edge_kinds* controls which edge ``kind`` values contribute to clustering.
    Passing ``None`` (the default) uses ``_COMMUNITY_EDGE_KINDS``, which
    covers both the code graph (``calls``, ``inherits``, ``imports``) and the
    vault note graph (``links_to``, ``tagged``).

    Edges that reference a ``node_id`` not present in *nodes* are silently
    ignored.  Every node in *nodes* participates: an isolated node becomes a
    singleton community.

    The output list is sorted by ``community_id``; ``node_ids`` within each
    community is sorted.  Calling this function twice with identical inputs
    produces byte-identical results.
    """
    effective_kinds: frozenset[str] = (
        _COMMUNITY_EDGE_KINDS if edge_kinds is None else edge_kinds
    )
    node_id_set: frozenset[str] = frozenset(n.node_id for n in nodes)

    adj, m = _build_simple_adj(node_id_set, edges, effective_kinds)
    raw_clusters = _cnm_partition(node_id_set, adj, m)

    communities: list[Community] = []
    for cluster in raw_clusters:
        sorted_ids = tuple(sorted(cluster))
        communities.append(
            Community(
                community_id=sorted_ids[0],
                node_ids=sorted_ids,
            )
        )

    return sorted(communities, key=lambda c: c.community_id)


def modularity_score(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    communities: list[Community],
    *,
    edge_kinds: frozenset[str] | None = None,
) -> float:
    """Return Newman modularity Q for *communities* on the given graph.

    ``Q = Σ_c [ L_c / m  −  (d_c / 2m)² ]``

    where *L_c* is the number of edges **within** community *c* and *d_c*
    is the sum of degrees of its member nodes.

    Returns ``0.0`` when there are no edges (*m* == 0).  The theoretical
    range is ``[−0.5, 1.0]``; values near 0 indicate no more structure than
    a random graph; values above ~0.3 indicate significant community structure.

    Nodes not covered by any entry in *communities* are assigned to singleton
    communities of their own.  *edge_kinds* defaults to
    ``_COMMUNITY_EDGE_KINDS`` (same default as ``detect_communities``).
    """
    effective_kinds: frozenset[str] = (
        _COMMUNITY_EDGE_KINDS if edge_kinds is None else edge_kinds
    )
    node_id_set: frozenset[str] = frozenset(n.node_id for n in nodes)

    adj, m = _build_simple_adj(node_id_set, edges, effective_kinds)
    if m == 0:
        return 0.0

    two_m: int = 2 * m

    # Build node → community_id mapping; uncovered nodes become singletons.
    node_to_comm: dict[str, str] = {}
    for comm in communities:
        for nid in comm.node_ids:
            if nid in node_id_set:
                node_to_comm[nid] = comm.community_id
    for nid in node_id_set:
        if nid not in node_to_comm:
            node_to_comm[nid] = nid

    # Accumulate per-community degree sums and internal edge counts.
    comm_degree: dict[str, int] = {}
    comm_internal: dict[str, int] = {}

    for nid in node_id_set:
        cid = node_to_comm[nid]
        comm_degree[cid] = comm_degree.get(cid, 0) + len(adj[nid])

    for nid in sorted(node_id_set):
        for nb in adj[nid]:
            if nid < nb:  # count each undirected edge once
                c_nid = node_to_comm[nid]
                c_nb = node_to_comm[nb]
                if c_nid == c_nb:
                    comm_internal[c_nid] = comm_internal.get(c_nid, 0) + 1

    q: float = 0.0
    for cid, dc in comm_degree.items():
        lc = comm_internal.get(cid, 0)
        q += lc / m - (dc / two_m) ** 2

    return q


# ---------------------------------------------------------------------------
# Semantic projection (vault note / tag subgraph)
# ---------------------------------------------------------------------------

#: Node kinds retained in the semantic projection.
_SEMANTIC_NODE_KINDS: frozenset[str] = frozenset({"note", "tag"})

#: Edge kinds that carry semantic meaning in the vault note graph.
#: Heading structural edges (``has_heading``) are intentionally excluded.
_SEMANTIC_EDGE_KINDS: frozenset[str] = frozenset({"links_to", "tagged", "embeds"})


def build_semantic_projection(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Return the subgraph restricted to note/tag nodes and semantic edges.

    Heading nodes and code nodes (module, class, function, variable) are
    removed.  Only ``links_to``, ``tagged``, and ``embeds`` edges — and only
    those whose both endpoints survive the node filter — are kept.

    Rationale: on real vault graphs CNM on the full graph produces thousands of
    singleton communities because every heading node has degree 1 and the
    ``has_heading`` edges carry no topical signal.  The semantic projection
    removes those artefacts so that CNM finds clusters that reflect genuine
    thematic relationships between notes and tags.

    Args:
        nodes: Full node list.
        edges: Full edge list.

    Returns:
        ``(projected_nodes, projected_edges)`` — both sorted deterministically
        (nodes by ``node_id``, edges by ``edge_id``).
    """
    proj_node_ids: frozenset[str] = frozenset(
        n.node_id for n in nodes if n.kind in _SEMANTIC_NODE_KINDS
    )
    proj_nodes: list[GraphNode] = sorted(
        [n for n in nodes if n.node_id in proj_node_ids],
        key=lambda n: n.node_id,
    )
    proj_edges: list[GraphEdge] = sorted(
        [
            e
            for e in edges
            if e.kind in _SEMANTIC_EDGE_KINDS
            and e.src_id in proj_node_ids
            and e.dst_id in proj_node_ids
        ],
        key=lambda e: e.edge_id,
    )
    return proj_nodes, proj_edges


def detect_projected_communities(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> list[Community]:
    """Run deterministic CNM clustering on the semantic projection.

    Internally calls :func:`build_semantic_projection` to obtain the filtered
    subgraph, then runs :func:`detect_communities` on it.

    The result is identical to calling ``detect_communities`` directly on the
    full graph except that heading nodes are absent from every community and
    ``has_heading`` edges are never considered.  Nodes not in the semantic
    projection (headings, code nodes) are silently omitted.

    Calling this function twice with identical inputs produces byte-identical
    results (determinism is inherited from :func:`detect_communities`).

    Args:
        nodes: Full node list (heading nodes are filtered out internally).
        edges: Full edge list (non-semantic edges are filtered out internally).

    Returns:
        Sorted list of :class:`Community` objects over the projected nodes.
    """
    proj_nodes, proj_edges = build_semantic_projection(nodes, edges)
    return detect_communities(proj_nodes, proj_edges, edge_kinds=_SEMANTIC_EDGE_KINDS)


def projected_modularity(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> float:
    """Return Newman modularity *Q* for the semantic-projection partition.

    Computes the modularity of the community partition returned by
    :func:`detect_projected_communities` on the semantic projection of the
    given graph.

    Returns ``0.0`` when the projected graph has no edges.  The theoretical
    range is ``[-0.5, 1.0]``; values above ~0.3 indicate significant community
    structure.

    Args:
        nodes: Full node list.
        edges: Full edge list.

    Returns:
        Modularity score *Q* in ``[-0.5, 1.0]``.
    """
    proj_nodes, proj_edges = build_semantic_projection(nodes, edges)
    communities = detect_communities(
        proj_nodes, proj_edges, edge_kinds=_SEMANTIC_EDGE_KINDS
    )
    return modularity_score(
        proj_nodes, proj_edges, communities, edge_kinds=_SEMANTIC_EDGE_KINDS
    )


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
