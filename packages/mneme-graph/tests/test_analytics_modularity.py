"""Tests for the modularity-based community detection upgrade.

Covers:
  - Two-cluster graph produces exactly 2 communities (R1/R3).
  - Determinism: identical inputs → byte-identical output across multiple calls (R5).
  - modularity_score returns a value in [-0.5, 1.0] (R2).
  - modularity_score for the good partition is strictly higher than all-in-one (R4).
  - links_to and tagged edges drive clustering (vault note graph support).
  - edge_kinds override correctly excludes vault-note edge kinds.
"""

from __future__ import annotations

from mneme_graph.analytics import (
    Community,
    detect_communities,
    modularity_score,
)
from mneme_graph.schema import GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(name: str, path: str = "src/a.py", line_start: int = 0) -> GraphNode:
    return GraphNode.make(
        kind="function",
        name=name,
        source_path=path,
        content_hash="abc",
        line_start=line_start,
        line_end=line_start + 1,
    )


def _edge(src: GraphNode, dst: GraphNode, kind: str = "calls") -> GraphEdge:
    return GraphEdge.make(  # type: ignore[arg-type]
        src_id=src.node_id,
        dst_id=dst.node_id,
        kind=kind,
    )


def _two_cluster_nodes_and_edges() -> tuple[list[GraphNode], list[GraphEdge]]:
    """Two K3 cliques with no cross edges: clear two-community structure."""
    a = _node("a", line_start=0)
    b = _node("b", line_start=1)
    c = _node("c", line_start=2)
    d = _node("d", line_start=3)
    e = _node("e", line_start=4)
    f = _node("f", line_start=5)
    edges: list[GraphEdge] = [
        _edge(a, b, "calls"),
        _edge(b, c, "calls"),
        _edge(a, c, "calls"),
        _edge(d, e, "calls"),
        _edge(e, f, "calls"),
        _edge(d, f, "calls"),
    ]
    return [a, b, c, d, e, f], edges


# ---------------------------------------------------------------------------
# Two-cluster detection
# ---------------------------------------------------------------------------


class TestTwoClusterDetection:
    def test_exactly_two_communities(self) -> None:
        """Two K3 cliques with no cross edges must produce exactly 2 communities."""
        nodes, edges = _two_cluster_nodes_and_edges()
        comms = detect_communities(nodes, edges)
        assert len(comms) == 2

    def test_correct_membership(self) -> None:
        """Each community must contain exactly the nodes from its clique."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        d = _node("d", line_start=3)
        e = _node("e", line_start=4)
        f = _node("f", line_start=5)
        edges: list[GraphEdge] = [
            _edge(a, b, "calls"),
            _edge(b, c, "calls"),
            _edge(a, c, "calls"),
            _edge(d, e, "calls"),
            _edge(e, f, "calls"),
            _edge(d, f, "calls"),
        ]
        comms = detect_communities([a, b, c, d, e, f], edges)
        member_sets = [set(comm.node_ids) for comm in comms]
        assert {a.node_id, b.node_id, c.node_id} in member_sets
        assert {d.node_id, e.node_id, f.node_id} in member_sets

    def test_community_id_is_lex_min_node_id(self) -> None:
        """community_id must equal the lex-min node_id inside that community."""
        nodes, edges = _two_cluster_nodes_and_edges()
        for comm in detect_communities(nodes, edges):
            assert comm.community_id == min(comm.node_ids)
            assert comm.community_id == comm.node_ids[0]

    def test_node_ids_sorted(self) -> None:
        """node_ids tuple must be lexicographically sorted."""
        nodes, edges = _two_cluster_nodes_and_edges()
        for comm in detect_communities(nodes, edges):
            assert list(comm.node_ids) == sorted(comm.node_ids)

    def test_output_sorted_by_community_id(self) -> None:
        """The returned list must be sorted by community_id."""
        nodes, edges = _two_cluster_nodes_and_edges()
        comms = detect_communities(nodes, edges)
        ids = [c.community_id for c in comms]
        assert ids == sorted(ids)

    def test_confidence_is_inferred(self) -> None:
        nodes, edges = _two_cluster_nodes_and_edges()
        for comm in detect_communities(nodes, edges):
            assert comm.confidence == "INFERRED"


# ---------------------------------------------------------------------------
# Determinism (R5)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_identical_calls_produce_same_result(self) -> None:
        """Calling detect_communities twice with the same inputs → identical list."""
        nodes, edges = _two_cluster_nodes_and_edges()
        r1 = detect_communities(nodes, edges)
        r2 = detect_communities(nodes, edges)
        assert r1 == r2

    def test_determinism_larger_mixed_graph(self) -> None:
        """Determinism holds for a 10-node graph with code and vault edges."""
        nodes = [_node(str(i), line_start=i) for i in range(10)]
        edges: list[GraphEdge] = [
            _edge(nodes[0], nodes[1], "calls"),
            _edge(nodes[1], nodes[2], "calls"),
            _edge(nodes[0], nodes[2], "calls"),
            _edge(nodes[3], nodes[4], "imports"),
            _edge(nodes[4], nodes[5], "imports"),
            _edge(nodes[3], nodes[5], "imports"),
            _edge(nodes[6], nodes[7], "inherits"),
        ]
        r1 = detect_communities(nodes, edges)
        r2 = detect_communities(nodes, edges)
        assert r1 == r2

    def test_determinism_with_vault_edges(self) -> None:
        """Determinism holds when links_to and tagged edges are present."""
        notes = [
            GraphNode.make(
                kind="note",
                name=f"note_{i}",
                source_path=f"vault/{i}.md",
                content_hash="h",
                line_start=0,
                line_end=1,
            )
            for i in range(6)
        ]
        edges: list[GraphEdge] = [
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=notes[0].node_id, dst_id=notes[1].node_id, kind="links_to"
            ),
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=notes[1].node_id, dst_id=notes[2].node_id, kind="links_to"
            ),
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=notes[3].node_id, dst_id=notes[4].node_id, kind="tagged"
            ),
        ]
        r1 = detect_communities(notes, edges)
        r2 = detect_communities(notes, edges)
        assert r1 == r2


# ---------------------------------------------------------------------------
# modularity_score
# ---------------------------------------------------------------------------


class TestModularityScore:
    def test_score_in_valid_range(self) -> None:
        """modularity_score must lie in [-0.5, 1.0] for any partition."""
        nodes, edges = _two_cluster_nodes_and_edges()
        comms = detect_communities(nodes, edges)
        q = modularity_score(nodes, edges, comms)
        assert -0.5 <= q <= 1.0

    def test_good_partition_beats_all_in_one(self) -> None:
        """The CNM partition scores strictly higher than a single all-in-one community."""
        nodes, edges = _two_cluster_nodes_and_edges()
        good_comms = detect_communities(nodes, edges)
        q_good = modularity_score(nodes, edges, good_comms)

        all_ids = tuple(sorted(n.node_id for n in nodes))
        one_comm = [Community(community_id=all_ids[0], node_ids=all_ids)]
        q_one = modularity_score(nodes, edges, one_comm)

        assert q_good > q_one

    def test_no_edges_returns_zero(self) -> None:
        """When there are no edges, modularity_score returns 0.0."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        comms = detect_communities([a, b], [])
        assert modularity_score([a, b], [], comms) == 0.0

    def test_score_type_is_float(self) -> None:
        nodes, edges = _two_cluster_nodes_and_edges()
        comms = detect_communities(nodes, edges)
        assert isinstance(modularity_score(nodes, edges, comms), float)

    def test_score_with_custom_edge_kinds(self) -> None:
        """modularity_score respects a custom edge_kinds parameter."""
        nodes, edges = _two_cluster_nodes_and_edges()
        comms = detect_communities(nodes, edges)
        q_code = modularity_score(
            nodes, edges, comms,
            edge_kinds=frozenset({"calls", "inherits", "imports"}),
        )
        assert -0.5 <= q_code <= 1.0


# ---------------------------------------------------------------------------
# links_to and tagged edges drive clustering
# ---------------------------------------------------------------------------


class TestVaultNoteEdges:
    def test_links_to_edges_cluster_notes(self) -> None:
        """links_to edges alone must cluster vault notes into communities."""
        note_a = GraphNode.make(
            kind="note", name="note_a", source_path="vault/a.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_b = GraphNode.make(
            kind="note", name="note_b", source_path="vault/b.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_c = GraphNode.make(
            kind="note", name="note_c", source_path="vault/c.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_d = GraphNode.make(
            kind="note", name="note_d", source_path="vault/d.md",
            content_hash="h", line_start=0, line_end=1,
        )
        edges: list[GraphEdge] = [
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note_a.node_id, dst_id=note_b.node_id, kind="links_to"
            ),
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note_c.node_id, dst_id=note_d.node_id, kind="links_to"
            ),
        ]
        comms = detect_communities([note_a, note_b, note_c, note_d], edges)
        assert len(comms) == 2
        member_sets = [set(comm.node_ids) for comm in comms]
        assert {note_a.node_id, note_b.node_id} in member_sets
        assert {note_c.node_id, note_d.node_id} in member_sets

    def test_tagged_edges_cluster_notes_through_shared_tag(self) -> None:
        """notes sharing a tag node via tagged edges must land in the same community."""
        tag_node = GraphNode.make(
            kind="tag", name="psychology", source_path="vault/tags.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_a = GraphNode.make(
            kind="note", name="note_a", source_path="vault/a.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_b = GraphNode.make(
            kind="note", name="note_b", source_path="vault/b.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_c = GraphNode.make(
            kind="note", name="note_c", source_path="vault/c.md",
            content_hash="h", line_start=0, line_end=1,
        )
        # note_a and note_b both tagged 'psychology'; note_c is isolated.
        edges: list[GraphEdge] = [
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note_a.node_id, dst_id=tag_node.node_id, kind="tagged"
            ),
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note_b.node_id, dst_id=tag_node.node_id, kind="tagged"
            ),
        ]
        comms = detect_communities([tag_node, note_a, note_b, note_c], edges)
        member_sets = [set(comm.node_ids) for comm in comms]
        # tag_node, note_a, note_b form one connected component.
        connected = {tag_node.node_id, note_a.node_id, note_b.node_id}
        assert connected in member_sets
        # note_c is isolated → singleton.
        assert {note_c.node_id} in member_sets

    def test_links_to_ignored_when_edge_kinds_overridden(self) -> None:
        """Overriding edge_kinds to code-only must exclude links_to edges."""
        note_a = GraphNode.make(
            kind="note", name="note_a", source_path="vault/a.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note_b = GraphNode.make(
            kind="note", name="note_b", source_path="vault/b.md",
            content_hash="h", line_start=0, line_end=1,
        )
        edges: list[GraphEdge] = [
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note_a.node_id, dst_id=note_b.node_id, kind="links_to"
            ),
        ]
        code_only = frozenset({"calls", "inherits", "imports"})
        comms = detect_communities([note_a, note_b], edges, edge_kinds=code_only)
        # links_to is excluded → both nodes are singletons.
        assert len(comms) == 2

    def test_tagged_ignored_when_edge_kinds_overridden(self) -> None:
        """Overriding edge_kinds to code-only must exclude tagged edges."""
        tag = GraphNode.make(
            kind="tag", name="ai", source_path="vault/tags.md",
            content_hash="h", line_start=0, line_end=1,
        )
        note = GraphNode.make(
            kind="note", name="ai_note", source_path="vault/ai.md",
            content_hash="h", line_start=0, line_end=1,
        )
        edges: list[GraphEdge] = [
            GraphEdge.make(  # type: ignore[arg-type]
                src_id=note.node_id, dst_id=tag.node_id, kind="tagged"
            ),
        ]
        code_only = frozenset({"calls", "inherits", "imports"})
        comms = detect_communities([tag, note], edges, edge_kinds=code_only)
        assert len(comms) == 2  # tagged ignored → two singletons
