"""Deterministic, zero-API Obsidian/Markdown extractor for mneme-graph.

Walks a single .md file and produces:

* GraphNode(kind='note')     — one per file; name = filename stem so that
                               Obsidian-style [[wikilinks]] resolve by filename.
* GraphNode(kind='heading')  — one per ATX heading (# … through ######) not
                               inside a fenced code block; edge kind='has_heading'.
* GraphNode(kind='tag')      — one per distinct tag (#tagname) found in body
                               text or YAML frontmatter; source_path='<external>'
                               so the same tag name deduplicates across vault files.
* Ghost note nodes           — source_path='<external>' for [[Wikilink]] and
                               [text](target.md) targets; resolved at build time
                               by cli._external_resolution_map via (name, 'note').

Redaction: only structural names (stems, heading text, tag names), paths, hashes,
and line numbers are stored. No paragraph prose, no string-literal values.

The extractor is stateless and side-effect-free at import time.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..schema import GraphEdge, GraphNode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Image/asset suffixes: [[Target]] embeds with these suffixes are skipped in v1.
_ASSET_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp4", ".mov", ".mp3", ".wav"}
)

# Regex for Obsidian tags in body text: # followed by valid tag char sequence.
# \w captures ASCII word chars AND Unicode letters (Turkish diacritics, etc.).
# Pure-hex-color and pure-numeric candidates are filtered by _is_valid_tag().
_TAG_RE = re.compile(r"(?<![/\[])#(\w[\w/\-]*)")

# CSS hex-color lengths: exactly 3, 6, or 8 hex digits — not a real tag.
# Used by _is_valid_tag() to reject tokens like #00695C, #FFF, #00695CFF.
_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{2})?)?$")

# ATX heading: ^#{1,6} followed by a space and text.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")

# Wikilink: [[Target]], [[Target|alias]], [[Target#heading]], [[Target#heading|alias]]
# also embed: ![[Target]] — captured by checking for leading !
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")

# Markdown link: [text](target) — group 1 = link text, group 2 = target.
# Both groups are captured so numeric-only link texts ([1](url)) can be rejected.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Fence opener: ``` or ~~~ (3+ chars)
_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")

# Inline code: strip `...` spans before tag scanning
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# URL schemes to skip for markdown links
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _vault_rel(path: Path, vault_root: Path) -> str:
    return path.relative_to(vault_root).as_posix()


def _is_asset(stem_or_target: str) -> bool:
    """Return True if the target looks like an image/asset file."""
    suffix = Path(stem_or_target).suffix.lower()
    return suffix in _ASSET_SUFFIXES


def _wikilink_stem(target: str) -> str:
    """Strip #heading and |alias from a wikilink target, return basename stem."""
    # Strip |alias
    if "|" in target:
        target = target.split("|", 1)[0]
    # Strip #heading
    if "#" in target:
        target = target.split("#", 1)[0]
    return Path(target).stem


def _parse_frontmatter_tags(lines: list[str]) -> list[str]:
    """Extract tags from YAML frontmatter at file top.

    Returns a list of tag strings (without leading #).
    Frontmatter is a ``---`` block starting on line 0.
    Handles both YAML list and inline/space/comma separated formats.
    """
    if not lines or lines[0].rstrip() != "---":
        return []

    # Find closing ---
    end = -1
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end = i
            break
    if end == -1:
        return []

    tags: list[str] = []
    in_tags = False
    for line in lines[1:end]:
        stripped = line.strip()
        if stripped.lower().startswith("tags:"):
            in_tags = True
            # Inline: tags: [foo, bar]  or  tags: foo bar  or  tags:
            rest = stripped[5:].strip()
            if rest.startswith("[") and rest.endswith("]"):
                # YAML flow sequence: [foo, bar, baz]
                inner = rest[1:-1]
                for item in inner.split(","):
                    t = item.strip().strip('"').strip("'").lstrip("#")
                    if t:
                        tags.append(t)
                in_tags = False  # consumed entirely
            elif rest:
                # space or comma separated inline
                for item in re.split(r"[,\s]+", rest):
                    t = item.strip().strip('"').strip("'").lstrip("#")
                    if t:
                        tags.append(t)
                in_tags = False
        elif in_tags:
            # YAML block list entry: '  - tagname'
            m = re.match(r"^\s*-\s+(.+)", stripped)
            if m:
                t = m.group(1).strip().strip('"').strip("'").lstrip("#")
                if t:
                    tags.append(t)
            elif stripped and not stripped.startswith("-"):
                # No longer in tags block
                in_tags = False

    return tags


def _is_valid_tag(name: str) -> bool:
    """Return True only if *name* is a real Obsidian tag candidate.

    Rejects:
    * CSS hex-color tokens with exactly 3, 6, or 8 hex digits (#FFF, #00695C,
      #00695CFF) — ``_HEX_COLOR_RE`` covers all three lengths in one pass.
    * Pure-numeric strings (#42, #123) — digits only, no letter content.

    Non-ASCII letters (Turkish diacritics, etc.) pass through as valid.
    """
    if _HEX_COLOR_RE.match(name):
        return False
    return not name.isdigit()


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


def extract_file(
    path: Path,
    vault_root: Path,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Extract GraphNodes and GraphEdges from a single Markdown file.

    Args:
        path:       Absolute path to the .md or .markdown file.
        vault_root: Absolute vault root for vault-relative path computation.

    Returns:
        A 2-tuple (nodes, edges). Both lists may be empty on read failure.
        Never raises (errors are swallowed; caller gets partial results).
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return [], []

    chash = _content_hash(raw_bytes)
    try:
        rel = _vault_rel(path.resolve(), vault_root.resolve())
    except ValueError:
        return [], []

    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    last_line = max(0, len(lines) - 1)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # --- NOTE node (one per file) ---
    note_name = path.stem
    note_node = GraphNode.make(
        kind="note",
        name=note_name,
        source_path=rel,
        content_hash=chash,
        line_start=0,
        line_end=last_line,
    )
    nodes.append(note_node)

    # --- Track seen ids for dedup ---
    seen_node_ids: set[str] = {note_node.node_id}
    seen_edge_ids: set[str] = set()

    def _add_node(node: GraphNode) -> None:
        if node.node_id not in seen_node_ids:
            seen_node_ids.add(node.node_id)
            nodes.append(node)

    def _add_edge(edge: GraphEdge) -> None:
        if edge.edge_id not in seen_edge_ids:
            seen_edge_ids.add(edge.edge_id)
            edges.append(edge)

    # --- Detect frontmatter bounds ---
    fm_end_line = -1  # last line index of frontmatter (the closing ---)
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                fm_end_line = i
                break

    # --- YAML frontmatter tags ---
    try:
        fm_tags = _parse_frontmatter_tags(lines)
    except Exception:  # noqa: BLE001
        fm_tags = []

    for tag_name in fm_tags:
        tag_name = tag_name.strip()
        if not tag_name:
            continue
        tag_node = GraphNode.make(
            kind="tag",
            name=tag_name,
            source_path="<external>",
            content_hash="",
            line_start=0,
            line_end=0,
        )
        _add_node(tag_node)
        _add_edge(
            GraphEdge.make(
                src_id=note_node.node_id,
                dst_id=tag_node.node_id,
                kind="tagged",
            )
        )

    # --- Walk lines: track fenced code blocks, emit headings/tags/links ---
    in_fence = False
    fence_char: str = ""

    for lineno, line in enumerate(lines):
        # Skip frontmatter lines (including the --- delimiters)
        if fm_end_line >= 0 and lineno <= fm_end_line:
            continue

        # Fence open/close detection
        m_fence = _FENCE_OPEN_RE.match(line)
        if m_fence:
            opener = m_fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = opener[0]  # '`' or '~'
            elif opener[0] == fence_char:
                in_fence = False
            continue  # fence delimiter lines carry no extractable content

        if in_fence:
            continue  # inside fenced code: skip everything

        # --- ATX heading ---
        m_heading = _HEADING_RE.match(line)
        if m_heading:
            heading_text = m_heading.group(2).strip()
            # Strip trailing # chars (closing hashes in ATX headings like ## Title ##)
            heading_text = heading_text.rstrip("#").strip()
            if heading_text:
                h_node = GraphNode.make(
                    kind="heading",
                    name=heading_text,
                    source_path=rel,
                    content_hash=chash,
                    line_start=lineno,
                    line_end=lineno,
                )
                _add_node(h_node)
                _add_edge(
                    GraphEdge.make(
                        src_id=note_node.node_id,
                        dst_id=h_node.node_id,
                        kind="has_heading",
                    )
                )
            # Headings can still contain wikilinks; fall through to link scanning below.

        # Strip inline-code spans once, before ALL link and tag scanning.
        # This prevents `[[NotALink]]` or `[text](file.md)` inside backtick
        # spans from being extracted as graph edges.
        line_stripped = _INLINE_CODE_RE.sub("", line)

        # --- Wikilinks and embeds (on inline-code-stripped line) ---
        for m_wl in _WIKILINK_RE.finditer(line_stripped):
            is_embed = m_wl.group(1) == "!"
            raw_target = m_wl.group(2)
            stem = _wikilink_stem(raw_target)
            if not stem:
                continue
            # Skip image/asset embeds in v1
            if _is_asset(stem) or _is_asset(raw_target.split("|")[0].split("#")[0]):
                continue
            ghost = GraphNode.make(
                kind="note",
                name=stem,
                source_path="<external>",
                content_hash="",
                line_start=0,
                line_end=0,
            )
            _add_node(ghost)
            _add_edge(
                GraphEdge.make(
                    src_id=note_node.node_id,
                    dst_id=ghost.node_id,
                    kind="embeds" if is_embed else "links_to",
                )
            )

        # --- Markdown links [text](target) (on inline-code-stripped line) ---
        for m_mdl in _MD_LINK_RE.finditer(line_stripped):
            link_text = m_mdl.group(1).strip()
            # Reject numeric citation refs such as [1](note.md) or [23](ref.md).
            if link_text.isdigit():
                continue
            target = m_mdl.group(2).strip()
            # Skip URLs
            if _URL_SCHEME_RE.match(target):
                continue
            # Skip image-like targets
            if _is_asset(target):
                continue
            t_path = Path(target)
            suffix = t_path.suffix.lower()
            # Only .md targets or extension-less targets
            if suffix not in ("", ".md", ".markdown"):
                continue
            stem = t_path.stem
            if not stem:
                continue
            ghost = GraphNode.make(
                kind="note",
                name=stem,
                source_path="<external>",
                content_hash="",
                line_start=0,
                line_end=0,
            )
            _add_node(ghost)
            _add_edge(
                GraphEdge.make(
                    src_id=note_node.node_id,
                    dst_id=ghost.node_id,
                    kind="links_to",
                )
            )

        # --- Body tags (inline code + wikilink interiors + URLs stripped) ---
        # Start from already-inline-code-stripped line_stripped.
        line_for_tags = re.sub(r"\[\[[^\]]*\]\]", "", line_stripped)
        line_for_tags = re.sub(r"https?://\S+", "", line_for_tags)
        # If line is an ATX heading, remove leading # markers so they aren't
        # parsed as tag tokens.
        if m_heading:
            line_for_tags = _HEADING_RE.sub(lambda m: " " + m.group(2), line_for_tags)

        for m_tag in _TAG_RE.finditer(line_for_tags):
            tag_name = m_tag.group(1)
            # Reject CSS hex-color tokens and pure-numeric strings.
            if not _is_valid_tag(tag_name):
                continue
            tag_node = GraphNode.make(
                kind="tag",
                name=tag_name,
                source_path="<external>",
                content_hash="",
                line_start=0,
                line_end=0,
            )
            _add_node(tag_node)
            _add_edge(
                GraphEdge.make(
                    src_id=note_node.node_id,
                    dst_id=tag_node.node_id,
                    kind="tagged",
                )
            )

    # --- Deterministic sorted output ---
    nodes_out = sorted(nodes, key=lambda n: n.node_id)
    edges_out = sorted(edges, key=lambda e: e.edge_id)
    return nodes_out, edges_out
