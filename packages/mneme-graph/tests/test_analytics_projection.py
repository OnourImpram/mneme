"""Tests for the semantic-projection clustering functions (B2 / R3 fix).

Exercises ``build_semantic_projection``, ``detect_projected_communities``, and
``projected_modularity`` from ``mneme_graph.analytics``.

Graph fixture
-------------
Notes:    note_a, note_b, note_c
Tags:     tag_x
Headings: head_1, head_2          <- must be absent from the projection

Semantic edges kept by the projection:
  note_a -> note_b  links_to
  note_b -> note_c  links_to
  note_a -> tag_x   tagged

Structural edges dropped by the projection:
  note_a -> head_1  has_heading
  note_b -> head_2  has_heading

After projection:
  Nodes = {note_a, note_b, note_c, tag_x}
  Edges = {links_to a-b, links_to b-c, tagged a-x}

CNM on this 4-node, 3-edge graph produces two 2-node communities:
  {note_a, tag_x}  and  {note_b, note_c}
so ``any(len(c.node_ids) > 1 ...)`` is True and heading singletons
(head_1, head_2) are absent from every community.
"""

from __future__ import annotations

from mneme_graph.analytics import (
    build_semantic_projection,
    detect_projected_communities,
    projected_modularity,
)
from mneme_graph.schema import GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Node / edge helpers
# ---------------------------------------------------------------------------


def _note(name: str, line_start: int = 0) -> GraphNode:
    return GraphNode.make(
        kind="note",
        name=name,
        source_path="vault/notes.md",
        content_hash="abcd1234",
        line_start=line_start,
        line_end=line_start + 1,
    )


def _tag(name: str, line_start: int = 0) -> GraphNode:
    return GraphNode.make(
        kind="tag",
        name=name,
        source_path="vault/tags.md",
        content_hash="ef567890",
        line_start=line_start,
        line_end=line_start + 1,
    )


def _heading(name: str, line_start: int = 0) -> GraphNode:
    return GraphNode.make(
        kind="heading",
        name=name,
        source_path="vault/heads.md",
        content_hash="aabb1122",
        line_start=line_start,
        line_end=line_start + 1,
    )


def _links_to(src: GraphNode, dst: GraphNode) -> GraphEdge:
    return GraphEdge.make(src_id=src.node_id, dst_id=dst.node_id, kind="links_to")


def _tagged(src: GraphNode, dst: GraphNode) -> GraphEdge:
    return GraphEdge.make(src_id=src.node_id, dst_id=dst.node_id, kind="tagged")


def _has_heading(src: GraphNode, dst: GraphNode) -> GraphEdge:
    return GraphEdge.make(
        src_id=src.node_id, dst_id=dst.node_id, kind="has_heading"
    )


# ---------------------------------------------------------------------------
# Shared fixture builder
# ---------------------------------------------------------------------------


def _make_mixed_graph() -> tuple[list[GraphNode], list[GraphEdge]]:
    """Small vault graph: notes + tags + headings + all edge kinds mixed."""
    note_a = _note("a", 0)
    note_b = _note("b", 1)
    note_c = _note("c", 2)
    tag_x = _tag("x", 0)
    head_1 = _heading("h1", 0)
    head_2 = _heading("h2", 1)

    nodes: list[GraphNode] = [note_a, note_b, note_c, tag_x, head_1, head_2]
    edges: list[GraphEdge] = [
        _links_to(note_a, note_b),
        _links_to(note_b, note_c),
        _tagged(note_a, tag_x),
        _has_heading(note_a, head_1),
        _has_heading(note_b, head_2),
    ]
    return nodes, edges


# ---------------------------------------------------------------------------
# Tests: build_semantic_projection
# ---------------------------------------------------------------------------


class TestBuildSemanticProjection:
    def test_heading_nodes_absent(self) -> None:
        """Heading node_ids must not appear in the projected node list."""
        nodes, edges = _make_mixed_graph()
        head_ids = {n.node_id for n in nodes if n.kind == "heading"}

        proj_nodes, _ = build_semantic_projection(nodes, edges)
        proj_ids = {n.node_id for n in proj_nodes}

        assert not proj_ids & head_ids, (
            "Heading node ids must not appear in the projection"
        )

    def test_note_and_tag_nodes_present(self) -> None:
        """Every note and tag node must appear in the projected node list."""
        nodes, edges = _make_mixed_graph()
        semantic_ids = {n.node_id for n in nodes if n.kind in {"note", "tag"}}

        proj_nodes, _ = build_semantic_projection(nodes, edges)
        proj_ids = {n.node_id for n in proj_nodes}

        assert semantic_ids == proj_ids

    def test_has_heading_edges_dropped(self) -> None:
        """has_heading edges must not appear in the projected edge list."""
        nodes, edges = _make_mixed_graph()
        _, proj_edges = build_semantic_projection(nodes, edges)

        assert all(e.kind != "has_heading" for e in proj_edges), (
            "has_heading edges must be absent from the projection"
        )

    def test_semantic_edges_retained(self) -> None:
        """links_to and tagged edges between note/tag nodes are retained."""
        nodes, edges = _make_mixed_graph()
        _, proj_edges = build_semantic_projection(nodes, edges)

        semantic_kinds = {"links_to", "tagged", "embeds"}
        assert all(e.kind in semantic_kinds for e in proj_edges)
        # Fixture has exactly 3 semantic edges: a->b, b->c, a->x.
        assert len(proj_edges) == 3

    def test_output_sorted_deterministically(self) -> None:
        """Projected nodes sorted by node_id; projected edges by edge_id."""
        nodes, edges = _make_mixed_graph()
        proj_nodes, proj_edges = build_semantic_projection(nodes, edges)

        assert [n.node_id for n in proj_nodes] == sorted(
            n.node_id for n in proj_nodes
        )
        assert [e.edge_id for e in proj_edges] == sorted(
            e.edge_id for e in proj_edges
        )

    def test_empty_graph(self) -> None:
        """Empty inputs return empty projections without error."""
        proj_nodes, proj_edges = build_semantic_projection([], [])
        assert proj_nodes == []
        assert proj_edges == []

    def test_only_headings_projects_to_empty(self) -> None:
        """A graph with only heading nodes and has_heading edges projects empty."""
        heads: list[GraphNode] = [_heading("h1", 0), _heading("h2", 1)]
        h_edges: list[GraphEdge] = [_has_heading(heads[0], heads[1])]
        proj_nodes, proj_edges = build_semantic_projection(heads, h_edges)
        assert proj_nodes == []
        assert proj_edges == []

    def test_cross_kind_edge_with_unknown_endpoint_dropped(self) -> None:
        """A semantic-kind edge whose dst is a heading node is dropped."""
        note_a = _note("a", 0)
        head_1 = _heading("h1", 0)
        # Hypothetical links_to from a note to a heading — not a semantic pair.
        weird_edge = GraphEdge.make(
            src_id=note_a.node_id, dst_id=head_1.node_id, kind="links_to"
        )
        _, proj_edges = build_semantic_projection(
            [note_a, head_1], [weird_edge]
        )
        assert proj_edges == [], (
            "An edge pointing to a non-projection node must be dropped"
        )


# ---------------------------------------------------------------------------
# Tests: detect_projected_communities
# ---------------------------------------------------------------------------


class TestDetectProjectedCommunities:
    def test_yields_multi_node_community(self) -> None:
        """The connected note/tag component forms at least one multi-node community."""
        nodes, edges = _make_mixed_graph()
        communities = detect_projected_communities(nodes, edges)

        assert any(len(c.node_ids) > 1 for c in communities), (
            "Expected at least one multi-node community in the projection"
        )

    def test_heading_ids_absent_from_all_communities(self) -> None:
        """Heading node_ids must not appear in any returned community."""
        nodes, edges = _make_mixed_graph()
        head_ids = {n.node_id for n in nodes if n.kind == "heading"}
        communities = detect_projected_communities(nodes, edges)

        all_community_ids: set[str] = set()
        for comm in communities:
            all_community_ids.update(comm.node_ids)

        assert not all_community_ids & head_ids

    def test_all_semantic_nodes_covered_exactly_once(self) -> None:
        """Every note/tag node appears in exactly one community (partition)."""
        nodes, edges = _make_mixed_graph()
        semantic_ids = {n.node_id for n in nodes if n.kind in {"note", "tag"}}
        communities = detect_projected_communities(nodes, edges)

        covered: set[str] = set()
        for comm in communities:
            overlap = covered & set(comm.node_ids)
            assert not overlap, f"Node(s) {overlap} appear in multiple communities"
            covered.update(comm.node_ids)

        assert covered == semantic_ids

    def test_determinism_same_result_twice(self) -> None:
        """Two calls with identical inputs produce byte-identical results."""
        nodes, edges = _make_mixed_graph()
        result_1 = detect_projected_communities(nodes, edges)
        result_2 = detect_projected_communities(nodes, edges)

        assert len(result_1) == len(result_2)
        for c1, c2 in zip(result_1, result_2, strict=True):
            assert c1.community_id == c2.community_id
            assert c1.node_ids == c2.node_ids
            assert c1.confidence == c2.confidence

    def test_empty_graph(self) -> None:
        """Empty inputs return an empty community list."""
        assert detect_projected_communities([], []) == []

    def test_heading_only_graph_returns_empty(self) -> None:
        """A graph with only heading nodes returns an empty community list."""
        heads: list[GraphNode] = [_heading("h1", 0), _heading("h2", 1)]
        h_edges: list[GraphEdge] = [_has_heading(heads[0], heads[1])]
        assert detect_projected_communities(heads, h_edges) == []

    def test_isolated_notes_become_singleton_communities(self) -> None:
        """Note nodes with no semantic edges each become their own singleton."""
        note_a = _note("a", 0)
        note_b = _note("b", 1)
        # No edges between them — two singletons expected.
        communities = detect_projected_communities([note_a, note_b], [])

        assert len(communities) == 2
        all_ids = {nid for c in communities for nid in c.node_ids}
        assert all_ids == {note_a.node_id, note_b.node_id}

    def test_community_confidence_is_inferred(self) -> None:
        """All returned communities carry confidence='INFERRED'."""
        nodes, edges = _make_mixed_graph()
        communities = detect_projected_communities(nodes, edges)

        for comm in communities:
            assert comm.confidence == "INFERRED"

    def test_community_id_is_lex_min_node_id(self) -> None:
        """community_id equals the lexicographically smallest node_id."""
        nodes, edges = _make_mixed_graph()
        communities = detect_projected_communities(nodes, edges)

        for comm in communities:
            assert comm.community_id == min(comm.node_ids)


# ---------------------------------------------------------------------------
# Tests: projected_modularity
# ---------------------------------------------------------------------------


class TestProjectedModularity:
    def test_q_in_valid_range(self) -> None:
        """Q must be within the theoretical range [-0.5, 1]."""
        nodes, edges = _make_mixed_graph()
        q = projected_modularity(nodes, edges)
        assert -0.5 <= q <= 1.0, f"Q={q!r} is outside the valid range [-0.5, 1]"

    def test_q_is_float(self) -> None:
        """Return type must be float."""
        nodes, edges = _make_mixed_graph()
        q = projected_modularity(nodes, edges)
        assert isinstance(q, float)

    def test_empty_graph_returns_zero(self) -> None:
        """No-edge projected graph returns 0.0."""
        q = projected_modularity([], [])
        assert q == 0.0

    def test_heading_only_graph_returns_zero(self) -> None:
        """Only headings -> empty projection -> 0.0."""
        heads: list[GraphNode] = [_heading("h1", 0), _heading("h2", 1)]
        h_edges: list[GraphEdge] = [_has_heading(heads[0], heads[1])]
        q = projected_modularity(heads, h_edges)
        assert q == 0.0

    def test_determinism(self) -> None:
        """Two calls with identical inputs produce the same Q."""
        nodes, edges = _make_mixed_graph()
        q1 = projected_modularity(nodes, edges)
        q2 = projected_modularity(nodes, edges)
        assert q1 == q2

    def test_isolated_notes_q_is_zero(self) -> None:
        """No-edge projection -> Q = 0.0 regardless of node count."""
        note_a = _note("a", 0)
        note_b = _note("b", 1)
        q = projected_modularity([note_a, note_b], [])
        assert q == 0.0

    def test_positive_q_for_well_clustered_graph(self) -> None:
        """Two tight clusters linked by one weak bridge should yield Q > 0."""
        # Cluster 1: note_a -- note_b -- tag_x (triangle of semantic edges)
        note_a = _note("a", 0)
        note_b = _note("b", 1)
        tag_x = _tag("x", 0)
        # Cluster 2: note_c -- note_d -- tag_y (triangle of semantic edges)
        note_c = _note("c", 2)
        note_d = _note("d", 3)
        tag_y = _tag("y", 1)

        nodes: list[GraphNode] = [note_a, note_b, tag_x, note_c, note_d, tag_y]
        edges: list[GraphEdge] = [
            # Cluster 1 (dense)
            _links_to(note_a, note_b),
            _tagged(note_a, tag_x),
            _tagged(note_b, tag_x),
            # Cluster 2 (dense)
            _links_to(note_c, note_d),
            _tagged(note_c, tag_y),
            _tagged(note_d, tag_y),
        ]

        q = projected_modularity(nodes, edges)
        assert q > 0.0, f"Expected Q > 0 for a well-clustered graph; got {q}"
