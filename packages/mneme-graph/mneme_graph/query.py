"""Retrieval query over a loaded mneme knowledge graph.

Public API
----------
    from mneme_graph.query import load, query
    result = query(load(vault_path), "my note")
    # result keys: matched_node, subgraph_node_ids, subgraph_edges, tokens, answer

Content-free: all identifiers used here come from node.name, node.kind,
node.source_path, and edge.kind — no file content is ever read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import (
    _SEMANTIC_EDGE_KINDS,
    _build_undirected_adj,
    _estimate_tokens,
    _node_line,
    _node_priority,
)
from .schema import GraphNode
from .store import GraphStore

__all__ = ["load", "query"]


def load(vault_path: Path | str) -> GraphStore:
    """Load a ``GraphStore`` from *vault_path* (the vault root with ``.mneme/``).

    Returns an empty store (never raises) when ``graph.json`` is absent or
    unreadable.  Delegates directly to :meth:`GraphStore.load`.
    """
    return GraphStore.load(Path(vault_path))


def query(
    graph: GraphStore,
    term: str,
    budget_tokens: int = 2000,
) -> dict[str, Any]:
    """Answer a retrieval query over *graph* for *term*.

    Matching
    --------
    Searches all nodes for *term* as a case-insensitive substring of
    ``node.name`` or ``node.source_path``.  Note and tag nodes are preferred
    over heading and code nodes when multiple nodes match (priority: note/tag=0,
    other=1, heading=2); the lowest ``node_id`` breaks further ties to ensure
    full determinism.

    BFS
    ---
    Performs an undirected BFS over semantic edges only (``links_to``,
    ``tagged``, ``embeds`` — never ``has_heading`` or code edges).  Neighbours
    at each level are expanded in sorted order.  A node is included only when
    the running content-free token estimate would not exceed *budget_tokens*
    after its addition.

    Answer
    ------
    Built from the matched node's direct *outgoing* semantic edges, grouped and
    sorted by edge kind, e.g.::

        "my_note -> embeds: Z; links_to: A, B; tagged: #x, #y"

    Args:
        graph:         Loaded ``GraphStore``; use :func:`load` to obtain one.
        term:          Search term (case-insensitive substring match against
                       node labels and vault-relative source paths).
        budget_tokens: Maximum content-free token budget for the returned
                       subgraph.  BFS stops adding a node when including it
                       would push the running estimate above this value.
                       Default: 2000.

    Returns:
        Dict with the following keys:

        - ``matched_node`` (``dict | None``): ``{id, label, kind}`` for the
          best-matched node, or ``None`` when no node matches *term*.
        - ``subgraph_node_ids`` (``list[str]``): node ids in BFS-discovery
          order (sorted within each BFS level for determinism).
        - ``subgraph_edges`` (``list[tuple[str, str, str]]``): semantic edges
          whose both endpoints are in the subgraph, as ``(src_id, kind, dst_id)``,
          sorted deterministically by ``(src_id, kind, dst_id)``.
        - ``tokens`` (``int``): content-free token estimate for the full
          subgraph (nodes + edges).  Zero when the subgraph is empty.
        - ``answer`` (``str``): human-readable summary of the matched node's
          direct outgoing semantic neighbours grouped by edge kind.  Empty
          string when no match was found.
    """
    nodes = graph.nodes
    edges = graph.edges
    term_lower = term.lower()

    # ------------------------------------------------------------------
    # 1. Find the best-matching node.
    # ------------------------------------------------------------------
    matches: list[GraphNode] = [
        n
        for n in nodes
        if term_lower in n.name.lower() or term_lower in n.source_path.lower()
    ]

    if not matches:
        return {
            "matched_node": None,
            "subgraph_node_ids": [],
            "subgraph_edges": [],
            "tokens": 0,
            "answer": "",
        }

    # note/tag=0, generic=1, heading=2; tiebreak = lowest node_id.
    matched: GraphNode = min(
        matches, key=lambda n: (_node_priority(n.kind), n.node_id)
    )

    # ------------------------------------------------------------------
    # 2. BFS over semantic edges, bounded by the token budget.
    # ------------------------------------------------------------------
    node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}
    adj = _build_undirected_adj(edges, allowed_kinds=_SEMANTIC_EDGE_KINDS)

    included_ids: list[str] = []        # BFS-discovery order
    visited_set: set[str] = {matched.node_id}
    running_tokens: int = 0
    frontier: list[str] = [matched.node_id]

    while frontier:
        next_frontier: list[str] = []
        for nid in frontier:
            node = node_by_id.get(nid)
            if node is None:
                continue
            node_tok = _estimate_tokens(_node_line(node))
            if running_tokens + node_tok > budget_tokens:
                continue  # skip this node; other nodes in the level may still fit
            running_tokens += node_tok
            included_ids.append(nid)
            for nb in sorted(adj.get(nid, [])):
                if nb not in visited_set:
                    visited_set.add(nb)
                    next_frontier.append(nb)
        frontier = sorted(next_frontier)

    # ------------------------------------------------------------------
    # 3. Collect semantic edges whose both endpoints are in the subgraph.
    # ------------------------------------------------------------------
    included_set: set[str] = set(included_ids)
    seen_edge_ids: set[str] = set()
    subgraph_edges_raw: list[tuple[str, str, str]] = []

    for edge in sorted(edges, key=lambda e: e.edge_id):
        if (
            edge.kind in _SEMANTIC_EDGE_KINDS
            and edge.src_id in included_set
            and edge.dst_id in included_set
            and edge.edge_id not in seen_edge_ids
        ):
            seen_edge_ids.add(edge.edge_id)
            # Use str() to widen EdgeKind literal to str for the tuple type.
            subgraph_edges_raw.append((edge.src_id, str(edge.kind), edge.dst_id))

    subgraph_edges_raw.sort()

    # ------------------------------------------------------------------
    # 4. Compute content-free token estimate (nodes + edges).
    # ------------------------------------------------------------------
    node_lines: list[str] = [
        _node_line(node_by_id[nid])
        for nid in sorted(included_ids)
        if nid in node_by_id
    ]
    edge_lines: list[str] = []
    for src_id, kind, dst_id in subgraph_edges_raw:
        src_name = node_by_id[src_id].name if src_id in node_by_id else src_id
        dst_name = node_by_id[dst_id].name if dst_id in node_by_id else dst_id
        edge_lines.append(f"EDGE {src_name} --{kind}--> {dst_name}")

    all_lines = node_lines + edge_lines
    total_tokens: int = _estimate_tokens("\n".join(all_lines)) if all_lines else 0

    # ------------------------------------------------------------------
    # 5. Build answer from matched node's direct outgoing semantic edges.
    # ------------------------------------------------------------------
    neighbor_by_kind: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind not in _SEMANTIC_EDGE_KINDS or edge.src_id != matched.node_id:
            continue
        dst_node = node_by_id.get(edge.dst_id)
        if dst_node is not None:
            neighbor_by_kind.setdefault(str(edge.kind), []).append(dst_node.name)

    parts: list[str] = []
    for kind in sorted(neighbor_by_kind):
        names_sorted = sorted(neighbor_by_kind[kind])
        parts.append(f"{kind}: {', '.join(names_sorted)}")

    answer: str = matched.name
    if parts:
        answer += " -> " + "; ".join(parts)

    return {
        "matched_node": {"id": matched.node_id, "label": matched.name, "kind": matched.kind},
        "subgraph_node_ids": included_ids,
        "subgraph_edges": subgraph_edges_raw,
        "tokens": total_tokens,
        "answer": answer,
    }
