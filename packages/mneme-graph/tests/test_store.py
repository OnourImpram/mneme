"""Tests for GraphStore: load/save/rebuild idempotency."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_graph.schema import GraphEdge, GraphNode
from mneme_graph.store import GraphStore


def _make_node(name: str, source_path: str = "a.py") -> GraphNode:
    return GraphNode.make(
        kind="function",
        name=name,
        source_path=source_path,
        content_hash="abc" * 21 + "ab",  # 64 hex chars
        line_start=0,
        line_end=10,
    )


def _make_edge(src: GraphNode, dst: GraphNode) -> GraphEdge:
    return GraphEdge.make(src_id=src.node_id, dst_id=dst.node_id, kind="defines")


class TestGraphStoreEmpty:
    def test_empty_store_has_no_nodes_or_edges(self, tmp_path: Path) -> None:
        store = GraphStore(tmp_path)
        assert store.nodes == []
        assert store.edges == []

    def test_graph_path_is_under_mneme_dir(self, tmp_path: Path) -> None:
        store = GraphStore(tmp_path)
        assert store.graph_path == tmp_path / ".mneme" / "graph.json"

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        store = GraphStore.load(tmp_path)
        assert store.nodes == []
        assert store.edges == []


class TestGraphStoreSaveLoad:
    def test_save_creates_graph_json(self, tmp_path: Path) -> None:
        store = GraphStore(tmp_path)
        node = _make_node("foo")
        store.add_nodes([node])
        store.save()
        assert store.graph_path.is_file()

    def test_save_creates_mneme_dir_when_absent(self, tmp_path: Path) -> None:
        """save() must succeed even when .mneme/ does not yet exist."""
        # tmp_path is a fresh directory with no .mneme/ subdirectory.
        assert not (tmp_path / ".mneme").exists()
        store = GraphStore(tmp_path)
        store.add_nodes([_make_node("bar")])
        store.save()  # must not raise OSError
        assert store.graph_path.is_file()
        assert (tmp_path / ".mneme").is_dir()

    def test_save_load_roundtrip_nodes(self, tmp_path: Path) -> None:
        node_a = _make_node("alpha")
        node_b = _make_node("beta")
        store = GraphStore(tmp_path)
        store.add_nodes([node_a, node_b])
        store.save()

        loaded = GraphStore.load(tmp_path)
        assert len(loaded.nodes) == 2
        loaded_ids = {n.node_id for n in loaded.nodes}
        assert node_a.node_id in loaded_ids
        assert node_b.node_id in loaded_ids

    def test_save_load_roundtrip_edges(self, tmp_path: Path) -> None:
        src = _make_node("src_fn")
        dst = _make_node("dst_fn")
        edge = _make_edge(src, dst)
        store = GraphStore(tmp_path)
        store.add_nodes([src, dst])
        store.add_edges([edge])
        store.save()

        loaded = GraphStore.load(tmp_path)
        assert len(loaded.edges) == 1
        assert loaded.edges[0].edge_id == edge.edge_id
        assert loaded.edges[0].kind == "defines"

    def test_nodes_sorted_by_node_id(self, tmp_path: Path) -> None:
        nodes = [_make_node(f"fn_{i}") for i in range(5)]
        store = GraphStore(tmp_path)
        store.add_nodes(nodes)
        ids = [n.node_id for n in store.nodes]
        assert ids == sorted(ids)

    def test_edges_sorted_by_edge_id(self, tmp_path: Path) -> None:
        nodes = [_make_node(f"fn_{i}") for i in range(4)]
        edges = [
            GraphEdge.make(src_id=nodes[i].node_id, dst_id=nodes[i + 1].node_id, kind="defines")
            for i in range(3)
        ]
        store = GraphStore(tmp_path)
        store.add_edges(edges)
        edge_ids = [e.edge_id for e in store.edges]
        assert edge_ids == sorted(edge_ids)


class TestGraphStoreRebuildIdempotency:
    def test_rebuild_produces_identical_result(self, tmp_path: Path) -> None:
        """Deleting graph.json and re-adding same nodes/edges gives same file."""
        node_a = _make_node("fn_a")
        node_b = _make_node("fn_b")
        edge = _make_edge(node_a, node_b)

        store1 = GraphStore(tmp_path)
        store1.add_nodes([node_a, node_b])
        store1.add_edges([edge])
        store1.save()
        content1 = store1.graph_path.read_text(encoding="utf-8")

        # Delete the graph file and rebuild from scratch.
        store1.graph_path.unlink()
        store2 = GraphStore(tmp_path)
        store2.add_nodes([node_a, node_b])
        store2.add_edges([edge])
        store2.save()
        content2 = store2.graph_path.read_text(encoding="utf-8")

        assert content1 == content2

    def test_overwrite_same_node_id_keeps_latest(self, tmp_path: Path) -> None:
        """add_nodes with collision on node_id: last write wins."""
        node_v1 = GraphNode.make(
            kind="function", name="fn", source_path="a.py",
            content_hash="old_hash" + "0" * 56, line_start=0, line_end=5,
        )
        node_v2 = GraphNode.make(
            kind="function", name="fn", source_path="a.py",
            content_hash="new_hash" + "0" * 56, line_start=0, line_end=10,
        )
        # Same node_id because source_path + name + kind are identical.
        assert node_v1.node_id == node_v2.node_id

        store = GraphStore(tmp_path)
        store.add_nodes([node_v1, node_v2])
        assert len(store.nodes) == 1
        assert store.nodes[0].content_hash == node_v2.content_hash


class TestGraphStoreClear:
    def test_clear_empties_store(self, tmp_path: Path) -> None:
        store = GraphStore(tmp_path)
        store.add_nodes([_make_node("fn")])
        store.clear()
        assert store.nodes == []
        assert store.edges == []


class TestGraphStoreCorruptedFile:
    def test_load_ignores_invalid_json(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        (mneme_dir / "graph.json").write_text("not json {{", encoding="utf-8")
        store = GraphStore.load(tmp_path)
        assert store.nodes == []

    def test_load_ignores_malformed_node(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        payload = {
            "version": 1,
            "nodes": [{"bad": "entry"}],
            "edges": [],
        }
        (mneme_dir / "graph.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        store = GraphStore.load(tmp_path)
        assert store.nodes == []

    def test_save_is_atomic_no_tmp_leftover(self, tmp_path: Path) -> None:
        store = GraphStore(tmp_path)
        store.add_nodes([_make_node("fn")])
        store.save()
        leftover = [p for p in (tmp_path / ".mneme").iterdir() if ".tmp" in p.name]
        assert leftover == []
