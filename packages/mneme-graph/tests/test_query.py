"""Tests for mneme_graph.query — self-contained, in-memory graphs only.

All tests build small GraphStore objects in memory; no real vault or disk
graph.json is required.  The only test that touches the filesystem is
test_load_missing_vault_returns_empty_store, which uses pytest's tmp_path
fixture on an empty directory.
"""

from __future__ import annotations

from pathlib import Path

from mneme_graph.query import load, query
from mneme_graph.schema import GraphEdge, GraphNode
from mneme_graph.store import GraphStore

_HASH = "a" * 64  # valid-length placeholder for content_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    name: str,
    kind: str = "note",
    path: str | None = None,
) -> GraphNode:
    if path is None:
        path = f"notes/{name}.md"
    return GraphNode.make(
        kind=kind,  # type: ignore[arg-type]
        name=name,
        source_path=path,
        content_hash=_HASH,
        line_start=0,
        line_end=1,
    )


def _make_edge(
    src: GraphNode,
    dst: GraphNode,
    kind: str = "links_to",
) -> GraphEdge:
    return GraphEdge.make(  # type: ignore[arg-type]
        src_id=src.node_id,
        dst_id=dst.node_id,
        kind=kind,
    )


def _make_store(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphStore:
    """In-memory GraphStore — never touches disk."""
    store = GraphStore(Path("/nonexistent_query_test_root"))
    store.add_nodes(nodes)
    store.add_edges(edges)
    return store


# ---------------------------------------------------------------------------
# Test graph used by several tests:
#
#   note_a --links_to--> note_b
#   note_a --tagged--> tag_x
#   note_b --embeds--> note_c
#   note_a --has_heading--> h1    ← structural, must NOT be traversed
#   tag_x  --links_to--> note_c
# ---------------------------------------------------------------------------


def _make_vault_graph() -> tuple[
    GraphStore,
    GraphNode,  # note_a
    GraphNode,  # note_b
    GraphNode,  # note_c
    GraphNode,  # tag_x
    GraphNode,  # h1
]:
    note_a = _make_node("note_a")
    note_b = _make_node("note_b")
    note_c = _make_node("note_c")
    tag_x = _make_node("tag_x", kind="tag")
    h1 = _make_node("h1", kind="heading", path="notes/note_a.md")

    e1 = _make_edge(note_a, note_b, "links_to")
    e2 = _make_edge(note_a, tag_x, "tagged")
    e3 = _make_edge(note_b, note_c, "embeds")
    e4 = _make_edge(note_a, h1, "has_heading")  # structural — NOT semantic
    e5 = _make_edge(tag_x, note_c, "links_to")

    store = _make_store([note_a, note_b, note_c, tag_x, h1], [e1, e2, e3, e4, e5])
    return store, note_a, note_b, note_c, tag_x, h1


# ---------------------------------------------------------------------------
# 1. Query finds the correct note by name
# ---------------------------------------------------------------------------


def test_query_finds_matched_note_by_name() -> None:
    store, note_a, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "note_a")

    assert result["matched_node"] is not None
    assert result["matched_node"]["label"] == "note_a"
    assert result["matched_node"]["kind"] == "note"
    assert result["matched_node"]["id"] == note_a.node_id


def test_matched_node_has_required_keys() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "note_a")

    assert result["matched_node"] is not None
    assert set(result["matched_node"].keys()) == {"id", "label", "kind"}


# ---------------------------------------------------------------------------
# 2. Traversal is semantic-only: has_heading neighbour is NOT pulled in
# ---------------------------------------------------------------------------


def test_semantic_only_excludes_has_heading_neighbour() -> None:
    note_a = _make_node("note_a")
    note_b = _make_node("note_b")
    h1 = _make_node("h1", kind="heading", path="notes/note_a.md")

    e_semantic = _make_edge(note_a, note_b, "links_to")
    e_structural = _make_edge(note_a, h1, "has_heading")

    store = _make_store([note_a, note_b, h1], [e_semantic, e_structural])
    result = query(store, "note_a", budget_tokens=10000)

    # Semantic neighbour is present.
    assert note_b.node_id in result["subgraph_node_ids"]
    # Heading neighbour reached only via has_heading must not appear.
    assert h1.node_id not in result["subgraph_node_ids"]


def test_has_heading_edge_absent_from_subgraph_edges() -> None:
    note_a = _make_node("note_a")
    h1 = _make_node("h1", kind="heading", path="notes/note_a.md")
    store = _make_store([note_a, h1], [_make_edge(note_a, h1, "has_heading")])
    result = query(store, "note_a", budget_tokens=10000)

    for _src, kind, _dst in result["subgraph_edges"]:
        assert kind != "has_heading"


# ---------------------------------------------------------------------------
# 3. Budget bound is respected
# ---------------------------------------------------------------------------


def test_budget_respected_limits_subgraph() -> None:
    """With budget_tokens=1, even the matched node cannot fit — subgraph is empty."""
    notes = [_make_node(f"n{i}") for i in range(5)]
    edges = [_make_edge(notes[i], notes[i + 1], "links_to") for i in range(4)]
    store = _make_store(notes, edges)

    result = query(store, "n0", budget_tokens=1)

    # Subgraph is strictly smaller than the full node list.
    assert len(result["subgraph_node_ids"]) < len(notes)
    # The matched_node is found (matching is independent of BFS).
    assert result["matched_node"] is not None
    assert result["matched_node"]["label"] == "n0"


def test_large_budget_includes_semantic_neighbours() -> None:
    store, note_a, note_b, _nc, tag_x, _h1 = _make_vault_graph()
    result = query(store, "note_a", budget_tokens=10000)

    ids = result["subgraph_node_ids"]
    assert note_a.node_id in ids
    assert note_b.node_id in ids
    assert tag_x.node_id in ids


# ---------------------------------------------------------------------------
# 4. Determinism: two identical calls produce identical dicts
# ---------------------------------------------------------------------------


def test_determinism_identical_calls() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()

    r1 = query(store, "note_a")
    r2 = query(store, "note_a")

    assert r1 == r2


def test_determinism_with_small_budget() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()

    r1 = query(store, "note_a", budget_tokens=50)
    r2 = query(store, "note_a", budget_tokens=50)

    assert r1 == r2


# ---------------------------------------------------------------------------
# 5. Answer string contains expected neighbour labels grouped by edge kind
# ---------------------------------------------------------------------------


def test_answer_groups_neighbours_by_kind() -> None:
    """Direct outgoing semantic edges from note_a appear sorted by kind."""
    note_a = _make_node("note_a")
    note_b = _make_node("note_b")
    tag_x = _make_node("tag_x", kind="tag")
    store = _make_store(
        [note_a, note_b, tag_x],
        [_make_edge(note_a, note_b, "links_to"), _make_edge(note_a, tag_x, "tagged")],
    )

    result = query(store, "note_a", budget_tokens=10000)

    # Expected: kinds sorted alphabetically → links_to before tagged.
    assert result["answer"] == "note_a -> links_to: note_b; tagged: tag_x"


def test_answer_excludes_heading_reached_via_has_heading() -> None:
    store, _na, _nb, _nc, _tx, h1 = _make_vault_graph()
    result = query(store, "note_a", budget_tokens=10000)

    assert h1.name not in result["answer"]


def test_answer_contains_matched_label_prefix() -> None:
    note = _make_node("wiki_home")
    store = _make_store([note], [])
    result = query(store, "wiki_home")

    assert result["answer"].startswith("wiki_home")


def test_answer_multiple_targets_same_kind_sorted() -> None:
    """Multiple targets under the same edge kind appear alphabetically sorted."""
    note_a = _make_node("note_a")
    note_z = _make_node("zebra")
    note_m = _make_node("mango")
    store = _make_store(
        [note_a, note_z, note_m],
        [_make_edge(note_a, note_z, "links_to"), _make_edge(note_a, note_m, "links_to")],
    )

    result = query(store, "note_a", budget_tokens=10000)

    # "mango" < "zebra" alphabetically.
    assert result["answer"] == "note_a -> links_to: mango, zebra"


# ---------------------------------------------------------------------------
# 6. No-match returns matched_node=None gracefully
# ---------------------------------------------------------------------------


def test_no_match_returns_none() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "zzz_this_term_does_not_exist_xyz")

    assert result["matched_node"] is None
    assert result["subgraph_node_ids"] == []
    assert result["subgraph_edges"] == []
    assert result["tokens"] == 0
    assert result["answer"] == ""


def test_no_match_empty_graph() -> None:
    store = _make_store([], [])
    result = query(store, "anything")

    assert result["matched_node"] is None


# ---------------------------------------------------------------------------
# 7. Prefer note/tag over heading when both match the same term
# ---------------------------------------------------------------------------


def test_prefers_note_over_heading_same_name() -> None:
    note = _make_node("topic", kind="note", path="notes/topic.md")
    heading = _make_node("topic", kind="heading", path="notes/other.md")
    store = _make_store([note, heading], [])

    result = query(store, "topic")

    assert result["matched_node"] is not None
    assert result["matched_node"]["kind"] == "note"


def test_prefers_tag_over_heading_same_name() -> None:
    tag = _make_node("concept", kind="tag", path="notes/concept.md")
    heading = _make_node("concept", kind="heading", path="notes/article.md")
    store = _make_store([tag, heading], [])

    result = query(store, "concept")

    assert result["matched_node"] is not None
    assert result["matched_node"]["kind"] == "tag"


def test_deterministic_tiebreak_by_lowest_node_id() -> None:
    """Two same-kind same-name nodes: the one with the lower node_id wins."""
    note_a = _make_node("topic", kind="note", path="notes/topic_a.md")
    note_b = _make_node("topic", kind="note", path="notes/topic_b.md")
    store = _make_store([note_a, note_b], [])

    result = query(store, "topic")

    assert result["matched_node"] is not None
    expected_id = min(note_a.node_id, note_b.node_id)
    assert result["matched_node"]["id"] == expected_id


# ---------------------------------------------------------------------------
# 8. subgraph_edges structure
# ---------------------------------------------------------------------------


def test_subgraph_edges_are_three_tuples() -> None:
    note_a = _make_node("note_a")
    note_b = _make_node("note_b")
    store = _make_store([note_a, note_b], [_make_edge(note_a, note_b, "links_to")])

    result = query(store, "note_a", budget_tokens=10000)

    assert len(result["subgraph_edges"]) > 0
    for edge in result["subgraph_edges"]:
        src_id, kind, dst_id = edge  # unpacking asserts exactly 3 elements
        assert isinstance(src_id, str)
        assert isinstance(kind, str)
        assert isinstance(dst_id, str)


def test_subgraph_edges_only_semantic_kinds() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "note_a", budget_tokens=10000)

    semantic = {"links_to", "tagged", "embeds"}
    for _src, kind, _dst in result["subgraph_edges"]:
        assert kind in semantic


def test_subgraph_edges_endpoints_in_subgraph_node_ids() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "note_a", budget_tokens=10000)

    ids_set = set(result["subgraph_node_ids"])
    for src_id, _kind, dst_id in result["subgraph_edges"]:
        assert src_id in ids_set
        assert dst_id in ids_set


# ---------------------------------------------------------------------------
# 9. tokens field
# ---------------------------------------------------------------------------


def test_tokens_is_nonneg_int() -> None:
    store, _na, _nb, _nc, _tx, _h1 = _make_vault_graph()
    result = query(store, "note_a")

    assert isinstance(result["tokens"], int)
    assert result["tokens"] >= 0


def test_tokens_zero_when_no_match() -> None:
    store = _make_store([], [])
    result = query(store, "missing")

    assert result["tokens"] == 0


def test_tokens_positive_when_subgraph_nonempty() -> None:
    note = _make_node("alpha")
    store = _make_store([note], [])
    result = query(store, "alpha", budget_tokens=10000)

    # If at least one node was included, tokens must be positive.
    if result["subgraph_node_ids"]:
        assert result["tokens"] > 0


# ---------------------------------------------------------------------------
# 10. load() helper
# ---------------------------------------------------------------------------


def test_load_missing_vault_returns_empty_store(tmp_path: Path) -> None:
    """load() on a vault directory without graph.json returns an empty store."""
    store = load(tmp_path)
    assert store.nodes == []
    assert store.edges == []


def test_load_accepts_str_path(tmp_path: Path) -> None:
    store = load(str(tmp_path))
    assert store.nodes == []
