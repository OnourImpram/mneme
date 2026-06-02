"""Tests for GraphNode and GraphEdge schema: construction, id derivation, roundtrip."""

from __future__ import annotations

import hashlib

import pytest

from mneme_graph.schema import (
    GraphEdge,
    GraphNode,
    _sha256_id,
)


class TestSha256Id:
    def test_deterministic(self) -> None:
        assert _sha256_id("a", "b", "c") == _sha256_id("a", "b", "c")

    def test_16_hex_chars(self) -> None:
        result = _sha256_id("x", "y")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_differs_on_different_input(self) -> None:
        assert _sha256_id("a", "b") != _sha256_id("b", "a")

    def test_matches_manual_sha256(self) -> None:
        raw = "src/foo.py:my_func:function"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:16]
        assert _sha256_id("src/foo.py", "my_func", "function") == expected


class TestGraphNodeMake:
    def test_node_id_is_derived(self) -> None:
        node = GraphNode.make(
            kind="function",
            name="my_func",
            source_path="src/foo.py",
            content_hash="abc123",
            line_start=10,
            line_end=20,
        )
        # Local nodes fold line_start into the id to disambiguate same-named
        # symbols in one file.
        expected_id = _sha256_id("src/foo.py", "my_func", "function", "10")
        assert node.node_id == expected_id

    def test_default_confidence_and_trust(self) -> None:
        node = GraphNode.make(
            kind="class",
            name="MyClass",
            source_path="src/bar.py",
            content_hash="def456",
            line_start=0,
            line_end=5,
        )
        assert node.confidence == "EXTRACTED"
        assert node.trust == "extracted"

    def test_frozen(self) -> None:
        node = GraphNode.make(
            kind="module",
            name="foo",
            source_path="foo.py",
            content_hash="x",
            line_start=0,
            line_end=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            node.name = "bar"  # type: ignore[misc]

    def test_same_inputs_same_id(self) -> None:
        a = GraphNode.make(
            kind="function", name="f", source_path="p.py",
            content_hash="h", line_start=1, line_end=2,
        )
        b = GraphNode.make(
            kind="function", name="f", source_path="p.py",
            content_hash="h", line_start=1, line_end=2,
        )
        assert a.node_id == b.node_id
        assert a == b


class TestGraphNodeRoundtrip:
    def test_to_dict_from_dict(self) -> None:
        node = GraphNode.make(
            kind="class",
            name="Foo",
            source_path="pkg/foo.py",
            content_hash="cafebabe",
            line_start=5,
            line_end=50,
            confidence="INFERRED",
            trust="extracted",
        )
        d = node.to_dict()
        assert d["kind"] == "class"
        assert d["name"] == "Foo"
        assert d["confidence"] == "INFERRED"

        restored = GraphNode.from_dict(d)
        assert restored == node

    def test_from_dict_defaults(self) -> None:
        """from_dict fills confidence/trust defaults when keys are absent."""
        d = {
            "node_id": "abc1234567890123",
            "kind": "function",
            "name": "fn",
            "source_path": "a.py",
            "content_hash": "x" * 64,
            "line_start": 0,
            "line_end": 1,
        }
        node = GraphNode.from_dict(d)
        assert node.confidence == "EXTRACTED"
        assert node.trust == "extracted"

    def test_confidence_labels(self) -> None:
        for label in ("EXTRACTED", "INFERRED", "AMBIGUOUS"):
            node = GraphNode.make(
                kind="function", name="f", source_path="f.py",
                content_hash="h", line_start=0, line_end=0,
                confidence=label,  # type: ignore[arg-type]
            )
            assert node.confidence == label
            assert GraphNode.from_dict(node.to_dict()).confidence == label


class TestGraphEdgeMake:
    def test_edge_id_is_derived(self) -> None:
        edge = GraphEdge.make(src_id="aaaa", dst_id="bbbb", kind="imports")
        expected = _sha256_id("aaaa", "bbbb", "imports")
        assert edge.edge_id == expected

    def test_frozen(self) -> None:
        edge = GraphEdge.make(src_id="a", dst_id="b", kind="defines")
        with pytest.raises((AttributeError, TypeError)):
            edge.src_id = "c"  # type: ignore[misc]

    def test_same_inputs_same_id(self) -> None:
        a = GraphEdge.make(src_id="x", dst_id="y", kind="calls")
        b = GraphEdge.make(src_id="x", dst_id="y", kind="calls")
        assert a == b


class TestGraphEdgeRoundtrip:
    def test_to_dict_from_dict(self) -> None:
        edge = GraphEdge.make(
            src_id="node_a",
            dst_id="node_b",
            kind="inherits",
            confidence="AMBIGUOUS",
            valid_at="2026-01-01T00:00:00+00:00",
        )
        d = edge.to_dict()
        assert d["kind"] == "inherits"
        assert d["confidence"] == "AMBIGUOUS"

        restored = GraphEdge.from_dict(d)
        assert restored == edge

    def test_from_dict_defaults(self) -> None:
        d = {
            "edge_id": "e" * 16,
            "src_id": "src",
            "dst_id": "dst",
            "kind": "imports",
        }
        edge = GraphEdge.from_dict(d)
        assert edge.confidence == "EXTRACTED"
        assert edge.valid_at == ""
