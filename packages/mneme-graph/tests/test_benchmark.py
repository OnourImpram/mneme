"""Tests for mneme_graph.benchmark — content-free token-reduction benchmark."""
from __future__ import annotations

from pathlib import Path

import pytest

from mneme_graph.benchmark import (
    _corpus_tokens,
    _default_questions,
    load_graph,
    run_benchmark,
)
from mneme_graph.schema import GraphEdge, GraphNode
from mneme_graph.store import GraphStore

# ---------------------------------------------------------------------------
# Test-graph helpers
# ---------------------------------------------------------------------------

_HASH = "a" * 64  # valid-length placeholder for content_hash


def _make_node(
    name: str,
    kind: str = "function",
    path: str = "src/foo.py",
) -> GraphNode:
    return GraphNode.make(
        kind=kind,  # type: ignore[arg-type]
        name=name,
        source_path=path,
        content_hash=_HASH,
        line_start=0,
        line_end=1,
    )


def _make_edge(src: GraphNode, dst: GraphNode, kind: str = "calls") -> GraphEdge:
    return GraphEdge.make(src_id=src.node_id, dst_id=dst.node_id, kind=kind)  # type: ignore[arg-type]


def _make_store(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphStore:
    """In-memory GraphStore — never touches disk."""
    store = GraphStore(Path("/nonexistent_bench_test_root"))
    store.add_nodes(nodes)
    store.add_edges(edges)
    return store


@pytest.fixture
def small_graph() -> GraphStore:
    """Five nodes, three edges — enough to exercise BFS narrowing."""
    n_load = _make_node("load_config", "function", "src/config.py")
    n_cfg = _make_node("Config", "class", "src/config.py")
    n_main = _make_node("main", "function", "src/main.py")
    n_parse = _make_node("parse_args", "function", "src/main.py")
    n_os = _make_node("os", "module", "<external>")

    e1 = _make_edge(n_main, n_load, "calls")       # main -> load_config
    e2 = _make_edge(n_load, n_cfg, "defines")      # load_config -> Config
    e3 = _make_edge(n_parse, n_os, "imports")      # parse_args -> os

    return _make_store([n_load, n_cfg, n_main, n_parse, n_os], [e1, e2, e3])


# ---------------------------------------------------------------------------
# _corpus_tokens
# ---------------------------------------------------------------------------


def test_corpus_tokens_positive(small_graph: GraphStore) -> None:
    result = _corpus_tokens(small_graph.nodes, small_graph.edges)
    assert isinstance(result, int)
    assert result > 0


def test_corpus_tokens_empty_graph() -> None:
    result = _corpus_tokens([], [])
    assert result >= 1  # guarded by max(1, …)


def test_corpus_tokens_grows_with_graph() -> None:
    """A graph with more nodes must have strictly more corpus tokens."""
    n1 = _make_node("alpha")
    n2 = _make_node("beta")
    n3 = _make_node("gamma")
    small = _corpus_tokens([n1], [])
    large = _corpus_tokens([n1, n2, n3], [])
    assert large > small


# ---------------------------------------------------------------------------
# run_benchmark — required keys and types
# ---------------------------------------------------------------------------


def test_run_benchmark_returns_required_keys(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph)
    assert "corpus_tokens" in result
    assert "avg_query_tokens" in result
    assert "reduction_ratio" in result
    assert "per_question" in result


def test_corpus_tokens_positive_via_run(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph)
    assert result["corpus_tokens"] > 0


def test_avg_query_tokens_is_float(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph)
    assert isinstance(result["avg_query_tokens"], float)


def test_reduction_ratio_in_unit_interval(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph)
    rr = result["reduction_ratio"]
    assert isinstance(rr, float)
    assert 0.0 <= rr <= 1.0


# ---------------------------------------------------------------------------
# per_question length contract
# ---------------------------------------------------------------------------


def test_per_question_length_matches_explicit_questions(small_graph: GraphStore) -> None:
    qs = ["what is main", "how does config work", "what are external deps"]
    result = run_benchmark(small_graph, questions=qs)
    assert len(result["per_question"]) == len(qs)


def test_per_question_length_single_question(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph, questions=["what is main"])
    assert len(result["per_question"]) == 1


def test_per_question_length_default_at_most_five(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph)
    assert 1 <= len(result["per_question"]) <= 5


def test_per_question_entry_has_required_keys(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph, questions=["what is main"])
    entry = result["per_question"][0]
    assert "question" in entry
    assert "subgraph_tokens" in entry
    assert "matched_node" in entry


def test_per_question_question_field_matches_input(small_graph: GraphStore) -> None:
    qs = ["what is load_config", "what is Config"]
    result = run_benchmark(small_graph, questions=qs)
    assert [e["question"] for e in result["per_question"]] == qs


def test_per_question_subgraph_tokens_nonnegative(small_graph: GraphStore) -> None:
    result = run_benchmark(small_graph, questions=["what is main", "what is config"])
    for entry in result["per_question"]:
        assert entry["subgraph_tokens"] >= 0


# ---------------------------------------------------------------------------
# Determinism — two identical calls must produce identical output
# ---------------------------------------------------------------------------


def test_determinism_default_questions(small_graph: GraphStore) -> None:
    r1 = run_benchmark(small_graph)
    r2 = run_benchmark(small_graph)
    assert r1 == r2


def test_determinism_explicit_questions(small_graph: GraphStore) -> None:
    qs = ["what is load_config", "what is Config", "what is os"]
    r1 = run_benchmark(small_graph, questions=qs)
    r2 = run_benchmark(small_graph, questions=qs)
    assert r1 == r2


def test_determinism_varying_depth(small_graph: GraphStore) -> None:
    qs = ["what is main"]
    r1 = run_benchmark(small_graph, questions=qs, depth=2)
    r2 = run_benchmark(small_graph, questions=qs, depth=2)
    assert r1 == r2


def test_determinism_with_retrieval_edge_kinds(small_graph: GraphStore) -> None:
    """Two identical runs with explicit retrieval_edge_kinds produce identical output."""
    qs = ["what is load_config"]
    kinds: frozenset[str] = frozenset({"links_to", "tagged", "embeds"})
    r1 = run_benchmark(small_graph, questions=qs, retrieval_edge_kinds=kinds)
    r2 = run_benchmark(small_graph, questions=qs, retrieval_edge_kinds=kinds)
    assert r1 == r2


# ---------------------------------------------------------------------------
# Focused-query subgraph < corpus
# ---------------------------------------------------------------------------


def test_focused_query_tokens_less_than_corpus(small_graph: GraphStore) -> None:
    """A query targeting one node must need fewer tokens than the full corpus."""
    result = run_benchmark(small_graph, questions=["what is load_config"])
    corpus_tok = result["corpus_tokens"]
    entry = result["per_question"][0]
    # If a match was found, its subgraph must be a strict subset of the corpus.
    if entry["subgraph_tokens"] > 0:
        assert entry["subgraph_tokens"] < corpus_tok


def test_reduction_ratio_positive_for_targeted_query(small_graph: GraphStore) -> None:
    """A focused query on a real node must achieve some positive reduction."""
    result = run_benchmark(small_graph, questions=["what is load_config"])
    entry = result["per_question"][0]
    if entry["subgraph_tokens"] > 0:
        assert result["reduction_ratio"] > 0.0


# ---------------------------------------------------------------------------
# Semantic-only BFS vs all-edges BFS — the core token-reduction fix
# ---------------------------------------------------------------------------


def _make_heading_heavy_store() -> GraphStore:
    """One note with 10 heading children via has_heading + one links_to target.

    This is the minimal graph that exposes the BFS inflation bug:
    traversing has_heading pulls the entire heading tree into every query
    subgraph, shrinking the reduction ratio.
    """
    note = _make_node("my_note", kind="note", path="notes/my_note.md")
    target = _make_node("other_note", kind="note", path="notes/other_note.md")
    headings = [
        _make_node(f"section_{i}", kind="heading", path="notes/my_note.md")
        for i in range(10)
    ]

    # Structural edges — inflate all-edges BFS.
    heading_edges: list[GraphEdge] = [
        _make_edge(note, h, "has_heading") for h in headings
    ]
    # One semantic edge — the only thing a focused query should traverse.
    semantic_edge = _make_edge(note, target, "links_to")

    nodes: list[GraphNode] = [note, target] + headings
    edges: list[GraphEdge] = heading_edges + [semantic_edge]
    return _make_store(nodes, edges)


def test_semantic_only_higher_reduction_than_all_edges() -> None:
    """Semantic-only BFS yields strictly higher reduction_ratio than all-edges BFS.

    On a graph where a note has many heading children (has_heading) plus one
    semantic link (links_to), the all-edges BFS inflates the query subgraph
    by pulling in every heading node.  Restricting traversal to semantic edges
    keeps the subgraph compact and raises the reduction_ratio.

    This is the RED test that drives the retrieval_edge_kinds fix.
    """
    store = _make_heading_heavy_store()

    _ALL_KINDS: frozenset[str] = frozenset(
        {"links_to", "tagged", "embeds", "has_heading", "calls", "imports", "defines", "inherits"}
    )

    result_all = run_benchmark(
        store,
        questions=["what is my_note"],
        retrieval_edge_kinds=_ALL_KINDS,
    )
    # Default: semantic-only (links_to, tagged, embeds — no has_heading).
    result_semantic = run_benchmark(store, questions=["what is my_note"])

    assert result_semantic["reduction_ratio"] > result_all["reduction_ratio"], (
        f"Semantic-only reduction {result_semantic['reduction_ratio']:.4f} must exceed "
        f"all-edges reduction {result_all['reduction_ratio']:.4f}"
    )


def test_semantic_only_avg_tokens_lower_than_all_edges() -> None:
    """Complementary check: avg_query_tokens is strictly lower for semantic-only."""
    store = _make_heading_heavy_store()

    _ALL_KINDS: frozenset[str] = frozenset(
        {"links_to", "tagged", "embeds", "has_heading", "calls", "imports", "defines", "inherits"}
    )

    result_all = run_benchmark(
        store,
        questions=["what is my_note"],
        retrieval_edge_kinds=_ALL_KINDS,
    )
    result_semantic = run_benchmark(store, questions=["what is my_note"])

    assert result_semantic["avg_query_tokens"] < result_all["avg_query_tokens"], (
        f"Semantic-only avg_query_tokens {result_semantic['avg_query_tokens']} must be "
        f"strictly less than all-edges {result_all['avg_query_tokens']}"
    )


def test_retrieval_edge_kinds_none_equals_default(small_graph: GraphStore) -> None:
    """Explicitly passing retrieval_edge_kinds=None gives same result as omitting it."""
    qs = ["what is load_config", "what is Config"]
    r_default = run_benchmark(small_graph, questions=qs)
    r_none = run_benchmark(small_graph, questions=qs, retrieval_edge_kinds=None)
    assert r_default == r_none


# ---------------------------------------------------------------------------
# _default_questions
# ---------------------------------------------------------------------------


def test_default_questions_deterministic(small_graph: GraphStore) -> None:
    q1 = _default_questions(small_graph.nodes, small_graph.edges)
    q2 = _default_questions(small_graph.nodes, small_graph.edges)
    assert q1 == q2


def test_default_questions_requested_count(small_graph: GraphStore) -> None:
    qs = _default_questions(small_graph.nodes, small_graph.edges, n=3)
    assert len(qs) == 3


def test_default_questions_format(small_graph: GraphStore) -> None:
    """Each generated question must begin with 'what is '."""
    qs = _default_questions(small_graph.nodes, small_graph.edges)
    for q in qs:
        assert q.startswith("what is ")


def test_default_questions_empty_graph() -> None:
    """Empty graph must not crash; returns fallback questions."""
    qs = _default_questions([], [], n=3)
    assert len(qs) == 3
    assert all(q.startswith("what is ") for q in qs)


# ---------------------------------------------------------------------------
# load_graph helper
# ---------------------------------------------------------------------------


def test_load_graph_missing_vault_returns_empty_store(tmp_path: Path) -> None:
    """load_graph on a vault with no graph.json must return empty store silently."""
    store = load_graph(tmp_path)
    assert store.nodes == []
    assert store.edges == []


def test_load_graph_accepts_str_path(tmp_path: Path) -> None:
    store = load_graph(str(tmp_path))
    assert store.nodes == []


def test_load_graph_populates_nodes(tmp_path: Path) -> None:
    """load_graph must return a populated store when graph.json exists."""
    node = _make_node("fn_alpha")
    prep = GraphStore(tmp_path)
    prep.add_nodes([node])
    prep.save()

    loaded = load_graph(tmp_path)
    assert len(loaded.nodes) == 1
    assert loaded.nodes[0].name == "fn_alpha"
