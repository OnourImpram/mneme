"""Tests for mneme_graph.completeness — honest recall metric for graph coverage.

Self-contained: builds tiny vault fixtures in tmp_path with KNOWN counts so
every assertion is against a deterministic expected value.

Ground-truth derivation for the 3-file vault:
    NoteA.md — 2 wikilinks ([[NoteB]], [[NoteC]]), 2 body tags (#türkçe, #research),
               1 embed (![[EmbedMe]])
    NoteB.md — 1 wikilink ([[NoteA]]), 1 body tag (#neuroscience),
               fenced block with [[NotALink]] / #notatag → IGNORED,
               inline spans `[[NotALink2]]` / `#notatag2` → IGNORED
    NoteC.md — 0 wikilinks, 1 frontmatter tag (project), 0 embeds;
               body has #00695C (#FFF, #123) — hex-color / numeric → NOT tags

    wikilinks : 2 + 1 + 0 = 3
    tags      : 2 + 1 + 1 = 4   (frontmatter tag counts; hex/numeric do NOT)
    embeds    : 1 + 0 + 0 = 1
    total     : 8
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mneme_graph.completeness import (
    completeness,
    count_source_relations,
    graph_capture,
    load_graph,
)

# ---------------------------------------------------------------------------
# Vault fixture content
# ---------------------------------------------------------------------------

_FILE_A = """\
This note links to [[NoteB]] and [[NoteC]].
Tags: #türkçe and #research.
Embed: ![[EmbedMe]].
"""

_FILE_B = """\
Back-link to [[NoteA]].
Topic: #neuroscience.
```
[[NotALink]] inside fenced code — must be ignored.
#notatag inside fence — must be ignored.
```
Also `[[NotALink2]]` in inline code — ignored.
And `#notatag2` in inline span — ignored.
"""

_FILE_C = """\
---
tags: [project]
---
Color code is #00695C — not a tag.
Also #FFF and #123 are not tags.
"""

_GT_WIKILINKS = 3
_GT_TAGS = 4
_GT_EMBEDS = 1
_GT_TOTAL = 8


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Three-file vault with precisely known semantic relation counts."""
    (tmp_path / "NoteA.md").write_text(_FILE_A, encoding="utf-8")
    (tmp_path / "NoteB.md").write_text(_FILE_B, encoding="utf-8")
    (tmp_path / "NoteC.md").write_text(_FILE_C, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Minimal mock graph for recall assertions
# ---------------------------------------------------------------------------


@dataclass
class _MockEdge:
    """Minimal edge duck-type — only .kind is read by graph_capture."""

    kind: str


class _MockGraph:
    """Minimal graph duck-type — only .edges is read by graph_capture."""

    def __init__(self, edges: list[_MockEdge]) -> None:
        self.edges = edges

    @classmethod
    def full(cls) -> _MockGraph:
        """Graph capturing ALL 8 ground-truth relations → recall 1.0."""
        return cls(
            [_MockEdge("links_to")] * _GT_WIKILINKS
            + [_MockEdge("tagged")] * _GT_TAGS
            + [_MockEdge("embeds")] * _GT_EMBEDS
        )

    @classmethod
    def half(cls) -> _MockGraph:
        """Graph capturing exactly 4 of 8 relations → recall 0.5."""
        return cls(
            # 2 of 3 wikilinks, 1 of 4 tags, 1 of 1 embeds = 4 total
            [_MockEdge("links_to"), _MockEdge("links_to")]
            + [_MockEdge("tagged")]
            + [_MockEdge("embeds")]
        )

    @classmethod
    def empty(cls) -> _MockGraph:
        """Graph with no captured relations → recall 0.0."""
        return cls([])


# ---------------------------------------------------------------------------
# count_source_relations
# ---------------------------------------------------------------------------


class TestCountSourceRelations:
    def test_wikilink_count(self, vault: Path) -> None:
        result = count_source_relations(vault)
        assert result["wikilinks"] == _GT_WIKILINKS

    def test_tag_count(self, vault: Path) -> None:
        # Includes 1 frontmatter tag (project) and 3 body tags.
        # Excludes #00695C, #FFF, #123 (hex-color / numeric).
        result = count_source_relations(vault)
        assert result["tags"] == _GT_TAGS

    def test_embed_count(self, vault: Path) -> None:
        result = count_source_relations(vault)
        assert result["embeds"] == _GT_EMBEDS

    def test_total_equals_sum(self, vault: Path) -> None:
        result = count_source_relations(vault)
        assert result["total"] == result["wikilinks"] + result["tags"] + result["embeds"]
        assert result["total"] == _GT_TOTAL

    def test_fenced_code_content_ignored(self, tmp_path: Path) -> None:
        """[[NotALink]] and #notatag inside a fenced block must not be counted."""
        (tmp_path / "fence.md").write_text(
            "```\n[[NotALink]]\n#notatag\n```\nReal: #realtag and [[RealLink]].\n",
            encoding="utf-8",
        )
        result = count_source_relations(tmp_path)
        assert result["wikilinks"] == 1
        assert result["tags"] == 1
        assert result["embeds"] == 0

    def test_inline_code_content_ignored(self, tmp_path: Path) -> None:
        """`[[NotALink]]` and `#notatag` inside backtick spans must not be counted."""
        (tmp_path / "inline.md").write_text(
            "Use `[[NotALink]]` and `#notatag` here.  Real: [[RealLink]] #realtag.\n",
            encoding="utf-8",
        )
        result = count_source_relations(tmp_path)
        assert result["wikilinks"] == 1
        assert result["tags"] == 1

    def test_turkish_diacritic_tags_counted(self, tmp_path: Path) -> None:
        """Tags with Turkish diacritics must be accepted by the H1.1 validity gate."""
        (tmp_path / "tr.md").write_text(
            "#türkçe #ruh-sağlığı text\n",
            encoding="utf-8",
        )
        result = count_source_relations(tmp_path)
        assert result["tags"] == 2, "Turkish diacritic tags must be counted"

    def test_hex_color_tokens_not_counted_as_tags(self, tmp_path: Path) -> None:
        """#00695C (6-digit hex), #FFF (3-digit), #123 (3-digit hex / numeric)
        must be rejected by the H1.1 gate."""
        (tmp_path / "hex.md").write_text(
            "Color #00695C and #FFF and #123 — none are tags.\n",
            encoding="utf-8",
        )
        result = count_source_relations(tmp_path)
        assert result["tags"] == 0

    def test_asset_embeds_not_counted(self, tmp_path: Path) -> None:
        """![[photo.png]] and ![[diagram.svg]] must not count as embeds."""
        (tmp_path / "assets.md").write_text(
            "![[photo.png]] and ![[diagram.svg]]\n",
            encoding="utf-8",
        )
        result = count_source_relations(tmp_path)
        assert result["embeds"] == 0

    def test_deterministic(self, vault: Path) -> None:
        r1 = count_source_relations(vault)
        r2 = count_source_relations(vault)
        assert r1 == r2

    def test_empty_vault_all_zero(self, tmp_path: Path) -> None:
        result = count_source_relations(tmp_path)
        assert result == {"wikilinks": 0, "tags": 0, "embeds": 0, "total": 0}


# ---------------------------------------------------------------------------
# graph_capture
# ---------------------------------------------------------------------------


class TestGraphCapture:
    def test_full_capture_counts(self) -> None:
        cap = graph_capture(_MockGraph.full())
        assert cap["links_to"] == _GT_WIKILINKS
        assert cap["tagged"] == _GT_TAGS
        assert cap["embeds"] == _GT_EMBEDS
        assert cap["total"] == _GT_TOTAL

    def test_empty_graph_all_zero(self) -> None:
        cap = graph_capture(_MockGraph.empty())
        assert cap == {"links_to": 0, "tagged": 0, "embeds": 0, "total": 0}

    def test_non_semantic_edges_ignored(self) -> None:
        """has_heading, calls, imports must not appear in captured counts."""
        g = _MockGraph(
            [
                _MockEdge("has_heading"),
                _MockEdge("calls"),
                _MockEdge("imports"),
                _MockEdge("links_to"),
            ]
        )
        cap = graph_capture(g)
        assert cap["total"] == 1
        assert cap["links_to"] == 1
        assert cap["tagged"] == 0
        assert cap["embeds"] == 0

    def test_total_equals_sum_of_kinds(self) -> None:
        cap = graph_capture(_MockGraph.full())
        assert cap["total"] == cap["links_to"] + cap["tagged"] + cap["embeds"]


# ---------------------------------------------------------------------------
# completeness (recall)
# ---------------------------------------------------------------------------


class TestCompleteness:
    def test_full_capture_recall_is_one(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.full())
        assert result["recall"] == 1.0

    def test_half_capture_recall_is_point_five(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.half())
        assert result["recall"] == pytest.approx(0.5, abs=1e-9)

    def test_empty_capture_recall_is_zero(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.empty())
        assert result["recall"] == 0.0

    def test_recall_clamped_to_one(self, vault: Path) -> None:
        """A graph with MORE edges than the ground truth must not exceed recall 1.0."""
        over = _MockGraph(
            [_MockEdge("links_to")] * (_GT_WIKILINKS + 100)
            + [_MockEdge("tagged")] * (_GT_TAGS + 100)
            + [_MockEdge("embeds")] * (_GT_EMBEDS + 100)
        )
        result = completeness(vault, over)
        assert result["recall"] <= 1.0

    def test_ground_truth_structure(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.full())
        gt = result["ground_truth"]
        assert isinstance(gt, dict)
        assert set(gt.keys()) == {"wikilinks", "tags", "embeds", "total"}

    def test_captured_structure(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.full())
        cap = result["captured"]
        assert isinstance(cap, dict)
        assert set(cap.keys()) == {"links_to", "tagged", "embeds", "total"}

    def test_recall_by_kind_structure(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.full())
        rbk = result["recall_by_kind"]
        assert isinstance(rbk, dict)
        assert set(rbk.keys()) == {"links_to", "tagged", "embeds"}

    def test_recall_by_kind_full_capture(self, vault: Path) -> None:
        """All per-kind recalls must be 1.0 when the graph captures everything."""
        result = completeness(vault, _MockGraph.full())
        rbk = result["recall_by_kind"]
        assert isinstance(rbk, dict)
        for kind, val in rbk.items():
            assert val == pytest.approx(1.0), f"recall_by_kind[{kind!r}] expected 1.0"

    def test_ground_truth_matches_count_source_relations(self, vault: Path) -> None:
        result = completeness(vault, _MockGraph.empty())
        assert result["ground_truth"] == count_source_relations(vault)

    def test_deterministic(self, vault: Path) -> None:
        r1 = completeness(vault, _MockGraph.full())
        r2 = completeness(vault, _MockGraph.full())
        assert r1["recall"] == r2["recall"]


# ---------------------------------------------------------------------------
# load_graph helper
# ---------------------------------------------------------------------------


class TestLoadGraph:
    def test_returns_graph_store_for_empty_vault(self, tmp_path: Path) -> None:
        from mneme_graph.store import GraphStore

        store = load_graph(tmp_path)
        assert isinstance(store, GraphStore)

    def test_empty_vault_has_no_nodes_or_edges(self, tmp_path: Path) -> None:
        store = load_graph(tmp_path)
        assert store.nodes == []
        assert store.edges == []

    def test_loaded_graph_usable_with_graph_capture(self, tmp_path: Path) -> None:
        """graph_capture must accept the GraphStore returned by load_graph."""
        store = load_graph(tmp_path)
        cap = graph_capture(store)
        assert cap["total"] == 0
