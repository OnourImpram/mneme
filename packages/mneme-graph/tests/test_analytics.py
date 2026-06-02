"""Tests for mneme_graph.analytics — pure graph analytics functions.

Coverage targets (>=80% of analytics.py):
  * detect_communities — connected components, cycle, defines-edge exclusion,
    missing node ignore, community_id as min, determinism.
  * pr_impact — chain traversal, max_depth, changed exclusion, absent changed id.
  * changed_nodes_for_paths — path-to-node_id mapping.
  * find_merge_candidates — duplicate detection, external exclusion, singles.
  * apply_merge — rewrite, drop, dedup, self-loop removal, input immutability,
    transitive canonical resolution.
"""

from __future__ import annotations

from mneme_graph.analytics import (
    ImpactResult,
    MergeCandidate,
    apply_merge,
    changed_nodes_for_paths,
    detect_communities,
    find_merge_candidates,
    pr_impact,
)
from mneme_graph.schema import GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(name: str, source_path: str = "src/a.py", line_start: int = 0) -> GraphNode:
    """Minimal GraphNode factory for tests."""
    return GraphNode.make(
        kind="function",
        name=name,
        source_path=source_path,
        content_hash="deadbeef",
        line_start=line_start,
        line_end=line_start + 1,
    )


def _edge(src: GraphNode, dst: GraphNode, kind: str = "calls") -> GraphEdge:
    return GraphEdge.make(src_id=src.node_id, dst_id=dst.node_id, kind=kind)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# detect_communities
# ---------------------------------------------------------------------------


class TestDetectCommunities:
    def test_two_disjoint_clusters_plus_isolated(self) -> None:
        """Two connected pairs + one isolated node => 3 communities."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        d = _node("d", line_start=3)
        e = _node("e", line_start=4)
        # cluster 1: a-b
        e_ab = _edge(a, b, "calls")
        # cluster 2: c-d
        e_cd = _edge(c, d, "imports")
        # e is isolated

        communities = detect_communities([a, b, c, d, e], [e_ab, e_cd])

        assert len(communities) == 3
        node_id_sets = [set(comm.node_ids) for comm in communities]
        assert {a.node_id, b.node_id} in node_id_sets
        assert {c.node_id, d.node_id} in node_id_sets
        assert {e.node_id} in node_id_sets

    def test_cycle_stays_one_community(self) -> None:
        """a->b->c->a forms a single component."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        edges = [
            _edge(a, b, "calls"),
            _edge(b, c, "calls"),
            _edge(c, a, "inherits"),
        ]
        communities = detect_communities([a, b, c], edges)
        assert len(communities) == 1
        assert set(communities[0].node_ids) == {a.node_id, b.node_id, c.node_id}

    def test_defines_edges_do_not_merge(self) -> None:
        """'defines' edges are excluded; the two nodes stay separate."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        e = GraphEdge.make(src_id=a.node_id, dst_id=b.node_id, kind="defines")
        communities = detect_communities([a, b], [e])
        assert len(communities) == 2

    def test_edge_to_missing_node_is_ignored(self) -> None:
        """An edge referencing an unknown node_id must not crash or merge."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        phantom_edge = GraphEdge.make(
            src_id=a.node_id, dst_id="phantom_id_not_in_nodes", kind="calls"
        )
        real_edge = _edge(a, b, "calls")
        communities = detect_communities([a, b], [phantom_edge, real_edge])
        # a and b are connected through real_edge; phantom is silently ignored
        assert len(communities) == 1
        assert set(communities[0].node_ids) == {a.node_id, b.node_id}

    def test_community_id_is_min_node_id(self) -> None:
        """community_id must equal the lexicographically smallest node_id."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        e = _edge(a, b, "calls")
        (comm,) = detect_communities([a, b], [e])
        assert comm.community_id == min(a.node_id, b.node_id)
        assert comm.community_id == comm.node_ids[0]

    def test_determinism(self) -> None:
        """Two calls with the same inputs produce identical results."""
        nodes = [_node(str(i), line_start=i) for i in range(5)]
        edges = [_edge(nodes[0], nodes[1], "calls"), _edge(nodes[2], nodes[3], "imports")]
        result_1 = detect_communities(nodes, edges)
        result_2 = detect_communities(nodes, edges)
        assert result_1 == result_2

    def test_empty_inputs(self) -> None:
        assert detect_communities([], []) == []

    def test_single_node_singleton(self) -> None:
        a = _node("a", line_start=0)
        (comm,) = detect_communities([a], [])
        assert comm.node_ids == (a.node_id,)
        assert comm.community_id == a.node_id
        assert comm.confidence == "INFERRED"

    def test_confidence_label(self) -> None:
        a = _node("a", line_start=0)
        (comm,) = detect_communities([a], [])
        assert comm.confidence == "INFERRED"


# ---------------------------------------------------------------------------
# pr_impact
# ---------------------------------------------------------------------------


class TestPrImpact:
    def _chain(self) -> tuple[GraphNode, GraphNode, GraphNode, list[GraphEdge]]:
        """a calls b, b calls c (a->b->c).  Change c -> b affected at 1, a at 2."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        edges = [_edge(a, b, "calls"), _edge(b, c, "calls")]
        return a, b, c, edges

    def test_chain_full_depth(self) -> None:
        a, b, c, edges = self._chain()
        result = pr_impact([a, b, c], edges, [c.node_id])
        assert result.changed == (c.node_id,)
        affected_map = dict(result.affected)
        assert affected_map[b.node_id] == 1
        assert affected_map[a.node_id] == 2
        assert c.node_id not in affected_map

    def test_max_depth_limits_traversal(self) -> None:
        a, b, c, edges = self._chain()
        result = pr_impact([a, b, c], edges, [c.node_id], max_depth=1)
        affected_map = dict(result.affected)
        assert b.node_id in affected_map
        assert affected_map[b.node_id] == 1
        assert a.node_id not in affected_map  # cut off at depth 1

    def test_changed_node_excluded_from_affected(self) -> None:
        a, b, c, edges = self._chain()
        result = pr_impact([a, b, c], edges, [c.node_id])
        affected_ids = {t[0] for t in result.affected}
        assert c.node_id not in affected_ids

    def test_absent_changed_id_yields_empty_affected(self) -> None:
        a = _node("a", line_start=0)
        result = pr_impact([a], [], ["nonexistent_id"])
        assert result.changed == ()
        assert result.affected == ()

    def test_multiple_changed_seeds(self) -> None:
        """Changing both endpoints of a chain gives distance 1 for the middle."""
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        # a -> b -> c
        edges = [_edge(a, b, "calls"), _edge(b, c, "calls")]
        result = pr_impact([a, b, c], edges, [a.node_id, c.node_id])
        affected_map = dict(result.affected)
        # b depends on c (distance 1 from c) and a depends on b (but a is changed)
        assert affected_map[b.node_id] == 1
        assert a.node_id not in affected_map
        assert c.node_id not in affected_map

    def test_affected_sorted_by_distance_then_node_id(self) -> None:
        root = _node("root", line_start=0)
        x = _node("x", line_start=1)
        y = _node("y", line_start=2)
        z = _node("z", line_start=3)
        # x->root, y->root, z->x
        edges = [
            _edge(x, root, "calls"),
            _edge(y, root, "calls"),
            _edge(z, x, "calls"),
        ]
        result = pr_impact([root, x, y, z], edges, [root.node_id])
        # x and y at distance 1; z at distance 2
        distances = [t[1] for t in result.affected]
        assert distances == sorted(distances)

    def test_no_edges_no_affected(self) -> None:
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        result = pr_impact([a, b], [], [a.node_id])
        assert result.affected == ()
        assert result.changed == (a.node_id,)

    def test_result_type(self) -> None:
        a = _node("a", line_start=0)
        result = pr_impact([a], [], [a.node_id])
        assert isinstance(result, ImpactResult)


# ---------------------------------------------------------------------------
# changed_nodes_for_paths
# ---------------------------------------------------------------------------


class TestChangedNodesForPaths:
    def test_maps_path_to_node_ids(self) -> None:
        a = _node("a", source_path="src/foo.py", line_start=0)
        b = _node("b", source_path="src/foo.py", line_start=10)
        c = _node("c", source_path="src/bar.py", line_start=0)
        result = changed_nodes_for_paths([a, b, c], ["src/foo.py"])
        assert sorted(result) == sorted([a.node_id, b.node_id])
        assert c.node_id not in result

    def test_empty_paths(self) -> None:
        a = _node("a", line_start=0)
        assert changed_nodes_for_paths([a], []) == []

    def test_no_match(self) -> None:
        a = _node("a", source_path="src/a.py", line_start=0)
        assert changed_nodes_for_paths([a], ["src/other.py"]) == []

    def test_deduped_and_sorted(self) -> None:
        """Same node_id from duplicate path entries appears once, sorted."""
        a = _node("a", source_path="src/a.py", line_start=0)
        b = _node("b", source_path="src/a.py", line_start=1)
        result = changed_nodes_for_paths([a, b], {"src/a.py"})
        assert result == sorted([a.node_id, b.node_id])
        assert len(result) == len(set(result))

    def test_accepts_set_of_paths(self) -> None:
        a = _node("a", source_path="src/a.py", line_start=0)
        result = changed_nodes_for_paths([a], {"src/a.py"})
        assert a.node_id in result


# ---------------------------------------------------------------------------
# find_merge_candidates
# ---------------------------------------------------------------------------


class TestFindMergeCandidates:
    def test_two_nodes_same_path_name_kind(self) -> None:
        """Same (path, name, kind) across file versions (different hashes) -> one candidate."""
        a = GraphNode.make(
            kind="function",
            name="my_fn",
            source_path="src/a.py",
            content_hash="v1",
            line_start=0,
            line_end=5,
        )
        b = GraphNode.make(
            kind="function",
            name="my_fn",
            source_path="src/a.py",
            content_hash="v2",
            line_start=10,
            line_end=15,
        )
        candidates = find_merge_candidates([a, b])
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.key == ("src/a.py", "my_fn", "function")
        assert set(cand.node_ids) == {a.node_id, b.node_id}
        assert len(cand.node_ids) == 2

    def test_external_nodes_excluded(self) -> None:
        """External nodes (source_path == '<external>') are never candidates."""
        ext = GraphNode.make(
            kind="module",
            name="os",
            source_path="<external>",
            content_hash="",
            line_start=0,
            line_end=0,
        )
        candidates = find_merge_candidates([ext, ext])
        assert candidates == []

    def test_single_occurrence_no_candidate(self) -> None:
        a = _node("a", line_start=0)
        assert find_merge_candidates([a]) == []

    def test_confidence_is_ambiguous(self) -> None:
        a = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="v1",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="v2",
            line_start=5,
            line_end=6,
        )
        (cand,) = find_merge_candidates([a, b])
        assert cand.confidence == "AMBIGUOUS"

    def test_different_name_no_candidate(self) -> None:
        a = _node("foo", line_start=0)
        b = _node("bar", line_start=1)
        assert find_merge_candidates([a, b]) == []

    def test_different_path_no_candidate(self) -> None:
        a = _node("fn", source_path="src/a.py", line_start=0)
        b = _node("fn", source_path="src/b.py", line_start=0)
        assert find_merge_candidates([a, b]) == []

    def test_sorted_by_key(self) -> None:
        """Candidates are sorted by (source_path, name, kind)."""
        a1 = GraphNode.make(
            kind="function",
            name="z_fn",
            source_path="src/a.py",
            content_hash="v1",
            line_start=0,
            line_end=1,
        )
        a2 = GraphNode.make(
            kind="function",
            name="z_fn",
            source_path="src/a.py",
            content_hash="v2",
            line_start=10,
            line_end=11,
        )
        b1 = GraphNode.make(
            kind="function",
            name="a_fn",
            source_path="src/a.py",
            content_hash="v1",
            line_start=20,
            line_end=21,
        )
        b2 = GraphNode.make(
            kind="function",
            name="a_fn",
            source_path="src/a.py",
            content_hash="v2",
            line_start=30,
            line_end=31,
        )
        candidates = find_merge_candidates([a1, a2, b1, b2])
        assert len(candidates) == 2
        assert candidates[0].key[1] == "a_fn"
        assert candidates[1].key[1] == "z_fn"

    def test_three_duplicates(self) -> None:
        # Three nodes for the same logical symbol across three file versions
        # (distinct content_hash + distinct line_start) -> one candidate, 3 ids.
        nodes = [
            GraphNode.make(
                kind="function",
                name="f",
                source_path="src/a.py",
                content_hash=f"v{i}",
                line_start=i,
                line_end=i + 1,
            )
            for i in range(3)
        ]
        (cand,) = find_merge_candidates(nodes)
        assert len(cand.node_ids) == 3

    def test_returns_merge_candidate_type(self) -> None:
        a = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="v1",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="v2",
            line_start=5,
            line_end=6,
        )
        (cand,) = find_merge_candidates([a, b])
        assert isinstance(cand, MergeCandidate)


# ---------------------------------------------------------------------------
# apply_merge
# ---------------------------------------------------------------------------


class TestApplyMerge:
    def _dup_and_canon(self) -> tuple[GraphNode, GraphNode]:
        dup = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        canon = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=10,
            line_end=11,
        )
        return dup, canon

    def test_drops_non_canonical_node(self) -> None:
        dup, canon = self._dup_and_canon()
        new_nodes, _ = apply_merge([dup, canon], [], {dup.node_id: canon.node_id})
        node_ids = [n.node_id for n in new_nodes]
        assert canon.node_id in node_ids
        assert dup.node_id not in node_ids

    def test_rewrites_edge_src(self) -> None:
        dup, canon = self._dup_and_canon()
        target = _node("target", line_start=20)
        edge = GraphEdge.make(src_id=dup.node_id, dst_id=target.node_id, kind="calls")
        _, new_edges = apply_merge([dup, canon, target], [edge], {dup.node_id: canon.node_id})
        assert len(new_edges) == 1
        assert new_edges[0].src_id == canon.node_id
        assert new_edges[0].dst_id == target.node_id

    def test_rewrites_edge_dst(self) -> None:
        dup, canon = self._dup_and_canon()
        caller = _node("caller", line_start=30)
        edge = GraphEdge.make(src_id=caller.node_id, dst_id=dup.node_id, kind="calls")
        _, new_edges = apply_merge([dup, canon, caller], [edge], {dup.node_id: canon.node_id})
        assert len(new_edges) == 1
        assert new_edges[0].dst_id == canon.node_id

    def test_deduplicates_edges_after_rewrite(self) -> None:
        """Two edges that become identical after rewriting -> one edge."""
        dup, canon = self._dup_and_canon()
        target = _node("target", line_start=20)
        # Both edges point from different srcs (dup and canon) to same target.
        # After rewriting dup->canon, both become canon->target; deduplicated.
        e1 = GraphEdge.make(src_id=dup.node_id, dst_id=target.node_id, kind="calls")
        e2 = GraphEdge.make(src_id=canon.node_id, dst_id=target.node_id, kind="calls")
        _, new_edges = apply_merge([dup, canon, target], [e1, e2], {dup.node_id: canon.node_id})
        assert len(new_edges) == 1
        assert new_edges[0].src_id == canon.node_id
        assert new_edges[0].dst_id == target.node_id

    def test_drops_self_loops_from_merge(self) -> None:
        """An edge dup->canon becomes canon->canon (self-loop) and must be dropped."""
        dup, canon = self._dup_and_canon()
        self_edge = GraphEdge.make(src_id=dup.node_id, dst_id=canon.node_id, kind="calls")
        _, new_edges = apply_merge([dup, canon], [self_edge], {dup.node_id: canon.node_id})
        assert new_edges == []

    def test_inputs_not_mutated(self) -> None:
        """Original node and edge lists must be unchanged after apply_merge."""
        dup, canon = self._dup_and_canon()
        target = _node("target", line_start=20)
        edge = GraphEdge.make(src_id=dup.node_id, dst_id=target.node_id, kind="calls")
        original_nodes = [dup, canon, target]
        original_edges = [edge]
        original_node_ids = [n.node_id for n in original_nodes]
        original_edge_ids = [e.edge_id for e in original_edges]

        apply_merge(original_nodes, original_edges, {dup.node_id: canon.node_id})

        assert [n.node_id for n in original_nodes] == original_node_ids
        assert [e.edge_id for e in original_edges] == original_edge_ids

    def test_transitive_canonical_resolution(self) -> None:
        """a->b, b->c in map: a should resolve to c."""
        a = GraphNode.make(
            kind="function",
            name="a",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="a",
            source_path="src/a.py",
            content_hash="h",
            line_start=5,
            line_end=6,
        )
        c = GraphNode.make(
            kind="function",
            name="a",
            source_path="src/a.py",
            content_hash="h",
            line_start=10,
            line_end=11,
        )
        target = _node("t", line_start=20)
        edge = GraphEdge.make(src_id=a.node_id, dst_id=target.node_id, kind="calls")

        canonical_map = {a.node_id: b.node_id, b.node_id: c.node_id}
        new_nodes, new_edges = apply_merge([a, b, c, target], [edge], canonical_map)
        node_ids = [n.node_id for n in new_nodes]
        assert a.node_id not in node_ids
        assert b.node_id not in node_ids
        assert c.node_id in node_ids
        # edge a->target should have been rewritten to c->target
        assert len(new_edges) == 1
        assert new_edges[0].src_id == c.node_id

    def test_empty_canonical_map_no_change(self) -> None:
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        edge = _edge(a, b, "calls")
        new_nodes, new_edges = apply_merge([a, b], [edge], {})
        assert {n.node_id for n in new_nodes} == {a.node_id, b.node_id}
        assert len(new_edges) == 1

    def test_output_sorted_deterministically(self) -> None:
        a = _node("a", line_start=0)
        b = _node("b", line_start=1)
        c = _node("c", line_start=2)
        e1 = _edge(a, c, "calls")
        e2 = _edge(b, c, "calls")
        new_nodes, new_edges = apply_merge([a, b, c], [e1, e2], {})
        assert [n.node_id for n in new_nodes] == sorted(n.node_id for n in new_nodes)
        assert [e.edge_id for e in new_edges] == sorted(e.edge_id for e in new_edges)

    def test_cycle_in_map_does_not_loop_forever(self) -> None:
        """A->B, B->A cycle in canonical_map: must terminate."""
        a = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=5,
            line_end=6,
        )
        # Cyclic map: a->b, b->a
        canonical_map = {a.node_id: b.node_id, b.node_id: a.node_id}
        # Should not raise or loop; result should be consistent
        new_nodes, _ = apply_merge([a, b], [], canonical_map)
        # After cycle detection, both a and b resolve to themselves (or one is canonical)
        # The invariant is: the function terminates and returns something.
        assert isinstance(new_nodes, list)

    # --- Fix 1: dangling-edge regression tests ---

    def test_fix1_dangling_edge_dropped_when_canonical_not_in_nodes(self) -> None:
        """FIX 1: canonical_map maps a node to an id absent from `nodes`.

        The canonical target does not exist in the node list, so after
        rewriting the edge both endpoints must still be in the surviving set.
        Any edge whose rewritten endpoint references a missing id must be
        dropped.
        """
        # 'ghost' is the canonical target but is NOT in the nodes list.
        ghost_id = "ghost_node_id_not_present"
        real = GraphNode.make(
            kind="function",
            name="real",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        other = GraphNode.make(
            kind="function",
            name="other",
            source_path="src/a.py",
            content_hash="h",
            line_start=10,
            line_end=11,
        )
        # Map real -> ghost (ghost not in nodes list)
        canonical_map = {real.node_id: ghost_id}
        edge = GraphEdge.make(src_id=real.node_id, dst_id=other.node_id, kind="calls")
        new_nodes, new_edges = apply_merge([real, other], [edge], canonical_map)

        surviving_ids = {n.node_id for n in new_nodes}
        # Every edge endpoint must be in the surviving set — no dangling refs.
        for e in new_edges:
            assert e.src_id in surviving_ids, f"dangling src {e.src_id}"
            assert e.dst_id in surviving_ids, f"dangling dst {e.dst_id}"

    # --- Fix 2: mutual-cycle regression tests ---

    def test_fix2_mutual_cycle_exactly_one_survives(self) -> None:
        """FIX 2: a->b, b->a cycle — exactly the lex-min node survives."""
        a = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=5,
            line_end=6,
        )
        canonical_map = {a.node_id: b.node_id, b.node_id: a.node_id}
        edge = GraphEdge.make(src_id=a.node_id, dst_id=b.node_id, kind="calls")
        new_nodes, new_edges = apply_merge([a, b], [edge], canonical_map)

        surviving_ids = {n.node_id for n in new_nodes}
        # Exactly one of {a, b} survives — the lexicographically smaller one.
        assert len(surviving_ids) == 1
        assert min(a.node_id, b.node_id) in surviving_ids
        # No dangling edges.
        for e in new_edges:
            assert e.src_id in surviving_ids, f"dangling src {e.src_id}"
            assert e.dst_id in surviving_ids, f"dangling dst {e.dst_id}"

    def test_fix2_three_cycle_exactly_one_survives(self) -> None:
        """FIX 2: a->b->c->a 3-cycle — exactly min(a,b,c) survives."""
        a = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=0,
            line_end=1,
        )
        b = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=5,
            line_end=6,
        )
        c = GraphNode.make(
            kind="function",
            name="f",
            source_path="src/a.py",
            content_hash="h",
            line_start=10,
            line_end=11,
        )
        canonical_map = {a.node_id: b.node_id, b.node_id: c.node_id, c.node_id: a.node_id}
        new_nodes, new_edges = apply_merge([a, b, c], [], canonical_map)

        surviving_ids = {n.node_id for n in new_nodes}
        assert len(surviving_ids) == 1
        assert min(a.node_id, b.node_id, c.node_id) in surviving_ids

    # --- Fix 3: content_hash guard regression tests ---


class TestFindMergeCandidatesContentHash:
    """FIX 3: regression tests for the content_hash same-version guard."""

    def test_fix3_same_content_hash_not_a_candidate(self) -> None:
        """Two nodes with same (path,name,kind) and SAME content_hash must NOT
        produce a merge candidate — they are legitimately distinct symbols in
        the same file version (e.g. two 'render' methods in different classes).
        """
        a = GraphNode.make(
            kind="function",
            name="render",
            source_path="src/a.py",
            content_hash="same_hash_abc",
            line_start=0,
            line_end=5,
        )
        b = GraphNode.make(
            kind="function",
            name="render",
            source_path="src/a.py",
            content_hash="same_hash_abc",
            line_start=20,
            line_end=25,
        )
        candidates = find_merge_candidates([a, b])
        assert candidates == [], (
            "Same content_hash means same file version; should NOT be a merge candidate"
        )

    def test_fix3_different_content_hash_is_a_candidate(self) -> None:
        """Two nodes with same (path,name,kind) but DIFFERENT content_hash are
        cross-version ghost duplicates and MUST produce exactly one candidate.
        """
        a = GraphNode.make(
            kind="function",
            name="render",
            source_path="src/a.py",
            content_hash="hash_version_1",
            line_start=0,
            line_end=5,
        )
        b = GraphNode.make(
            kind="function",
            name="render",
            source_path="src/a.py",
            content_hash="hash_version_2",
            line_start=20,
            line_end=25,
        )
        candidates = find_merge_candidates([a, b])
        assert len(candidates) == 1
        assert set(candidates[0].node_ids) == {a.node_id, b.node_id}


# Keep existing tests passing: update the ones that use same content_hash
# so they reflect the new intent (different hash = cross-version duplicate).
# The existing tests in TestFindMergeCandidates already use content_hash="h"
# for BOTH nodes in the same group — these now correctly test the
# same-hash (no-candidate) case vs. different-hash (candidate) case.
