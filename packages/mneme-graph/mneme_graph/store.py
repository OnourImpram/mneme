"""Atomic GraphStore: read/write graph.json under vault/.mneme/graph.json.

The graph is a derived, rebuildable artifact. Deleting graph.json and running
the extractor over the same source files must produce an identical result.

Atomicity: writes go through mneme_core.vault.atomic_write.atomic_write_text
so the file is either the old content or the new content; no partial writes.

Thread safety: the store does not hold a file lock — callers that need
exclusive access must acquire mneme_core.vault.file_lock themselves. For
the rebuild CLI (single-writer, no concurrent readers during rebuild) this
is sufficient.
"""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.vault.atomic_write import atomic_write_text

from .schema import GraphEdge, GraphNode

# Derived path: <vault_root>/.mneme/graph.json
_GRAPH_FILENAME = "graph.json"
_MNEME_DIR = ".mneme"


class GraphStore:
    """In-memory graph with atomic persistence to graph.json.

    Typical usage:

        store = GraphStore.load(vault_root)
        store.add_nodes(nodes)
        store.add_edges(edges)
        store.save()

    Re-building from scratch:

        store = GraphStore(vault_root)   # starts empty
        for path in py_files:
            nodes, edges = extract_file(path, vault_root)
            store.add_nodes(nodes)
            store.add_edges(edges)
        store.save()
    """

    def __init__(self, vault_root: Path) -> None:
        self._vault_root = vault_root
        self._graph_path = vault_root / _MNEME_DIR / _GRAPH_FILENAME
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}

    @property
    def graph_path(self) -> Path:
        """Absolute path of the derived graph.json file."""
        return self._graph_path

    @property
    def nodes(self) -> list[GraphNode]:
        """Ordered list of all nodes (sorted by node_id for determinism)."""
        return sorted(self._nodes.values(), key=lambda n: n.node_id)

    @property
    def edges(self) -> list[GraphEdge]:
        """Ordered list of all edges (sorted by edge_id for determinism)."""
        return sorted(self._edges.values(), key=lambda e: e.edge_id)

    def add_nodes(self, nodes: list[GraphNode]) -> None:
        """Merge nodes into the store; later node wins on node_id collision."""
        for node in nodes:
            self._nodes[node.node_id] = node

    def add_edges(self, edges: list[GraphEdge]) -> None:
        """Merge edges into the store; later edge wins on edge_id collision."""
        for edge in edges:
            self._edges[edge.edge_id] = edge

    def clear(self) -> None:
        """Remove all nodes and edges (use before a full rebuild pass)."""
        self._nodes.clear()
        self._edges.clear()

    def save(self) -> None:
        """Write graph.json atomically via atomic_write_text."""
        self._graph_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": 1,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        atomic_write_text(self._graph_path, content)

    @classmethod
    def load(cls, vault_root: Path) -> GraphStore:
        """Load from graph.json if present; return an empty store if absent.

        Never raises on a missing or unreadable file — callers should check
        ``store.nodes`` and ``store.edges`` to detect an empty state.
        """
        store = cls(vault_root)
        graph_path = vault_root / _MNEME_DIR / _GRAPH_FILENAME
        if not graph_path.is_file():
            return store
        try:
            raw = graph_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return store

        if not isinstance(data, dict):
            return store

        for node_dict in data.get("nodes", []):
            try:
                node = GraphNode.from_dict(node_dict)
                store._nodes[node.node_id] = node
            except (KeyError, ValueError, TypeError):
                continue

        for edge_dict in data.get("edges", []):
            try:
                edge = GraphEdge.from_dict(edge_dict)
                store._edges[edge.edge_id] = edge
            except (KeyError, ValueError, TypeError):
                continue

        return store
