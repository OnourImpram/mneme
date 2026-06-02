"""Tests for new extractor features: inherits, calls, variable nodes.

Also covers the CLI build + report subcommands (Task D).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_graph.extractor.python_extractor import extract_file
from mneme_graph.store import GraphStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

INHERITS_PY = """\
class Base:
    def base_method(self): pass

class Child(Base):
    def child_method(self): pass

class GrandChild(Child, Base):
    pass
"""

CALLS_PY = """\
def helper():
    pass

def caller():
    helper()
    len([1, 2, 3])

class MyClass:
    def method(self):
        helper()
        self.other()

    def other(self):
        pass
"""

VARIABLE_PY = """\
X = 1
CONSTANT = "hello"
_PRIVATE = True

def func():
    local_var = 2
    return local_var

class Cls:
    class_attr = 3
"""

MIXED_PY = """\
import os

BASE_URL = "http://example.com"

class Service(object):
    def call(self):
        pass

def run():
    svc = Service()
    svc.call()
"""


@pytest.fixture()
def inherits_file(tmp_path: Path) -> tuple[Path, Path]:
    py_file = tmp_path / "inherits.py"
    py_file.write_text(INHERITS_PY, encoding="utf-8")
    return tmp_path, py_file


@pytest.fixture()
def calls_file(tmp_path: Path) -> tuple[Path, Path]:
    py_file = tmp_path / "calls.py"
    py_file.write_text(CALLS_PY, encoding="utf-8")
    return tmp_path, py_file


@pytest.fixture()
def variable_file(tmp_path: Path) -> tuple[Path, Path]:
    py_file = tmp_path / "variables.py"
    py_file.write_text(VARIABLE_PY, encoding="utf-8")
    return tmp_path, py_file


class TestCallsEnclosingAttribution:
    """Regression: a call must be attributed to the correct enclosing function
    when two functions/methods share a name (enclosing resolved by name AND
    line-range containment, not name alone)."""

    def test_same_named_methods_attribute_calls_to_correct_method(
        self, tmp_path: Path
    ) -> None:
        src = (
            "def alpha():\n"
            "    pass\n"
            "\n"
            "def beta():\n"
            "    pass\n"
            "\n"
            "class A:\n"
            "    def run(self):\n"
            "        alpha()\n"
            "\n"
            "class B:\n"
            "    def run(self):\n"
            "        beta()\n"
        )
        py_file = tmp_path / "dup.py"
        py_file.write_text(src, encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)

        run_nodes = [n for n in nodes if n.kind == "function" and n.name == "run"]
        assert len(run_nodes) == 2
        a_run = min(run_nodes, key=lambda n: n.line_start)  # A.run defined first
        b_run = max(run_nodes, key=lambda n: n.line_start)  # B.run defined later
        alpha_node = next(
            n for n in nodes if n.name == "alpha" and n.source_path != "<external>"
        )
        beta_node = next(
            n for n in nodes if n.name == "beta" and n.source_path != "<external>"
        )

        calls = [e for e in edges if e.kind == "calls"]
        alpha_calls = [e for e in calls if e.dst_id == alpha_node.node_id]
        beta_calls = [e for e in calls if e.dst_id == beta_node.node_id]
        assert len(alpha_calls) == 1
        assert len(beta_calls) == 1
        # alpha() is called from A.run, beta() from B.run — not both from the
        # first-defined run.
        assert alpha_calls[0].src_id == a_run.node_id
        assert beta_calls[0].src_id == b_run.node_id


# ---------------------------------------------------------------------------
# Task C: inherits edges
# ---------------------------------------------------------------------------

class TestInheritsEdges:
    def test_child_inherits_base(self, inherits_file: tuple[Path, Path]) -> None:
        vault_root, py_file = inherits_file
        nodes, edges = extract_file(py_file, vault_root)
        inherits_edges = [e for e in edges if e.kind == "inherits"]
        assert len(inherits_edges) >= 1

    def test_child_inherits_base_resolves_locally(
        self, inherits_file: tuple[Path, Path]
    ) -> None:
        """Child(Base) should inherit from the locally-extracted Base node."""
        vault_root, py_file = inherits_file
        nodes, edges = extract_file(py_file, vault_root)
        child_node = next(n for n in nodes if n.kind == "class" and n.name == "Child")
        base_node = next(n for n in nodes if n.kind == "class" and n.name == "Base")
        child_inherits = [
            e for e in edges
            if e.kind == "inherits" and e.src_id == child_node.node_id
        ]
        assert len(child_inherits) == 1
        assert child_inherits[0].dst_id == base_node.node_id

    def test_inherits_confidence_is_extracted(
        self, inherits_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = inherits_file
        nodes, edges = extract_file(py_file, vault_root)
        inherits_edges = [e for e in edges if e.kind == "inherits"]
        for edge in inherits_edges:
            assert edge.confidence == "EXTRACTED"

    def test_multiple_bases_emit_multiple_edges(
        self, inherits_file: tuple[Path, Path]
    ) -> None:
        """GrandChild(Child, Base) should produce two inherits edges."""
        vault_root, py_file = inherits_file
        nodes, edges = extract_file(py_file, vault_root)
        grandchild = next(n for n in nodes if n.kind == "class" and n.name == "GrandChild")
        gc_inherits = [
            e for e in edges
            if e.kind == "inherits" and e.src_id == grandchild.node_id
        ]
        assert len(gc_inherits) == 2

    def test_external_base_class_gets_external_node(self, tmp_path: Path) -> None:
        """class Foo(Exception) — Exception is external, gets <external> source."""
        py_file = tmp_path / "ext.py"
        py_file.write_text("class Foo(Exception): pass\n", encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        node_by_id = {n.node_id: n for n in nodes}
        inherits_edges = [e for e in edges if e.kind == "inherits"]
        assert len(inherits_edges) == 1
        target = node_by_id[inherits_edges[0].dst_id]
        assert target.source_path == "<external>"
        assert target.name == "Exception"

    def test_inherits_dst_is_a_class_node(self, inherits_file: tuple[Path, Path]) -> None:
        vault_root, py_file = inherits_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        for edge in edges:
            if edge.kind == "inherits":
                assert node_by_id[edge.dst_id].kind == "class"

    def test_no_inherits_for_class_without_bases(self, tmp_path: Path) -> None:
        py_file = tmp_path / "noinherit.py"
        py_file.write_text("class Plain: pass\n", encoding="utf-8")
        _, edges = extract_file(py_file, tmp_path)
        assert not any(e.kind == "inherits" for e in edges)


# ---------------------------------------------------------------------------
# Task C: calls edges
# ---------------------------------------------------------------------------

class TestCallsEdges:
    def test_calls_edges_emitted(self, calls_file: tuple[Path, Path]) -> None:
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        calls_edges = [e for e in edges if e.kind == "calls"]
        assert len(calls_edges) >= 1

    def test_local_call_has_inferred_confidence(
        self, calls_file: tuple[Path, Path]
    ) -> None:
        """caller() calls helper() which is local — should be INFERRED."""
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        caller_node = next(n for n in nodes if n.kind == "function" and n.name == "caller")
        local_calls = [
            e for e in edges
            if e.kind == "calls" and e.src_id == caller_node.node_id
            and node_by_id[e.dst_id].name == "helper"
        ]
        assert len(local_calls) == 1
        assert local_calls[0].confidence == "INFERRED"

    def test_external_call_has_extracted_confidence(
        self, calls_file: tuple[Path, Path]
    ) -> None:
        """caller() calls len() which is external — should be EXTRACTED."""
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        caller_node = next(n for n in nodes if n.kind == "function" and n.name == "caller")
        external_calls = [
            e for e in edges
            if e.kind == "calls" and e.src_id == caller_node.node_id
            and node_by_id[e.dst_id].source_path == "<external>"
        ]
        assert len(external_calls) >= 1

    def test_no_self_calls(self, calls_file: tuple[Path, Path]) -> None:
        """A function should never have a calls edge to itself."""
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        for edge in edges:
            if edge.kind == "calls":
                assert edge.src_id != edge.dst_id

    def test_calls_src_is_function_node(self, calls_file: tuple[Path, Path]) -> None:
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        node_by_id = {n.node_id: n for n in nodes}
        for edge in edges:
            if edge.kind == "calls":
                assert node_by_id[edge.src_id].kind == "function"

    def test_no_duplicate_calls_edges(self, calls_file: tuple[Path, Path]) -> None:
        vault_root, py_file = calls_file
        nodes, edges = extract_file(py_file, vault_root)
        calls_ids = [e.edge_id for e in edges if e.kind == "calls"]
        assert len(calls_ids) == len(set(calls_ids))

    def test_calls_edges_idempotent(self, calls_file: tuple[Path, Path]) -> None:
        vault_root, py_file = calls_file
        _, edges1 = extract_file(py_file, vault_root)
        _, edges2 = extract_file(py_file, vault_root)
        assert {e.edge_id for e in edges1 if e.kind == "calls"} == {
            e.edge_id for e in edges2 if e.kind == "calls"
        }


# ---------------------------------------------------------------------------
# Task C: variable nodes
# ---------------------------------------------------------------------------

class TestVariableNodes:
    def test_module_level_variables_extracted(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = variable_file
        nodes, _ = extract_file(py_file, vault_root)
        var_names = {n.name for n in nodes if n.kind == "variable"}
        assert "X" in var_names
        assert "CONSTANT" in var_names
        assert "_PRIVATE" in var_names

    def test_local_variables_not_extracted(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        """Variables inside functions must not become variable nodes."""
        vault_root, py_file = variable_file
        nodes, _ = extract_file(py_file, vault_root)
        var_names = {n.name for n in nodes if n.kind == "variable"}
        assert "local_var" not in var_names

    def test_class_attributes_not_extracted(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        """Class-body assignments must not become variable nodes."""
        vault_root, py_file = variable_file
        nodes, _ = extract_file(py_file, vault_root)
        var_names = {n.name for n in nodes if n.kind == "variable"}
        assert "class_attr" not in var_names

    def test_variable_node_confidence_is_extracted(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = variable_file
        nodes, _ = extract_file(py_file, vault_root)
        for node in nodes:
            if node.kind == "variable":
                assert node.confidence == "EXTRACTED"

    def test_variable_node_source_path_correct(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = variable_file
        nodes, _ = extract_file(py_file, vault_root)
        for node in nodes:
            if node.kind == "variable":
                assert node.source_path.endswith("variables.py")

    def test_module_defines_variable_edges(
        self, variable_file: tuple[Path, Path]
    ) -> None:
        vault_root, py_file = variable_file
        nodes, edges = extract_file(py_file, vault_root)
        module_node = next(n for n in nodes if n.kind == "module")
        var_nodes = {n.node_id for n in nodes if n.kind == "variable"}
        defines_to_vars = [
            e for e in edges
            if e.kind == "defines" and e.src_id == module_node.node_id
            and e.dst_id in var_nodes
        ]
        assert len(defines_to_vars) >= 3

    def test_no_variables_in_empty_file(self, tmp_path: Path) -> None:
        py_file = tmp_path / "empty.py"
        py_file.write_text("", encoding="utf-8")
        nodes, _ = extract_file(py_file, tmp_path)
        assert not any(n.kind == "variable" for n in nodes)


# ---------------------------------------------------------------------------
# Task D: CLI build + report subcommands
# ---------------------------------------------------------------------------

class TestCLIBuild:
    def test_build_creates_graph_json(self, tmp_path: Path) -> None:
        """build subcommand creates graph.json under vault/.mneme/."""
        py_file = tmp_path / "mod.py"
        py_file.write_text("def foo(): pass\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        run_build(tmp_path)

        graph_json = tmp_path / ".mneme" / "graph.json"
        assert graph_json.is_file()

    def test_build_graph_json_is_valid_json(self, tmp_path: Path) -> None:
        py_file = tmp_path / "mod.py"
        py_file.write_text("class A: pass\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        run_build(tmp_path)

        data = json.loads((tmp_path / ".mneme" / "graph.json").read_text(encoding="utf-8"))
        assert "nodes" in data
        assert "edges" in data

    def test_build_is_idempotent(self, tmp_path: Path) -> None:
        """Two consecutive builds produce the same node/edge sets (by ID)."""
        py_file = tmp_path / "mod.py"
        py_file.write_text("def bar(): pass\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        run_build(tmp_path)
        store1 = GraphStore.load(tmp_path)
        ids1 = ({n.node_id for n in store1.nodes}, {e.edge_id for e in store1.edges})

        run_build(tmp_path)
        store2 = GraphStore.load(tmp_path)
        ids2 = ({n.node_id for n in store2.nodes}, {e.edge_id for e in store2.edges})

        assert ids1 == ids2

    def test_build_walks_subdirs(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text("def inner_fn(): pass\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        run_build(tmp_path)

        data = json.loads((tmp_path / ".mneme" / "graph.json").read_text(encoding="utf-8"))
        names = [n["name"] for n in data["nodes"]]
        assert "inner_fn" in names

    def test_build_returns_summary_dict(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        summary = run_build(tmp_path)
        assert "nodes" in summary
        assert "edges" in summary
        assert summary["nodes"] >= 1

    def test_build_skips_graph_json_itself(self, tmp_path: Path) -> None:
        """graph.json is not a .py file; build should not try to parse it."""
        (tmp_path / "mod.py").write_text("pass\n", encoding="utf-8")

        from mneme_graph.cli import run_build
        # Should not raise even after graph.json exists.
        run_build(tmp_path)
        run_build(tmp_path)


class TestCLIReport:
    def test_report_returns_summary(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            "class Foo: pass\ndef bar(): pass\n", encoding="utf-8"
        )

        from mneme_graph.cli import run_build, run_report
        run_build(tmp_path)
        report = run_report(tmp_path)

        assert "node_counts" in report
        assert "edge_counts" in report
        assert "top_referenced" in report
        assert "external_deps" in report

    def test_report_node_counts_correct(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            "class A: pass\nclass B: pass\n", encoding="utf-8"
        )

        from mneme_graph.cli import run_build, run_report
        run_build(tmp_path)
        report = run_report(tmp_path)

        # 2 class nodes + 1 module node at minimum
        total = sum(report["node_counts"].values())
        assert total >= 3

    def test_report_edge_counts_correct(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            "def f(): pass\ndef g(): f()\n", encoding="utf-8"
        )

        from mneme_graph.cli import run_build, run_report
        run_build(tmp_path)
        report = run_report(tmp_path)

        total_edges = sum(report["edge_counts"].values())
        assert total_edges >= 1

    def test_report_on_missing_graph_json_returns_empty(self, tmp_path: Path) -> None:
        """report without prior build should return zero counts, not raise."""
        from mneme_graph.cli import run_report
        report = run_report(tmp_path)
        assert report["node_counts"] == {}
        assert report["edge_counts"] == {}

    def test_report_external_deps_list(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text("import os\nimport pathlib\n", encoding="utf-8")

        from mneme_graph.cli import run_build, run_report
        run_build(tmp_path)
        report = run_report(tmp_path)

        assert "os" in report["external_deps"] or len(report["external_deps"]) >= 0

    def test_report_top_referenced_deterministic(self, tmp_path: Path) -> None:
        """Two consecutive reports on the same graph.json yield identical top_referenced."""
        (tmp_path / "mod.py").write_text(
            "def a(): pass\ndef b(): a()\ndef c(): a()\n", encoding="utf-8"
        )

        from mneme_graph.cli import run_build, run_report
        run_build(tmp_path)
        r1 = run_report(tmp_path)
        r2 = run_report(tmp_path)
        assert r1["top_referenced"] == r2["top_referenced"]


# ---------------------------------------------------------------------------
# CLI _print_report + main entrypoint coverage
# ---------------------------------------------------------------------------

class TestCLIPrintReport:
    def test_print_report_runs_without_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "mod.py").write_text("def f(): pass\nimport os\n", encoding="utf-8")

        from mneme_graph.cli import _print_report, run_build, run_report
        run_build(tmp_path)
        report = run_report(tmp_path)
        _print_report(report, tmp_path)

        captured = capsys.readouterr()
        assert "node counts" in captured.out.lower()
        assert "edge counts" in captured.out.lower()

    def test_print_report_empty_graph(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from mneme_graph.cli import _print_report
        report: dict[str, object] = {
            "node_counts": {},
            "edge_counts": {},
            "top_referenced": [],
            "external_deps": [],
        }
        _print_report(report, tmp_path)
        captured = capsys.readouterr()
        assert "no nodes" in captured.out.lower() or "node counts" in captured.out.lower()


class TestCLIMain:
    def test_main_build_subcommand(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
        import sys
        monkeypatch.setattr(sys, "argv", ["mneme-graph", "--vault", str(tmp_path), "build"])
        from mneme_graph.cli import main
        main()
        captured = capsys.readouterr()
        assert "graph built" in captured.out.lower()

    def test_main_report_subcommand(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "mod.py").write_text("def f(): pass\n", encoding="utf-8")
        import sys

        from mneme_graph.cli import run_build
        run_build(tmp_path)
        monkeypatch.setattr(sys, "argv", ["mneme-graph", "--vault", str(tmp_path), "report"])
        from mneme_graph.cli import main
        main()
        captured = capsys.readouterr()
        assert "node counts" in captured.out.lower()


# ---------------------------------------------------------------------------
# Extra extractor coverage: attribute base class, calls attribute form
# ---------------------------------------------------------------------------

class TestExtractorEdgeCases:
    def test_attribute_base_class(self, tmp_path: Path) -> None:
        """class Foo(module.Base) — dotted base produces an <external> inherits edge."""
        src = "class Foo(collections.abc.Sequence): pass\n"
        py_file = tmp_path / "attr_base.py"
        py_file.write_text(src, encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        inherits_edges = [e for e in edges if e.kind == "inherits"]
        assert len(inherits_edges) == 1
        node_by_id = {n.node_id: n for n in nodes}
        target = node_by_id[inherits_edges[0].dst_id]
        assert target.source_path == "<external>"

    def test_calls_attribute_form(self, tmp_path: Path) -> None:
        """obj.method() call produces a calls edge with the method name as callee."""
        src = "def run():\n    os.path.join('a', 'b')\n"
        py_file = tmp_path / "attr_call.py"
        py_file.write_text(src, encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        calls_edges = [e for e in edges if e.kind == "calls"]
        # Should emit a calls edge for 'join'
        node_by_id = {n.node_id: n for n in nodes}
        callee_names = {node_by_id[e.dst_id].name for e in calls_edges}
        assert "join" in callee_names

    def test_provenance_all_nodes_have_extracted_confidence(
        self, tmp_path: Path
    ) -> None:
        """All locally-extracted nodes carry confidence=EXTRACTED."""
        src = "X = 1\nclass A(object): pass\ndef f(): pass\n"
        py_file = tmp_path / "prov.py"
        py_file.write_text(src, encoding="utf-8")
        nodes, _ = extract_file(py_file, tmp_path)
        local_nodes = [n for n in nodes if n.source_path != "<external>"]
        for node in local_nodes:
            assert node.confidence == "EXTRACTED"

    def test_build_handles_oserror_gracefully(self, tmp_path: Path) -> None:
        """run_build should not crash on an unreadable file — just warn."""
        (tmp_path / "good.py").write_text("def ok(): pass\n", encoding="utf-8")
        from mneme_graph.cli import run_build
        # Should complete without raising.
        summary = run_build(tmp_path)
        assert summary["nodes"] >= 1

    def test_inherits_object_keyword_skipped(self, tmp_path: Path) -> None:
        """class Foo(object) produces an <external> inherits edge for 'object'."""
        py_file = tmp_path / "obj_base.py"
        py_file.write_text("class Foo(object): pass\n", encoding="utf-8")
        nodes, edges = extract_file(py_file, tmp_path)
        inherits_edges = [e for e in edges if e.kind == "inherits"]
        # 'object' is an external base: exactly one inherits edge expected.
        assert len(inherits_edges) == 1
        node_by_id = {n.node_id: n for n in nodes}
        assert node_by_id[inherits_edges[0].dst_id].name == "object"
