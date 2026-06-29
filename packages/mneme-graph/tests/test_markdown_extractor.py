"""Tests for the deterministic Markdown extractor (markdown_extractor.py).

Mirrors the style of test_python_extractor.py: tmp_path fixtures, class-based
test groups, assertions on exact node kinds/names and edge kinds.
"""

from __future__ import annotations

from pathlib import Path

from mneme_graph.extractor.markdown_extractor import extract_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note(nodes: list) -> object:  # type: ignore[type-arg]
    """Return the single note node, or raise."""
    notes = [n for n in nodes if n.kind == "note" and n.source_path != "<external>"]
    assert len(notes) == 1, f"Expected 1 local note node, got {len(notes)}"
    return notes[0]


# ---------------------------------------------------------------------------
# Note node basics
# ---------------------------------------------------------------------------


class TestNoteNode:
    def test_note_node_emitted(self, tmp_path: Path) -> None:
        f = tmp_path / "MyNote.md"
        f.write_text("Hello world\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note_nodes = [n for n in nodes if n.kind == "note" and n.source_path != "<external>"]
        assert len(note_nodes) == 1

    def test_note_name_is_stem(self, tmp_path: Path) -> None:
        f = tmp_path / "ProjectAlpha.md"
        f.write_text("# A different title\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note = _note(nodes)
        assert note.name == "ProjectAlpha"  # type: ignore[union-attr]

    def test_note_source_path_is_vault_relative_posix(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        f = sub / "alpha.md"
        f.write_text("text\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note = _note(nodes)
        assert note.source_path == "notes/alpha.md"  # type: ignore[union-attr]
        assert "\\" not in note.source_path  # type: ignore[union-attr]

    def test_note_has_content_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "x.md"
        f.write_text("content\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note = _note(nodes)
        assert len(note.content_hash) == 64  # type: ignore[union-attr]
        assert all(c in "0123456789abcdef" for c in note.content_hash)  # type: ignore[union-attr]

    def test_empty_file_yields_note_no_edges(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        assert any(n.kind == "note" for n in nodes)
        assert edges == []

    def test_note_line_range(self, tmp_path: Path) -> None:
        f = tmp_path / "n.md"
        f.write_text("line0\nline1\nline2\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note = _note(nodes)
        assert note.line_start == 0  # type: ignore[union-attr]
        assert note.line_end == 2  # 3 lines → last index 2 (type: ignore[union-attr])


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


HEADING_MD = """\
# Main Title
Some text here.
## Sub Heading
More text.
### Deep
"""


class TestHeadingNodes:
    def test_heading_nodes_emitted(self, tmp_path: Path) -> None:
        f = tmp_path / "h.md"
        f.write_text(HEADING_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        heading_names = {n.name for n in nodes if n.kind == "heading"}
        assert "Main Title" in heading_names
        assert "Sub Heading" in heading_names
        assert "Deep" in heading_names

    def test_heading_count(self, tmp_path: Path) -> None:
        f = tmp_path / "h.md"
        f.write_text(HEADING_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        assert len([n for n in nodes if n.kind == "heading"]) == 3

    def test_has_heading_edges_from_note(self, tmp_path: Path) -> None:
        f = tmp_path / "h.md"
        f.write_text(HEADING_MD, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        note = _note(nodes)
        hh_edges = [e for e in edges if e.kind == "has_heading" and e.src_id == note.node_id]  # type: ignore[union-attr]
        assert len(hh_edges) == 3

    def test_heading_source_path_matches_note(self, tmp_path: Path) -> None:
        f = tmp_path / "h.md"
        f.write_text(HEADING_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        note = _note(nodes)
        headings = [n for n in nodes if n.kind == "heading"]
        for h in headings:
            assert h.source_path == note.source_path  # type: ignore[union-attr]

    def test_heading_line_start(self, tmp_path: Path) -> None:
        f = tmp_path / "h.md"
        f.write_text(HEADING_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        main = next(n for n in nodes if n.kind == "heading" and n.name == "Main Title")
        assert main.line_start == 0  # first line


# ---------------------------------------------------------------------------
# Tags — body text
# ---------------------------------------------------------------------------


TAG_BODY_MD = """\
This note is about #python and #machine-learning.
Also relevant: #data_science topics.
"""


class TestBodyTags:
    def test_body_tags_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "tags.md"
        f.write_text(TAG_BODY_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "python" in tag_names
        assert "machine-learning" in tag_names
        assert "data_science" in tag_names

    def test_tag_nodes_have_external_source_path(self, tmp_path: Path) -> None:
        f = tmp_path / "tags.md"
        f.write_text(TAG_BODY_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        for n in nodes:
            if n.kind == "tag":
                assert n.source_path == "<external>"
                assert n.content_hash == ""

    def test_tagged_edges_from_note(self, tmp_path: Path) -> None:
        f = tmp_path / "tags.md"
        f.write_text(TAG_BODY_MD, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        note = _note(nodes)
        tagged_edges = [e for e in edges if e.kind == "tagged" and e.src_id == note.node_id]  # type: ignore[union-attr]
        assert len(tagged_edges) == 3

    def test_heading_hash_not_treated_as_tag(self, tmp_path: Path) -> None:
        f = tmp_path / "headtag.md"
        f.write_text("# Title\nSome content.\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        # "Title" must not appear as a tag
        assert "Title" not in tag_names

    def test_tag_inside_inline_code_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "inlinecode.md"
        f.write_text("Use `#notag` here.\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "notag" not in tag_names


# ---------------------------------------------------------------------------
# Tags — YAML frontmatter
# ---------------------------------------------------------------------------


FM_MD = """\
---
tags: [python, obsidian, machine-learning]
---
Body text here.
"""

FM_LIST_MD = """\
---
tags:
  - research
  - neuroscience
---
Some content.
"""


class TestFrontmatterTags:
    def test_flow_sequence_tags_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "fm.md"
        f.write_text(FM_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "python" in tag_names
        assert "obsidian" in tag_names
        assert "machine-learning" in tag_names

    def test_block_list_tags_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "fml.md"
        f.write_text(FM_LIST_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "research" in tag_names
        assert "neuroscience" in tag_names

    def test_frontmatter_body_not_emitted_as_headings(self, tmp_path: Path) -> None:
        f = tmp_path / "fm.md"
        f.write_text(FM_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        # No heading nodes — no ATX headings in the body
        assert not any(n.kind == "heading" for n in nodes)


# ---------------------------------------------------------------------------
# Tag deduplication across notes
# ---------------------------------------------------------------------------


class TestTagDeduplication:
    def test_same_tag_yields_same_node_id_from_two_notes(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.md"
        f2 = tmp_path / "b.md"
        f1.write_text("Text with #x here.\n", encoding="utf-8")
        f2.write_text("Also #x in this note.\n", encoding="utf-8")

        nodes1, _ = extract_file(f1, tmp_path)
        nodes2, _ = extract_file(f2, tmp_path)

        tag1 = next(n for n in nodes1 if n.kind == "tag" and n.name == "x")
        tag2 = next(n for n in nodes2 if n.kind == "tag" and n.name == "x")

        assert tag1.node_id == tag2.node_id, (
            "The same tag #x from two different notes must produce identical node_ids "
            "so GraphStore deduplication collapses them to a single node."
        )


# ---------------------------------------------------------------------------
# Wikilinks
# ---------------------------------------------------------------------------


WIKI_MD = """\
See [[Other]] for details.
Also check [[Folder/Note|Display Name]] and [[Target#heading|alias]].
"""


class TestWikilinks:
    def test_wikilink_ghost_note_emitted(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("See [[Other]] here.\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.kind == "note" and n.source_path == "<external>"}
        assert "Other" in ghost_names

    def test_wikilink_alias_stripped(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("See [[Target|Display Text]] here.\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.kind == "note" and n.source_path == "<external>"}
        assert "Target" in ghost_names
        assert "Display Text" not in ghost_names

    def test_wikilink_heading_stripped(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("See [[Target#section]] here.\n", encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.kind == "note" and n.source_path == "<external>"}
        assert "Target" in ghost_names
        assert "section" not in ghost_names

    def test_wikilink_ghost_has_links_to_edge(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("See [[Other]] here.\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        link_edges = [e for e in edges if e.kind == "links_to"]
        assert len(link_edges) == 1

    def test_wikilink_all_forms(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text(WIKI_MD, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        link_edges = [e for e in edges if e.kind == "links_to"]
        ghost_names = {n.name for n in nodes if n.kind == "note" and n.source_path == "<external>"}
        assert "Other" in ghost_names
        assert "Note" in ghost_names       # Folder/Note → stem "Note"
        assert "Target" in ghost_names
        assert len(link_edges) == 3

    def test_image_wikilink_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("![[photo.png]] and ![[diagram.svg]]\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        embed_edges = [e for e in edges if e.kind == "embeds"]
        assert embed_edges == []
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "photo" not in ghost_names
        assert "diagram" not in ghost_names


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------


class TestEmbeds:
    def test_embed_note_ghost_emitted(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("![[EmbeddedNote]]\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        embed_edges = [e for e in edges if e.kind == "embeds"]
        assert len(embed_edges) == 1
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "EmbeddedNote" in ghost_names

    def test_embed_uses_embeds_kind_not_links_to(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("![[EmbeddedNote]]\n", encoding="utf-8")
        _, edges = extract_file(f, tmp_path)
        assert not any(e.kind == "links_to" for e in edges)
        assert any(e.kind == "embeds" for e in edges)


# ---------------------------------------------------------------------------
# Markdown links
# ---------------------------------------------------------------------------


class TestMarkdownLinks:
    def test_md_link_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("[click here](Other.md)\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        link_edges = [e for e in edges if e.kind == "links_to"]
        assert len(link_edges) == 1
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "Other" in ghost_names

    def test_http_link_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("[external](https://example.com)\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        assert not any(e.kind == "links_to" for e in edges)

    def test_image_md_link_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "src.md"
        f.write_text("![alt](image.png)\n", encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        assert not any(e.kind == "links_to" for e in edges)


# ---------------------------------------------------------------------------
# Fenced code block: tags and headings inside are ignored
# ---------------------------------------------------------------------------


FENCE_MD = """\
Normal text with #realtag here.
```
# this is code not a heading
#faketag
```
After fence: #aftertag
~~~
## also code
#alsofake
~~~
End.
"""


class TestFencedCodeBlock:
    def test_real_tag_found(self, tmp_path: Path) -> None:
        f = tmp_path / "fence.md"
        f.write_text(FENCE_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "realtag" in tag_names
        assert "aftertag" in tag_names

    def test_tags_inside_fence_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "fence.md"
        f.write_text(FENCE_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "faketag" not in tag_names
        assert "alsofake" not in tag_names

    def test_headings_inside_fence_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "fence.md"
        f.write_text(FENCE_MD, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        heading_names = {n.name for n in nodes if n.kind == "heading"}
        assert "this is code not a heading" not in heading_names
        assert "also code" not in heading_names
        # No real headings in FENCE_MD outside fences
        assert len(heading_names) == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_file_same_ids(self, tmp_path: Path) -> None:
        f = tmp_path / "idem.md"
        f.write_text("# Title\n#tag [[Link]]\n", encoding="utf-8")
        nodes1, edges1 = extract_file(f, tmp_path)
        nodes2, edges2 = extract_file(f, tmp_path)
        assert {n.node_id for n in nodes1} == {n.node_id for n in nodes2}
        assert {e.edge_id for e in edges1} == {e.edge_id for e in edges2}


# ---------------------------------------------------------------------------
# CLI integration: run_build over a tmp vault with .md files
# ---------------------------------------------------------------------------


class TestCliRunBuild:
    def test_run_build_produces_note_nodes(self, tmp_path: Path) -> None:
        """run_build over a vault with .md files must produce note nodes."""
        from mneme_graph.cli import run_build

        (tmp_path / "NoteA.md").write_text("# Alpha\n#tagx\n", encoding="utf-8")
        (tmp_path / "NoteB.md").write_text("# Beta\n[[NoteA]]\n", encoding="utf-8")

        summary = run_build(tmp_path)
        assert summary["nodes"] > 0
        assert summary["edges"] > 0

        from mneme_graph.store import GraphStore

        store = GraphStore.load(tmp_path)
        note_names = {
            n.name for n in store.nodes
            if n.kind == "note" and n.source_path != "<external>"
        }
        assert "NoteA" in note_names
        assert "NoteB" in note_names

    def test_run_build_produces_heading_and_tag_nodes(self, tmp_path: Path) -> None:
        from mneme_graph.cli import run_build
        from mneme_graph.store import GraphStore

        (tmp_path / "doc.md").write_text("# MyHeading\n#mytag\n", encoding="utf-8")
        run_build(tmp_path)
        store = GraphStore.load(tmp_path)
        kinds = {n.kind for n in store.nodes}
        assert "heading" in kinds
        assert "tag" in kinds

    def test_run_build_resolves_wikilink_ghost_to_real_note(self, tmp_path: Path) -> None:
        """_external_resolution_map must merge [[NoteA]] ghost onto real NoteA note."""
        from mneme_graph.analytics import apply_merge
        from mneme_graph.cli import _external_resolution_map, run_build
        from mneme_graph.store import GraphStore

        (tmp_path / "NoteA.md").write_text("Hello.\n", encoding="utf-8")
        (tmp_path / "NoteB.md").write_text("See [[NoteA]].\n", encoding="utf-8")
        run_build(tmp_path)

        store = GraphStore.load(tmp_path)
        canonical = _external_resolution_map(store.nodes)
        # There should be a ghost note named "NoteA" that maps to the real NoteA node
        ghost_notea = next(
            (
                n for n in store.nodes
                if n.kind == "note" and n.source_path == "<external>" and n.name == "NoteA"
            ),
            None,
        )
        assert ghost_notea is not None, "Ghost note for [[NoteA]] must exist in graph"
        assert ghost_notea.node_id in canonical, (
            "_external_resolution_map must resolve the [[NoteA]] ghost to the real NoteA note node"
        )

        # After apply_merge, the ghost should no longer appear
        merged_nodes, merged_edges = apply_merge(store.nodes, store.edges, canonical)
        remaining_ghosts = [
            n for n in merged_nodes
            if n.kind == "note" and n.source_path == "<external>" and n.name == "NoteA"
        ]
        assert remaining_ghosts == [], (
            "After merge, the NoteA ghost must be replaced by the real node"
        )


# ---------------------------------------------------------------------------
# Noise class 1: hex-color tokens must NOT become tags
# ---------------------------------------------------------------------------


class TestHexColorTagsRejected:
    """CSS hex-color tokens (#RGB, #RRGGBB, #RRGGBBAA) are NOT Obsidian tags."""

    def test_hex_color_tokens_not_tags(self, tmp_path: Path) -> None:
        # 3-digit, 6-digit, and 8-digit hex color strings must all be rejected.
        content = (
            "Button color is #00695C and hover is #FFF.\n"
            "Background: #1a2b3c and with-alpha: #00695CFF.\n"
        )
        f = tmp_path / "hex.md"
        f.write_text(content, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "00695C" not in tag_names, "#00695C (6-digit hex) must not be a tag"
        assert "FFF" not in tag_names, "#FFF (3-digit hex) must not be a tag"
        assert "1a2b3c" not in tag_names, "#1a2b3c (6-digit hex) must not be a tag"
        assert "00695CFF" not in tag_names, "#00695CFF (8-digit hex) must not be a tag"


# ---------------------------------------------------------------------------
# Noise class 2: pure-numeric tokens and citation-style bracket refs
# ---------------------------------------------------------------------------


class TestNumericTagsAndCitationRefsRejected:
    """#123 and #42 are numeric noise; bare [1] / [23] / [12] are citation refs."""

    def test_pure_numeric_not_tags(self, tmp_path: Path) -> None:
        content = "See footnote #123 and also #42 for the full list.\n"
        f = tmp_path / "numeric.md"
        f.write_text(content, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "123" not in tag_names, "#123 (pure numeric) must not be a tag"
        assert "42" not in tag_names, "#42 (pure numeric) must not be a tag"

    def test_numeric_citation_refs_not_links(self, tmp_path: Path) -> None:
        # Bare [1], [23], [12] without a (target) must never become graph links.
        content = "As shown in [1] and [23] and [12], the results hold.\n"
        f = tmp_path / "citeref.md"
        f.write_text(content, encoding="utf-8")
        _, edges = extract_file(f, tmp_path)
        assert not any(e.kind == "links_to" for e in edges), (
            "Bare numeric citation refs [1] [23] [12] must not produce links_to edges"
        )


# ---------------------------------------------------------------------------
# Noise class 3: tokens inside inline-code spans and fenced code blocks
# ---------------------------------------------------------------------------


class TestCodeSpanNoise:
    """Content inside backtick spans must not generate tags, wikilinks, or md-links."""

    def test_wikilink_in_inline_code_not_a_link(self, tmp_path: Path) -> None:
        content = "Use `[[NotALink]]` to reference the API docs.\n"
        f = tmp_path / "inlinewiki.md"
        f.write_text(content, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "NotALink" not in ghost_names, (
            "[[NotALink]] inside inline code must not create a ghost note node"
        )
        assert not any(e.kind in ("links_to", "embeds") for e in edges)

    def test_md_link_in_inline_code_not_a_link(self, tmp_path: Path) -> None:
        content = "Call `[text](Other.md)` pattern to navigate.\n"
        f = tmp_path / "inlinemdlink.md"
        f.write_text(content, encoding="utf-8")
        _, edges = extract_file(f, tmp_path)
        assert not any(e.kind == "links_to" for e in edges), (
            "[text](Other.md) inside inline code must not produce a links_to edge"
        )

    def test_fenced_block_wikilink_not_a_link(self, tmp_path: Path) -> None:
        content = "```\n[[NotALink]]\n#notatag\n```\nReal text after.\n"
        f = tmp_path / "fencedwiki.md"
        f.write_text(content, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "NotALink" not in ghost_names, (
            "[[NotALink]] inside a fenced code block must not create a ghost note"
        )
        assert not any(e.kind in ("links_to", "embeds") for e in edges)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "notatag" not in tag_names, (
            "#notatag inside a fenced block must not be extracted as a tag"
        )


# ---------------------------------------------------------------------------
# Real signal 4: non-ASCII / Turkish tags must be captured
# ---------------------------------------------------------------------------


class TestNonAsciiRealTags:
    """Tags containing Turkish diacritics and other non-ASCII letters are valid."""

    def test_non_ascii_turkish_tags_extracted(self, tmp_path: Path) -> None:
        # Real diacritic forms as used in an Obsidian vault.
        content = "#clinical-psychology #YZ #türkçe #ruh-sağlığı\n"
        f = tmp_path / "turkish.md"
        f.write_text(content, encoding="utf-8")
        nodes, _ = extract_file(f, tmp_path)
        tag_names = {n.name for n in nodes if n.kind == "tag"}
        assert "clinical-psychology" in tag_names
        assert "YZ" in tag_names
        assert "türkçe" in tag_names, "#türkçe (Turkish diacritics) must be extracted as a tag"
        assert "ruh-sağlığı" in tag_names, "#ruh-sağlığı (Turkish diacritics) must be extracted"


# ---------------------------------------------------------------------------
# Real signal 5: genuine wikilinks, md-links, and embeds are preserved
# ---------------------------------------------------------------------------


class TestRealSignalsPreserved:
    """Hex/numeric noise filters must not accidentally suppress real graph edges."""

    def test_real_wikilink_still_links_to(self, tmp_path: Path) -> None:
        content = "See [[Real Note]] for details.\n"
        f = tmp_path / "realwiki.md"
        f.write_text(content, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "Real Note" in ghost_names
        assert any(e.kind == "links_to" for e in edges)

    def test_real_md_link_still_links_to(self, tmp_path: Path) -> None:
        content = "[read more](Other.md)\n"
        f = tmp_path / "realmdlink.md"
        f.write_text(content, encoding="utf-8")
        nodes, edges = extract_file(f, tmp_path)
        ghost_names = {n.name for n in nodes if n.source_path == "<external>"}
        assert "Other" in ghost_names
        assert any(e.kind == "links_to" for e in edges)

    def test_embed_still_produces_embeds_edge(self, tmp_path: Path) -> None:
        content = "![[EmbedMe]]\n"
        f = tmp_path / "embed.md"
        f.write_text(content, encoding="utf-8")
        _, edges = extract_file(f, tmp_path)
        assert any(e.kind == "embeds" for e in edges)
