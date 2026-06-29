"""Tests for mneme_graph.report -- TASK B4 / R6 (browsable GRAPH_REPORT.md).

Self-contained: all fixtures build an in-memory graph using GraphNode.make /
GraphEdge.make directly.  No B1/B2 behaviour, no analytics import.
"""

from __future__ import annotations

from pathlib import Path

from mneme_graph.report import generate_report, write_report
from mneme_graph.schema import GraphEdge, GraphNode
from mneme_graph.store import GraphStore

# ---------------------------------------------------------------------------
# Helpers / shared fixture
# ---------------------------------------------------------------------------


def _make_store() -> GraphStore:
    """Build a small, deterministic in-memory graph for assertions.

    Graph layout
    ------------
    Nodes (6):
        note    notes/alpha.md   (in-degree 2 from beta->alpha + heading->alpha)
        note    notes/beta.md    (in-degree 1 from alpha->beta)
        tag     ai
        tag     python           (in-degree 1 from alpha->python)
        heading Introduction     (in-degree 0)
        module  <external>       (noise-free; external module)

    Edges (4):
        beta   --links_to-->   alpha
        heading--has_heading-->alpha
        alpha  --links_to-->   beta
        alpha  --tagged-->     python
    """
    # GraphStore with an arbitrary root -- no disk access in these tests.
    store = GraphStore(Path("."))

    alpha = GraphNode.make("note", "notes/alpha.md", "notes/alpha.md", "hash_a", 0, 10)
    beta = GraphNode.make("note", "notes/beta.md", "notes/beta.md", "hash_b", 0, 5)
    tag_ai = GraphNode.make("tag", "ai", "<tags>", "hash_t1", 0, 0)
    tag_py = GraphNode.make("tag", "python", "<tags>", "hash_t2", 0, 0)
    heading = GraphNode.make("heading", "Introduction", "notes/alpha.md", "hash_h", 1, 1)
    ext_mod = GraphNode.make("module", "external_lib", "<external>", "hash_e", 0, 0)

    store.add_nodes([alpha, beta, tag_ai, tag_py, heading, ext_mod])

    edges = [
        GraphEdge.make(beta.node_id, alpha.node_id, "links_to"),      # alpha in-deg +1
        GraphEdge.make(heading.node_id, alpha.node_id, "has_heading"), # alpha in-deg +1
        GraphEdge.make(alpha.node_id, beta.node_id, "links_to"),       # beta  in-deg +1
        GraphEdge.make(alpha.node_id, tag_py.node_id, "tagged"),       # python in-deg +1
    ]
    store.add_edges(edges)
    return store


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_generate_report_returns_string() -> None:
    result = generate_report(_make_store())
    assert isinstance(result, str)
    assert len(result) > 0


def test_section_headers_present() -> None:
    result = generate_report(_make_store())
    for header in (
        "# GRAPH_REPORT",
        "## Summary",
        "## Node Counts by Kind",
        "## Edge Counts by Kind",
        "## Top-Referenced Notes",
        "## Tags",
        "## Noise Summary",
        "## Determinism",
    ):
        assert header in result, f"missing section: {header!r}"


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_total_counts_correct() -> None:
    result = generate_report(_make_store())
    assert "| Total nodes | 6 |" in result
    assert "| Total edges | 4 |" in result


def test_node_kind_counts() -> None:
    result = generate_report(_make_store())
    assert "| heading | 1 |" in result
    assert "| module | 1 |" in result
    assert "| note | 2 |" in result
    assert "| tag | 2 |" in result


def test_edge_kind_counts() -> None:
    result = generate_report(_make_store())
    assert "| has_heading | 1 |" in result
    assert "| links_to | 2 |" in result
    assert "| tagged | 1 |" in result


# ---------------------------------------------------------------------------
# Top-referenced notes
# ---------------------------------------------------------------------------


def test_top_notes_paths_present() -> None:
    result = generate_report(_make_store())
    assert "notes/alpha.md" in result
    assert "notes/beta.md" in result


def test_top_notes_order_by_in_degree() -> None:
    """notes/alpha.md (in-degree 2) must appear before notes/beta.md (in-degree 1)."""
    result = generate_report(_make_store())
    lines = result.splitlines()
    alpha_idx = next(i for i, ln in enumerate(lines) if "notes/alpha.md" in ln)
    beta_idx = next(i for i, ln in enumerate(lines) if "notes/beta.md" in ln)
    assert alpha_idx < beta_idx, "alpha (higher in-degree) should appear before beta"


def test_top_notes_in_degree_values() -> None:
    result = generate_report(_make_store())
    # alpha has in-degree 2
    alpha_line = next(ln for ln in result.splitlines() if "notes/alpha.md" in ln)
    assert "| 2 |" in alpha_line
    # beta has in-degree 1
    beta_line = next(ln for ln in result.splitlines() if "notes/beta.md" in ln)
    assert "| 1 |" in beta_line


def test_external_nodes_not_in_top_notes() -> None:
    """External module nodes should never appear in the top-notes table."""
    result = generate_report(_make_store())
    # Find the top-notes section block
    start = result.index("## Top-Referenced Notes")
    end = result.index("## Tags")
    section = result[start:end]
    assert "external_lib" not in section
    assert "<external>" not in section


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_tags_listed_alphabetically() -> None:
    result = generate_report(_make_store())
    lines = result.splitlines()
    ai_idx = next(i for i, ln in enumerate(lines) if "- `ai`" in ln)
    py_idx = next(i for i, ln in enumerate(lines) if "- `python`" in ln)
    assert ai_idx < py_idx, "tags must appear alphabetically (ai before python)"


def test_tag_names_in_output() -> None:
    result = generate_report(_make_store())
    assert "- `ai`" in result
    assert "- `python`" in result


# ---------------------------------------------------------------------------
# Noise summary
# ---------------------------------------------------------------------------


def test_noise_summary_lists_headings() -> None:
    result = generate_report(_make_store())
    start = result.index("## Noise Summary")
    end = result.index("## Determinism")
    section = result[start:end]
    assert "heading" in section
    assert "| 1 |" in section


def test_no_noise_when_graph_has_no_headings() -> None:
    store = GraphStore(Path("."))
    store.add_nodes([
        GraphNode.make("note", "notes/x.md", "notes/x.md", "hx", 0, 0),
    ])
    result = generate_report(store)
    assert "_No noise nodes detected._" in result


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism_same_store() -> None:
    store = _make_store()
    assert generate_report(store) == generate_report(store)


def test_determinism_independent_stores() -> None:
    """Two independently-built stores with identical data must produce identical output."""
    assert generate_report(_make_store()) == generate_report(_make_store())


# ---------------------------------------------------------------------------
# Empty graph
# ---------------------------------------------------------------------------


def test_empty_graph_all_sections_present() -> None:
    store = GraphStore(Path("."))
    result = generate_report(store)
    for header in (
        "# GRAPH_REPORT",
        "## Summary",
        "## Node Counts by Kind",
        "## Edge Counts by Kind",
        "## Top-Referenced Notes",
        "## Tags",
        "## Noise Summary",
        "## Determinism",
    ):
        assert header in result, f"empty-graph report missing: {header!r}"


def test_empty_graph_zero_counts() -> None:
    result = generate_report(GraphStore(Path(".")))
    assert "| Total nodes | 0 |" in result
    assert "| Total edges | 0 |" in result


def test_empty_graph_placeholder_messages() -> None:
    result = generate_report(GraphStore(Path(".")))
    assert "_No nodes found._" in result
    assert "_No edges found._" in result
    assert "_No note nodes found" in result
    assert "_No tags found._" in result
    assert "_No noise nodes detected._" in result


# ---------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------


def test_write_report_creates_nonempty_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    disk_store = GraphStore(vault)
    src = _make_store()
    disk_store.add_nodes(src.nodes)
    disk_store.add_edges(src.edges)
    disk_store.save()

    out = tmp_path / "GRAPH_REPORT.md"
    write_report(vault, out)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert len(content) > 0


def test_write_report_content_matches_generate(tmp_path: Path) -> None:
    """write_report must produce the same text as generate_report for the same data."""
    vault = tmp_path / "vault"
    vault.mkdir()
    disk_store = GraphStore(vault)
    src = _make_store()
    disk_store.add_nodes(src.nodes)
    disk_store.add_edges(src.edges)
    disk_store.save()

    out = tmp_path / "GRAPH_REPORT.md"
    write_report(vault, out)

    expected = generate_report(disk_store)
    actual = out.read_text(encoding="utf-8")
    assert actual == expected


def test_write_report_has_graph_report_header(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    disk_store = GraphStore(vault)
    disk_store.save()

    out = tmp_path / "GRAPH_REPORT.md"
    write_report(vault, out)

    assert "# GRAPH_REPORT" in out.read_text(encoding="utf-8")


def test_write_report_missing_graph_json_writes_empty_graph_report(tmp_path: Path) -> None:
    """write_report on a vault with no graph.json must still write a valid report."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Do NOT call save() -- graph.json is absent.

    out = tmp_path / "GRAPH_REPORT.md"
    write_report(vault, out)

    content = out.read_text(encoding="utf-8")
    assert "# GRAPH_REPORT" in content
    assert "| Total nodes | 0 |" in content
