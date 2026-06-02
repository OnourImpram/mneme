"""Tests for the extractor registry (mneme_graph.extractor).

Verifies:
  - extract_any dispatches .py to python_extractor
  - extract_any dispatches .js to javascript_extractor
  - Unknown suffix (.txt) returns ([], [])
  - SUPPORTED_SUFFIXES includes .py and .js
  - Importing mneme_graph.extractor does NOT require grammars to be installed
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Import safety: importing the package must not crash without grammars
# ---------------------------------------------------------------------------


class TestImportSafety:
    def test_import_extractor_package_succeeds(self) -> None:
        """Importing mneme_graph.extractor must not raise even without grammars."""
        import importlib

        mod = importlib.import_module("mneme_graph.extractor")
        assert mod is not None

    def test_supported_suffixes_attribute_present(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        assert isinstance(SUPPORTED_SUFFIXES, tuple)

    def test_extractors_dict_present(self) -> None:
        from mneme_graph.extractor import EXTRACTORS

        assert isinstance(EXTRACTORS, dict)

    def test_extract_any_callable(self) -> None:
        from mneme_graph.extractor import extract_any

        assert callable(extract_any)


# ---------------------------------------------------------------------------
# SUPPORTED_SUFFIXES content
# ---------------------------------------------------------------------------


class TestSupportedSuffixes:
    def test_contains_py(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        assert ".py" in SUPPORTED_SUFFIXES

    def test_contains_js(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        assert ".js" in SUPPORTED_SUFFIXES

    def test_contains_ts(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        assert ".ts" in SUPPORTED_SUFFIXES

    def test_contains_jsx(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        assert ".jsx" in SUPPORTED_SUFFIXES

    def test_all_lowercase(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        for suffix in SUPPORTED_SUFFIXES:
            assert suffix == suffix.lower(), f"Suffix {suffix!r} is not lowercase"

    def test_all_start_with_dot(self) -> None:
        from mneme_graph.extractor import SUPPORTED_SUFFIXES

        for suffix in SUPPORTED_SUFFIXES:
            assert suffix.startswith("."), f"Suffix {suffix!r} does not start with '.'"


# ---------------------------------------------------------------------------
# Dispatch: .py → python extractor
# ---------------------------------------------------------------------------


class TestDispatchPython:
    def test_py_file_dispatched(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        py_file = tmp_path / "mod.py"
        py_file.write_text("def hello(): pass\n", encoding="utf-8")
        nodes, edges = extract_any(py_file, tmp_path)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        # Python extractor always emits a module node
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "mod"

    def test_py_function_extracted(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        py_file = tmp_path / "funcs.py"
        py_file.write_text("def alpha(): pass\ndef beta(): pass\n", encoding="utf-8")
        nodes, _ = extract_any(py_file, tmp_path)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "alpha" in fn_names
        assert "beta" in fn_names


# ---------------------------------------------------------------------------
# Dispatch: .js / .jsx / .mjs / .cjs / .ts / .tsx → javascript extractor
# ---------------------------------------------------------------------------


class TestDispatchJavaScript:
    def test_js_file_dispatched(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        js_file = tmp_path / "app.js"
        js_file.write_text("function run() {}\n", encoding="utf-8")
        nodes, edges = extract_any(js_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "app"

    def test_jsx_file_dispatched(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        jsx_file = tmp_path / "comp.jsx"
        jsx_file.write_text("function MyComp() {}\n", encoding="utf-8")
        nodes, _ = extract_any(jsx_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1

    def test_mjs_file_dispatched(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        mjs_file = tmp_path / "esm.mjs"
        mjs_file.write_text("export function foo() {}\n", encoding="utf-8")
        nodes, _ = extract_any(mjs_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1

    def test_ts_file_dispatched(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("function init(): void {}\n", encoding="utf-8")
        nodes, _ = extract_any(ts_file, tmp_path)
        module_nodes = [n for n in nodes if n.kind == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "service"

    def test_js_produces_function_node(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        js_file = tmp_path / "util.js"
        js_file.write_text("function compute(x) { return x * 2; }\n", encoding="utf-8")
        nodes, _ = extract_any(js_file, tmp_path)
        fn_names = {n.name for n in nodes if n.kind == "function"}
        assert "compute" in fn_names


# ---------------------------------------------------------------------------
# Unknown suffix → ([], [])
# ---------------------------------------------------------------------------


class TestUnknownSuffix:
    def test_txt_returns_empty(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("hello world\n", encoding="utf-8")
        nodes, edges = extract_any(txt_file, tmp_path)
        assert nodes == []
        assert edges == []

    def test_md_returns_empty(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        md_file = tmp_path / "README.md"
        md_file.write_text("# Title\n", encoding="utf-8")
        nodes, edges = extract_any(md_file, tmp_path)
        assert nodes == []
        assert edges == []

    def test_json_returns_empty(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        json_file = tmp_path / "config.json"
        json_file.write_text('{"key": "value"}\n', encoding="utf-8")
        nodes, edges = extract_any(json_file, tmp_path)
        assert nodes == []
        assert edges == []

    def test_no_suffix_returns_empty(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        no_suffix = tmp_path / "Makefile"
        no_suffix.write_text("all:\n\techo hi\n", encoding="utf-8")
        nodes, edges = extract_any(no_suffix, tmp_path)
        assert nodes == []
        assert edges == []


# ---------------------------------------------------------------------------
# Determinism via extract_any
# ---------------------------------------------------------------------------


class TestRegistryDeterminism:
    def test_py_extract_any_idempotent(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        py_file = tmp_path / "idem.py"
        py_file.write_text("class Foo:\n    def bar(self): pass\n", encoding="utf-8")
        nodes1, edges1 = extract_any(py_file, tmp_path)
        nodes2, edges2 = extract_any(py_file, tmp_path)
        assert {n.node_id for n in nodes1} == {n.node_id for n in nodes2}
        assert {e.edge_id for e in edges1} == {e.edge_id for e in edges2}

    def test_js_extract_any_idempotent(self, tmp_path: Path) -> None:
        from mneme_graph.extractor import extract_any

        js_file = tmp_path / "idem.js"
        js_file.write_text("class Bar { baz() {} }\n", encoding="utf-8")
        nodes1, edges1 = extract_any(js_file, tmp_path)
        nodes2, edges2 = extract_any(js_file, tmp_path)
        assert {n.node_id for n in nodes1} == {n.node_id for n in nodes2}
        assert {e.edge_id for e in edges1} == {e.edge_id for e in edges2}
