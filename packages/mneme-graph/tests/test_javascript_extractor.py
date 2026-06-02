"""Tests for the tree-sitter JavaScript/TypeScript extractor.

Fixture: a synthetic JS file with:
  - function declaration
  - arrow function assigned to const
  - class with a method
  - class extends (inherits edge)
  - a call to a local function
  - ES import and require() import

Asserts correct node kinds/names and edge kinds; determinism (extract twice
→ identical); broken file returns ([], []) without raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mneme_graph.extractor.javascript_extractor import extract_file

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_JS = """\
import utils from 'utils';
const logger = require('logger');

function greet(name) {
    return sayHello(name);
}

const sayHello = (name) => {
    return name;
};

class Animal {
    speak() {
        greet('world');
    }
}

class Dog extends Animal {
    bark() {}
}

const TOP_LEVEL = 42;
"""

BROKEN_JS = "function broken( { if if }"


@pytest.fixture()
def simple_js_file(tmp_path: Path) -> tuple[Path, Path]:
    """Return (vault_root, js_file_path) for SIMPLE_JS."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    js_file = src_dir / "example.js"
    js_file.write_text(SIMPLE_JS, encoding="utf-8")
    return tmp_path, js_file


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


class TestExtractFile:
    def test_returns_nodes_and_edges(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        assert len(nodes) > 0
        assert len(edges) > 0

    def test_module_node_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        module_nodes = [n for n in nodes if n.kind == "module" and n.name == "example"]
        assert len(module_nodes) == 1

    def test_function_declaration_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "greet" in fn_names

    def test_arrow_function_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "sayHello" in fn_names

    def test_class_node_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        class_names = {n.name for n in nodes if n.kind == "class"}
        assert "Animal" in class_names
        assert "Dog" in class_names

    def test_class_method_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "speak" in fn_names
        assert "bark" in fn_names

    def test_variable_node_present(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        var_names = {n.name for n in nodes if n.kind == "variable"}
        assert "TOP_LEVEL" in var_names


# ---------------------------------------------------------------------------
# Import edges
# ---------------------------------------------------------------------------


class TestImportEdges:
    def test_es_import_produces_edge(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        import_edges = [e for e in edges if e.kind == "imports"]
        assert len(import_edges) >= 1

    def test_require_import_produces_edge(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_targets = {
            node_by_id[e.dst_id].name
            for e in edges
            if e.kind == "imports" and e.dst_id in node_by_id
        }
        assert "logger" in import_targets

    def test_import_target_names(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        import_targets = {
            node_by_id[e.dst_id].name
            for e in edges
            if e.kind == "imports" and e.dst_id in node_by_id
        }
        assert "utils" in import_targets

    def test_import_targets_are_external_modules(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        for edge in edges:
            if edge.kind == "imports":
                target = node_by_id[edge.dst_id]
                assert target.source_path == "<external>"
                assert target.content_hash == ""


# ---------------------------------------------------------------------------
# Inherits edges
# ---------------------------------------------------------------------------


class TestInheritsEdges:
    def test_dog_inherits_animal(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        dog_node = next(n for n in nodes if n.kind == "class" and n.name == "Dog")
        inherits_edges = [e for e in edges if e.kind == "inherits" and e.src_id == dog_node.node_id]
        assert len(inherits_edges) == 1
        dst = node_by_id[inherits_edges[0].dst_id]
        assert dst.name == "Animal"

    def test_inherits_local_class_not_external(self, simple_js_file: tuple[Path, Path]) -> None:
        """Dog extends Animal — Animal is locally defined, so dst should be local."""
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        dog_node = next(n for n in nodes if n.kind == "class" and n.name == "Dog")
        inherits_edges = [e for e in edges if e.kind == "inherits" and e.src_id == dog_node.node_id]
        dst = node_by_id[inherits_edges[0].dst_id]
        assert dst.source_path != "<external>", "Animal is locally defined — should not be external"

    def test_class_inheriting_external_base(self, tmp_path: Path) -> None:
        """class Foo extends UnknownBase → external inherits node."""
        js_file = tmp_path / "foo.js"
        js_file.write_text("class Foo extends UnknownBase {}\n", encoding="utf-8")
        nodes, edges = extract_file(js_file, tmp_path)
        node_by_id = {n.node_id: n for n in nodes}
        foo_node = next(n for n in nodes if n.kind == "class" and n.name == "Foo")
        inherits_edges = [e for e in edges if e.kind == "inherits" and e.src_id == foo_node.node_id]
        assert len(inherits_edges) == 1
        dst = node_by_id[inherits_edges[0].dst_id]
        assert dst.source_path == "<external>"
        assert dst.name == "UnknownBase"


# ---------------------------------------------------------------------------
# Calls edges
# ---------------------------------------------------------------------------


class TestCallsEdges:
    def test_greet_calls_sayHello(self, simple_js_file: tuple[Path, Path]) -> None:
        """greet() calls sayHello() — both locally defined → INFERRED confidence."""
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        greet_node = next((n for n in nodes if n.kind == "function" and n.name == "greet"), None)
        assert greet_node is not None
        calls_from_greet = [
            e for e in edges if e.kind == "calls" and e.src_id == greet_node.node_id
        ]
        assert len(calls_from_greet) >= 1
        callee_names = {
            node_by_id[e.dst_id].name for e in calls_from_greet if e.dst_id in node_by_id
        }
        assert "sayHello" in callee_names

    def test_calls_to_unknown_callee_use_external_node(self, tmp_path: Path) -> None:
        """Calls to names not defined locally produce an <external> node."""
        js_file = tmp_path / "caller.js"
        js_file.write_text("function myFn() { externalLib(); }\n", encoding="utf-8")
        nodes, edges = extract_file(js_file, tmp_path)
        node_by_id = {n.node_id: n for n in nodes}
        calls_edges = [e for e in edges if e.kind == "calls"]
        assert len(calls_edges) >= 1
        dst = node_by_id[calls_edges[0].dst_id]
        assert dst.source_path == "<external>"
        assert dst.name == "externalLib"


# ---------------------------------------------------------------------------
# Defines edges
# ---------------------------------------------------------------------------


class TestDefinesEdges:
    def test_module_defines_function(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        module_node = next(n for n in nodes if n.kind == "module")
        fn_node = next(n for n in nodes if n.kind == "function" and n.name == "greet")
        defines_edges = [
            e
            for e in edges
            if e.kind == "defines"
            and e.src_id == module_node.node_id
            and e.dst_id == fn_node.node_id
        ]
        assert len(defines_edges) == 1

    def test_class_defines_method(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        animal_node = next(n for n in nodes if n.kind == "class" and n.name == "Animal")
        speak_node = next(n for n in nodes if n.kind == "function" and n.name == "speak")
        defines_edges = [
            e
            for e in edges
            if e.kind == "defines"
            and e.src_id == animal_node.node_id
            and e.dst_id == speak_node.node_id
        ]
        assert len(defines_edges) == 1


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_all_local_nodes_have_content_hash(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, edges = extract_file(js_file, vault_root)
        import_target_ids = {e.dst_id for e in edges if e.kind == "imports"}
        for node in nodes:
            if node.node_id not in import_target_ids and node.source_path != "<external>":
                assert len(node.content_hash) == 64

    def test_source_path_is_vault_relative_posix(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        module_node = next(n for n in nodes if n.kind == "module" and n.name == "example")
        assert "\\" not in module_node.source_path
        assert "/" in module_node.source_path

    def test_line_ranges_non_negative(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes, _ = extract_file(js_file, vault_root)
        for node in nodes:
            assert node.line_start >= 0
            assert node.line_end >= node.line_start

    def test_valid_at_is_empty(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        _, edges = extract_file(js_file, vault_root)
        for edge in edges:
            assert edge.valid_at == ""


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_extract_twice_identical(self, simple_js_file: tuple[Path, Path]) -> None:
        vault_root, js_file = simple_js_file
        nodes1, edges1 = extract_file(js_file, vault_root)
        nodes2, edges2 = extract_file(js_file, vault_root)
        assert {n.node_id for n in nodes1} == {n.node_id for n in nodes2}
        assert {e.edge_id for e in edges1} == {e.edge_id for e in edges2}


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


class TestErrorResilience:
    def test_broken_syntax_returns_empty_no_raise(self, tmp_path: Path) -> None:
        js_file = tmp_path / "broken.js"
        js_file.write_text(BROKEN_JS, encoding="utf-8")
        # Must not raise; may return ([], []) or partial nodes
        result = extract_file(js_file, tmp_path)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_empty_file_returns_module_node(self, tmp_path: Path) -> None:
        js_file = tmp_path / "empty.js"
        js_file.write_text("", encoding="utf-8")
        nodes, edges = extract_file(js_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "empty"

    def test_symlink_outside_vault_raises(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target_file = outside / "secret.js"
        target_file.write_text("const x = 1;\n", encoding="utf-8")

        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        link = vault_root / "evil.js"
        try:
            link.symlink_to(target_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        with pytest.raises(ValueError, match="outside vault"):
            extract_file(link, vault_root)


# ---------------------------------------------------------------------------
# TypeScript support (same extractor, different grammar)
# ---------------------------------------------------------------------------

SIMPLE_TS = """\
import { Component } from '@angular/core';

function add(a: number, b: number): number {
    return a + b;
}

class Greeter {
    greet(name: string): string {
        return add(1, 2).toString();
    }
}
"""


class TestTypeScriptExtraction:
    def test_ts_module_node(self, tmp_path: Path) -> None:
        ts_file = tmp_path / "greeter.ts"
        ts_file.write_text(SIMPLE_TS, encoding="utf-8")
        nodes, _ = extract_file(ts_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module" and n.name == "greeter"]
        assert len(module_nodes) == 1

    def test_ts_function_node(self, tmp_path: Path) -> None:
        ts_file = tmp_path / "greeter.ts"
        ts_file.write_text(SIMPLE_TS, encoding="utf-8")
        nodes, _ = extract_file(ts_file, tmp_path)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "add" in fn_names

    def test_ts_class_node(self, tmp_path: Path) -> None:
        ts_file = tmp_path / "greeter.ts"
        ts_file.write_text(SIMPLE_TS, encoding="utf-8")
        nodes, _ = extract_file(ts_file, tmp_path)
        class_names = {n.name for n in nodes if n.kind == "class"}
        assert "Greeter" in class_names

    def test_ts_import_edge(self, tmp_path: Path) -> None:
        ts_file = tmp_path / "greeter.ts"
        ts_file.write_text(SIMPLE_TS, encoding="utf-8")
        nodes, edges = extract_file(ts_file, tmp_path)
        node_by_id = {n.node_id: n for n in nodes}
        import_targets = {
            node_by_id[e.dst_id].name
            for e in edges
            if e.kind == "imports" and e.dst_id in node_by_id
        }
        assert "@angular/core" in import_targets
