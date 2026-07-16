"""Tests for the tree-sitter Python extractor.

Fixture: a synthetic 3-function .py file with imports, a class, and top-level
functions. Asserts correct node kinds, names, line ranges, and import edges.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mneme_graph.extractor.python_extractor import extract_file

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_PY = """\
import os
from pathlib import Path

class MyClass:
    def method_one(self): pass
    def method_two(self): pass

def standalone_func(x):
    return x + 1

async def async_func():
    pass
"""


@pytest.fixture()
def simple_file(tmp_path: Path) -> tuple[Path, Path]:
    """Return (vault_root, py_file_path) for SIMPLE_PY."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    py_file = src_dir / "example.py"
    py_file.write_text(SIMPLE_PY, encoding="utf-8")
    return tmp_path, py_file


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

class TestExtractFile:
    def test_returns_nodes_and_edges(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        assert len(nodes) > 0
        assert len(edges) > 0

    def test_module_node_present(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        module_nodes = [n for n in nodes if n.kind == "module" and n.name == "example"]
        assert len(module_nodes) == 1

    def test_class_node_present(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        class_nodes = [n for n in nodes if n.kind == "class" and n.name == "MyClass"]
        assert len(class_nodes) == 1

    def test_method_nodes_present(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "method_one" in fn_names
        assert "method_two" in fn_names

    def test_standalone_function_present(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "standalone_func" in fn_names

    def test_async_function_present(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "async_func" in fn_names


class TestImportEdges:
    def test_import_os_produces_edge(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        import_edges = [e for e in edges if e.kind == "imports"]
        assert len(import_edges) >= 2  # os + pathlib

    def test_import_targets_are_module_nodes(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_edges = [e for e in edges if e.kind == "imports"]
        for edge in import_edges:
            assert edge.dst_id in node_by_id
            target = node_by_id[edge.dst_id]
            assert target.kind == "module"

    def test_import_target_names(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_targets = {
            node_by_id[e.dst_id].name
            for e in edges
            if e.kind == "imports"
        }
        assert "os" in import_targets
        assert "pathlib" in import_targets


class TestDefinesEdges:
    def test_module_defines_class(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        module_node = next(n for n in nodes if n.kind == "module" and n.name == "example")
        class_node = next(n for n in nodes if n.kind == "class" and n.name == "MyClass")
        module_to_class = [
            e for e in edges
            if e.kind == "defines" and e.src_id == module_node.node_id
            and e.dst_id == class_node.node_id
        ]
        assert len(module_to_class) == 1

    def test_class_defines_methods(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        class_node = next(n for n in nodes if n.kind == "class" and n.name == "MyClass")
        defines_from_class = [
            e for e in edges
            if e.kind == "defines" and e.src_id == class_node.node_id
        ]
        assert len(defines_from_class) == 2
        node_by_id = {n.node_id: n for n in nodes}
        defined_names = {node_by_id[e.dst_id].name for e in defines_from_class}
        assert "method_one" in defined_names
        assert "method_two" in defined_names


class TestProvenance:
    def test_all_nodes_have_extracted_confidence(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        for node in nodes:
            assert node.confidence == "EXTRACTED"

    def test_all_nodes_have_trust_extracted(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        for node in nodes:
            assert node.trust == "extracted"

    def test_source_nodes_have_file_source_path(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        """Non-import-target nodes carry the file's vault-relative path."""
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        import_target_ids = {e.dst_id for e in edges if e.kind == "imports"}
        source_nodes = [n for n in nodes if n.node_id not in import_target_ids]
        for node in source_nodes:
            assert node.source_path.endswith("example.py")

    def test_external_import_targets_have_external_source_path(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        """Import-target nodes carry source_path='<external>'."""
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_target_ids = {e.dst_id for e in edges if e.kind == "imports"}
        for tid in import_target_ids:
            assert node_by_id[tid].source_path == "<external>"

    def test_source_nodes_have_content_hash(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        """Non-import-target nodes carry a 64-char sha256 hex digest."""
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        import_target_ids = {e.dst_id for e in edges if e.kind == "imports"}
        source_nodes = [n for n in nodes if n.node_id not in import_target_ids]
        for node in source_nodes:
            assert len(node.content_hash) == 64
            assert all(c in "0123456789abcdef" for c in node.content_hash)

    def test_external_import_targets_have_empty_content_hash(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        """Import-target nodes carry content_hash='' (no file hash to attribute)."""
        vault_root, py_file = simple_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_target_ids = {e.dst_id for e in edges if e.kind == "imports"}
        for tid in import_target_ids:
            assert node_by_id[tid].content_hash == ""

    def test_source_path_is_vault_relative_posix(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        module_node = next(n for n in nodes if n.kind == "module")
        assert "/" in module_node.source_path
        assert "\\" not in module_node.source_path

    def test_line_ranges_are_non_negative(
        self, simple_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = simple_file
        nodes, _ = extract_file(py_file, vault_root)
        for node in nodes:
            assert node.line_start >= 0
            assert node.line_end >= node.line_start


class TestSymlinkEscape:
    def test_symlink_outside_vault_raises(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target_file = outside / "secret.py"
        target_file.write_text("x = 1\n", encoding="utf-8")

        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        link = vault_root / "evil.py"
        try:
            link.symlink_to(target_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        with pytest.raises(ValueError, match="outside vault"):
            extract_file(link, vault_root)


class TestEmptyFile:
    def test_empty_file_yields_module_node(self, tmp_path: Path) -> None:
        py_file = tmp_path / "empty.py"
        py_file.write_text("", encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "empty"

    def test_empty_file_no_edges(self, tmp_path: Path) -> None:
        py_file = tmp_path / "empty.py"
        py_file.write_text("", encoding="utf-8")
        _, edges = extract_file(py_file, tmp_path)
        assert edges == []


class TestTraversalResilience:
    def test_deep_call_tree_does_not_use_python_recursion(self, tmp_path: Path) -> None:
        depth = 1_200
        expression = "external(" * depth + "0" + ")" * depth
        py_file = tmp_path / "deep_calls.py"
        py_file.write_text(
            f"def caller():\n    return {expression}\n",
            encoding="utf-8",
        )

        nodes, edges = extract_file(py_file, tmp_path)

        caller = next(node for node in nodes if node.name == "caller")
        external = next(
            node
            for node in nodes
            if node.name == "external" and node.source_path == "<external>"
        )
        call_edges = [
            edge
            for edge in edges
            if edge.kind == "calls"
            and edge.src_id == caller.node_id
            and edge.dst_id == external.node_id
        ]
        assert len(call_edges) == 1


class TestIdempotency:
    def test_same_file_same_nodes(self, simple_file: tuple[Path, Path]) -> None:
        vault_root, py_file = simple_file
        nodes1, edges1 = extract_file(py_file, vault_root)
        nodes2, edges2 = extract_file(py_file, vault_root)
        assert {n.node_id for n in nodes1} == {n.node_id for n in nodes2}
        assert {e.edge_id for e in edges1} == {e.edge_id for e in edges2}


# ---------------------------------------------------------------------------
# Finding 1: external import-target nodes are stable across importers
# ---------------------------------------------------------------------------

IMPORTER_A = """\
import os
import pathlib
"""

IMPORTER_B = """\
import os
from pathlib import Path
"""


class TestExternalImportTargetStability:
    """Two files importing 'os' must reference the SAME module node_id."""

    def test_shared_os_node_id_across_two_importers(self, tmp_path: Path) -> None:
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text(IMPORTER_A, encoding="utf-8")
        file_b.write_text(IMPORTER_B, encoding="utf-8")

        nodes_a, edges_a = extract_file(file_a, tmp_path)
        nodes_b, edges_b = extract_file(file_b, tmp_path)

        node_by_id_a = {n.node_id: n for n in nodes_a}
        node_by_id_b = {n.node_id: n for n in nodes_b}

        # Locate the 'os' target node from each extraction.
        os_target_a = next(
            node_by_id_a[e.dst_id]
            for e in edges_a
            if e.kind == "imports" and node_by_id_a[e.dst_id].name == "os"
        )
        os_target_b = next(
            node_by_id_b[e.dst_id]
            for e in edges_b
            if e.kind == "imports" and node_by_id_b[e.dst_id].name == "os"
        )

        assert os_target_a.node_id == os_target_b.node_id, (
            "Two files importing 'os' must resolve to the same module node_id "
            "so the GraphStore can deduplicate them."
        )

    def test_external_node_source_path_is_external_sentinel(
        self, tmp_path: Path
    ) -> None:
        py_file = tmp_path / "c.py"
        py_file.write_text("import os\n", encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        node_by_id = {n.node_id: n for n in nodes}
        os_node = next(
            node_by_id[e.dst_id]
            for e in edges
            if e.kind == "imports" and node_by_id[e.dst_id].name == "os"
        )
        assert os_node.source_path == "<external>"
        assert os_node.content_hash == ""


# ---------------------------------------------------------------------------
# Finding 5: decorated top-level class emits method nodes
# ---------------------------------------------------------------------------

DECORATED_CLASS_PY = """\
import dataclasses

@dataclasses.dataclass
class Point:
    def distance(self): pass
    def scale(self, factor): pass
"""


class TestDecoratedClassMethods:
    """@decorator class bodies must be walked to emit method nodes."""

    def test_decorated_class_node_present(self, tmp_path: Path) -> None:
        py_file = tmp_path / "point.py"
        py_file.write_text(DECORATED_CLASS_PY, encoding="utf-8")
        nodes, _ = extract_file(py_file, tmp_path)
        class_nodes = [n for n in nodes if n.kind == "class" and n.name == "Point"]
        assert len(class_nodes) == 1

    def test_decorated_class_method_nodes_emitted(self, tmp_path: Path) -> None:
        py_file = tmp_path / "point.py"
        py_file.write_text(DECORATED_CLASS_PY, encoding="utf-8")
        nodes, _ = extract_file(py_file, tmp_path)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "distance" in fn_names, "method 'distance' must be extracted from @dataclass"
        assert "scale" in fn_names, "method 'scale' must be extracted from @dataclass"

    def test_decorated_class_defines_method_edges(self, tmp_path: Path) -> None:
        py_file = tmp_path / "point.py"
        py_file.write_text(DECORATED_CLASS_PY, encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        class_node = next(n for n in nodes if n.kind == "class" and n.name == "Point")
        defines_from_class = [
            e for e in edges
            if e.kind == "defines" and e.src_id == class_node.node_id
        ]
        assert len(defines_from_class) == 2
        node_by_id = {n.node_id: n for n in nodes}
        defined_names = {node_by_id[e.dst_id].name for e in defines_from_class}
        assert defined_names == {"distance", "scale"}
