"""Token-reduction benchmark for mneme-graph — content-free variant.

Measures how many tokens a BFS subgraph query costs compared with
naively sending the entire identifier corpus to the LLM.

Privacy invariant: NO file content is included anywhere.  Corpus and
subgraph measurements use only node.name, node.kind, node.source_path
and edge.kind — the same content-free fields stored in graph.json.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .schema import GraphEdge, GraphNode
from .store import GraphStore

_CHARS_PER_TOKEN: int = 4  # 4-char-per-token standard approximation

# Default edge kinds used for the per-question retrieval BFS.
# Only semantic vault edges are traversed; structural ``has_heading`` edges
# are excluded so a query on a note does not inflate the subgraph by pulling
# in the entire heading tree of that note.
_SEMANTIC_EDGE_KINDS: frozenset[str] = frozenset({"links_to", "tagged", "embeds"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_words(text: str) -> list[str]:
    """Split *text* on whitespace and common punctuation; return non-empty parts.

    ``source_path`` → [``source``, ``path``]; ``src/config.py`` →
    [``src``, ``config``, ``py``].  Used for corpus token counting so that
    identifier pieces are treated as individual tokens.
    """
    return [t for t in re.split(r"[\s/._\-:]+", text) if t]


def _estimate_tokens(text: str) -> int:
    """Character-count estimate: 1 token ≈ 4 characters (lower bound: 1)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _node_line(node: GraphNode) -> str:
    """Content-free one-line serialisation of a node (no file content)."""
    return f"NODE {node.name} kind={node.kind} path={node.source_path}"


def _edge_line(edge: GraphEdge, node_by_id: dict[str, GraphNode]) -> str:
    """Content-free one-line serialisation of an edge."""
    src = node_by_id.get(edge.src_id)
    dst = node_by_id.get(edge.dst_id)
    src_name = src.name if src is not None else edge.src_id
    dst_name = dst.name if dst is not None else edge.dst_id
    return f"EDGE {src_name} --{edge.kind}--> {dst_name}"


def _node_priority(kind: str) -> int:
    """BFS start-node priority — lower value means higher preference.

    note/tag nodes are preferred; heading nodes are deprioritised so that a
    label match on a heading does not anchor the BFS inside a single note's
    structural heading tree.  All other kinds (function, class, module, …)
    sit between the two extremes.
    """
    if kind in ("note", "tag"):
        return 0
    if kind == "heading":
        return 2
    return 1


# ---------------------------------------------------------------------------
# Corpus token count
# ---------------------------------------------------------------------------


def _corpus_tokens(nodes: list[GraphNode], edges: list[GraphEdge]) -> int:
    """Token count for the full content-free identifier corpus.

    Serialises every node and edge into content-free text and estimates
    tokens at 4 chars per token.  This is the ``naïve full-context``
    baseline against which subgraph queries are measured.
    """
    node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}
    lines: list[str] = [_node_line(n) for n in nodes]
    lines.extend(_edge_line(e, node_by_id) for e in edges)
    if not lines:
        return 1
    return _estimate_tokens("\n".join(lines))


# ---------------------------------------------------------------------------
# Default question generation
# ---------------------------------------------------------------------------


def _default_questions(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    n: int = 5,
) -> list[str]:
    """Derive up to *n* questions deterministically from top in-degree nodes.

    Ranking: descending in-degree, then ascending name for tie-breaking.
    Pads with generic fallbacks (``main``, ``config``, …) when there are
    fewer than *n* nodes with at least one incoming edge.
    """
    in_degree: Counter[str] = Counter()
    for edge in edges:
        in_degree[edge.dst_id] += 1

    node_by_id: dict[str, GraphNode] = {nd.node_id: nd for nd in nodes}

    ranked = sorted(
        [
            (cnt, node_by_id[nid].name)
            for nid, cnt in in_degree.items()
            if nid in node_by_id
        ],
        key=lambda x: (-x[0], x[1]),
    )
    top_names: list[str] = [name for _, name in ranked[:n]]

    fallbacks = ["main", "config", "init", "core", "base"]
    for fb in fallbacks:
        if len(top_names) >= n:
            break
        if fb not in top_names:
            top_names.append(fb)

    return [f"what is {name}" for name in top_names[:n]]


# ---------------------------------------------------------------------------
# Subgraph BFS query
# ---------------------------------------------------------------------------


def _build_undirected_adj(
    edges: list[GraphEdge],
    allowed_kinds: frozenset[str] | None = None,
) -> dict[str, list[str]]:
    """Build an undirected adjacency list from directed edges.

    Args:
        edges:         All graph edges.
        allowed_kinds: When provided, only edges whose ``kind`` is in this set
                       are included.  ``None`` includes all edges (legacy path).
    """
    adj: dict[str, list[str]] = {}
    for edge in edges:
        if allowed_kinds is not None and edge.kind not in allowed_kinds:
            continue
        adj.setdefault(edge.src_id, []).append(edge.dst_id)
        adj.setdefault(edge.dst_id, []).append(edge.src_id)
    return adj


def _query_subgraph_tokens(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    question: str,
    depth: int = 3,
    retrieval_edge_kinds: frozenset[str] | None = None,
) -> tuple[int, str]:
    """BFS from best-label-matching nodes; return ``(subgraph_tokens, matched_node_name)``.

    Args:
        nodes:                Graph nodes.
        edges:                Graph edges.
        question:             Free-text query; terms are used for label matching.
        depth:                BFS depth (default 3).
        retrieval_edge_kinds: Edge kinds traversed during BFS.  ``None`` (the
                              default) uses ``_SEMANTIC_EDGE_KINDS``
                              (``links_to``, ``tagged``, ``embeds``), which
                              deliberately excludes ``has_heading`` so that a
                              query on a note does not pull in its entire
                              heading tree.  Pass an explicit set to override.

    Start-node selection prefers note/tag nodes over heading nodes when scores
    tie, avoiding heading-tree anchoring as the BFS origin.

    Returns ``(0, '')`` when no node matches any question term.
    """
    effective_kinds: frozenset[str] = (
        retrieval_edge_kinds if retrieval_edge_kinds is not None else _SEMANTIC_EDGE_KINDS
    )

    terms = {t.lower() for t in _split_words(question) if len(t) > 1}
    if not terms:
        return 0, ""

    node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}

    scored: list[tuple[int, str]] = []
    for node in nodes:
        score = sum(1 for t in terms if t in node.name.lower())
        if score > 0:
            scored.append((score, node.node_id))

    # Deterministic sort: descending score, preferred kind first (note/tag
    # before generic, heading last), then ascending node_id as final tiebreak.
    scored.sort(
        key=lambda x: (-x[0], _node_priority(node_by_id[x[1]].kind), x[1])
    )

    if not scored:
        return 0, ""

    start_ids = [nid for _, nid in scored[:3]]
    matched_node = node_by_id[scored[0][1]].name

    adj = _build_undirected_adj(edges, allowed_kinds=effective_kinds)
    visited: set[str] = set(start_ids)
    frontier: set[str] = set(start_ids)
    discovered_pairs: list[tuple[str, str]] = []

    for _ in range(depth):
        nxt: set[str] = set()
        for nid in frontier:
            for neighbor in adj.get(nid, []):
                if neighbor not in visited:
                    nxt.add(neighbor)
                    discovered_pairs.append((nid, neighbor))
        visited.update(nxt)
        frontier = nxt

    # Serialise subgraph content-free; sorted node iteration for determinism.
    lines: list[str] = []
    for nid in sorted(visited):
        if nid in node_by_id:
            lines.append(_node_line(node_by_id[nid]))

    # Emit each directed edge at most once; both endpoints must be in subgraph.
    edge_lookup: dict[tuple[str, str], GraphEdge] = {(e.src_id, e.dst_id): e for e in edges}
    seen_edges: set[tuple[str, str]] = set()
    for u, v in discovered_pairs:
        if u in visited and v in visited:
            key = (u, v) if (u, v) in edge_lookup else (v, u)
            if key in edge_lookup and key not in seen_edges:
                seen_edges.add(key)
                lines.append(_edge_line(edge_lookup[key], node_by_id))

    return _estimate_tokens("\n".join(lines)), matched_node


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_benchmark(
    graph: GraphStore,
    questions: list[str] | None = None,
    depth: int = 3,
    retrieval_edge_kinds: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Measure token reduction: identifier corpus vs BFS subgraph query.

    Args:
        graph:                Loaded ``GraphStore`` (use :func:`load_graph`).
        questions:            Questions to benchmark.  ``None`` → ~5 are derived
                              deterministically from the top in-degree node names.
        depth:                BFS depth for each subgraph query (default 3).
        retrieval_edge_kinds: Edge kinds used by the per-question BFS.  ``None``
                              (the default) restricts traversal to semantic vault
                              edges — ``links_to``, ``tagged``, ``embeds`` —
                              excluding structural ``has_heading`` edges so that a
                              query on a note does not inflate the subgraph with
                              the full heading tree.  Pass an explicit
                              ``frozenset[str]`` to override, e.g. to include all
                              edge kinds for comparison benchmarks.

    Returns a dict with the following keys:

    ``corpus_tokens`` (int)
        Token estimate for the full content-free identifier corpus.

    ``avg_query_tokens`` (float)
        Mean subgraph token count across questions that matched at least
        one node.  ``0.0`` when nothing matched.

    ``reduction_ratio`` (float)
        ``1 - avg_query_tokens / corpus_tokens``, clamped to [0, 1].
        A value of 0.9 means a query costs ~10 % of the full corpus.

    ``per_question`` (list[dict])
        One entry per question with keys ``question``, ``subgraph_tokens``,
        and ``matched_node``.
    """
    nodes = graph.nodes
    edges = graph.edges

    corpus_tok = _corpus_tokens(nodes, edges)
    qs: list[str] = questions if questions is not None else _default_questions(nodes, edges)

    per_question: list[dict[str, Any]] = []
    for q in qs:
        sq_tok, matched = _query_subgraph_tokens(
            nodes, edges, q, depth=depth, retrieval_edge_kinds=retrieval_edge_kinds
        )
        per_question.append(
            {
                "question": q,
                "subgraph_tokens": sq_tok,
                "matched_node": matched,
            }
        )

    answered = [p for p in per_question if p["subgraph_tokens"] > 0]
    avg_qt: float = (
        sum(float(p["subgraph_tokens"]) for p in answered) / len(answered)
        if answered
        else 0.0
    )

    raw_ratio = 1.0 - avg_qt / corpus_tok if corpus_tok > 0 else 0.0
    reduction: float = max(0.0, min(1.0, raw_ratio))

    return {
        "corpus_tokens": corpus_tok,
        "avg_query_tokens": avg_qt,
        "reduction_ratio": reduction,
        "per_question": per_question,
    }


def load_graph(vault_path: Path | str) -> GraphStore:
    """Load a ``GraphStore`` from *vault_path* (the vault root containing ``.mneme/``).

    Delegates to :meth:`GraphStore.load`; returns an empty store (never
    raises) when ``graph.json`` is absent or unreadable.
    """
    return GraphStore.load(Path(vault_path))
