"""Completeness (recall) metric for mneme-graph.

Measures how well a graph captures the TRUE semantic relations present in
vault markdown source files.  A sparse graph that captures almost nothing
scores LOW recall; a graph capturing the vault's real wikilinks, tags, and
embeds scores HIGH recall.  This replaces the old token-reduction ratio which
rewarded sparsity.

Public API
----------
count_source_relations(vault_path)  -> dict[str, int]    # ground-truth G
graph_capture(graph)                -> dict[str, int]    # captured edge counts
completeness(vault_path, graph)     -> dict[str, object] # recall metric
load_graph(vault_path)              -> GraphStore         # helper
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .store import GraphStore

# ---------------------------------------------------------------------------
# Regex constants — mirrored exactly from extractor/markdown_extractor.py
# so that ground-truth counting matches what the extractor accepts (H1.1).
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"(?<![/\[])#(\w[\w/\-]*)")
_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{2})?)?$")
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")
_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")

_ASSET_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp4", ".mov", ".mp3", ".wav"}
)


# ---------------------------------------------------------------------------
# Helpers — mirrored from markdown_extractor.py to match extraction rules
# ---------------------------------------------------------------------------


def _is_valid_tag(name: str) -> bool:
    """Return True only if *name* is a real Obsidian tag candidate (H1.1 gate).

    Rejects CSS hex-color tokens (3, 6, or 8 hex digits) and pure-numeric strings.
    Turkish diacritics and other non-ASCII letters pass through as valid.
    Mirrors markdown_extractor._is_valid_tag exactly.
    """
    if _HEX_COLOR_RE.match(name):
        return False
    return not name.isdigit()


def _is_asset(target: str) -> bool:
    return Path(target).suffix.lower() in _ASSET_SUFFIXES


def _wikilink_stem(target: str) -> str:
    if "|" in target:
        target = target.split("|", 1)[0]
    if "#" in target:
        target = target.split("#", 1)[0]
    return Path(target).stem


def _strip_atx_hashes(m: re.Match[str]) -> str:
    """re.sub callback: replace an ATX heading line with its text only.

    Prevents the leading ``#`` chars of a heading from being parsed as a tag.
    """
    return " " + m.group(2)


def _parse_frontmatter_tags(lines: list[str]) -> list[str]:
    """Extract tags from YAML frontmatter.  Mirrors markdown_extractor exactly."""
    if not lines or lines[0].rstrip() != "---":
        return []
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
            rest = stripped[5:].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                for item in inner.split(","):
                    t = item.strip().strip('"').strip("'").lstrip("#")
                    if t:
                        tags.append(t)
                in_tags = False
            elif rest:
                for item in re.split(r"[,\s]+", rest):
                    t = item.strip().strip('"').strip("'").lstrip("#")
                    if t:
                        tags.append(t)
                in_tags = False
        elif in_tags:
            fm = re.match(r"^\s*-\s+(.+)", stripped)
            if fm:
                t = fm.group(1).strip().strip('"').strip("'").lstrip("#")
                if t:
                    tags.append(t)
            elif stripped and not stripped.startswith("-"):
                in_tags = False
    return tags


# ---------------------------------------------------------------------------
# Core per-file scanner
# ---------------------------------------------------------------------------


def _count_file_relations(path: Path) -> tuple[int, int, int]:
    """Count wikilinks, valid tags, and embeds in one markdown file.

    Returns ``(wikilinks, tags, embeds)``.
    Skips fenced code blocks and inline code spans — mirrors extractor behaviour.
    Content-free: counts only; no prose is stored.
    """
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return 0, 0, 0

    lines = text.splitlines()
    wikilinks = 0
    tags = 0
    embeds = 0

    # Frontmatter tags (no body-tag validity filter — mirrors extractor)
    try:
        fm_tags = _parse_frontmatter_tags(lines)
    except Exception:
        fm_tags = []
    tags += len(fm_tags)

    # Detect frontmatter span so the body walk skips those lines
    fm_end_line = -1
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                fm_end_line = i
                break

    in_fence = False
    fence_char: str = ""

    for lineno, line in enumerate(lines):
        if fm_end_line >= 0 and lineno <= fm_end_line:
            continue

        m_fence = _FENCE_OPEN_RE.match(line)
        if m_fence:
            opener = m_fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = opener[0]
            elif opener[0] == fence_char:
                in_fence = False
            continue

        if in_fence:
            continue

        # Heading check on the original line (before inline-code stripping)
        m_heading = _HEADING_RE.match(line)

        # Strip inline code spans before wikilink / tag scanning
        line_stripped = _INLINE_CODE_RE.sub("", line)

        # Count wikilinks and embeds (skip asset targets — mirrors extractor)
        for m_wl in _WIKILINK_RE.finditer(line_stripped):
            is_embed = m_wl.group(1) == "!"
            raw_target = m_wl.group(2)
            stem = _wikilink_stem(raw_target)
            if not stem:
                continue
            if _is_asset(stem) or _is_asset(raw_target.split("|")[0].split("#")[0]):
                continue
            if is_embed:
                embeds += 1
            else:
                wikilinks += 1

        # Count valid body tags (wikilink interiors and URLs stripped first)
        line_for_tags = re.sub(r"\[\[[^\]]*\]\]", "", line_stripped)
        line_for_tags = re.sub(r"https?://\S+", "", line_for_tags)
        if m_heading:
            line_for_tags = _HEADING_RE.sub(_strip_atx_hashes, line_for_tags)

        for m_tag in _TAG_RE.finditer(line_for_tags):
            if _is_valid_tag(m_tag.group(1)):
                tags += 1

    return wikilinks, tags, embeds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def count_source_relations(vault_path: Path) -> dict[str, int]:
    """Scan ``.md`` files under *vault_path* and count TRUE semantic relations.

    This is the **ground-truth G** — independent of any graph.  Counts:

    * **wikilinks** — ``[[Target]]``, ``[[Target|alias]]``, ``[[Target#h]]``
      (each occurrence; asset embeds excluded)
    * **tags** — body ``#tag`` tokens passing the H1.1 validity gate (rejects
      hex-colors ``#00695C``, ``#FFF``; pure-numeric ``#123``; accepts Turkish
      diacritics ``#türkçe``); YAML frontmatter tag entries are included without
      the validity filter, mirroring the extractor.
    * **embeds** — ``![[Target]]`` (asset embeds excluded)

    Content-free: counts only; no file content is stored.  Deterministic.
    """
    total_wikilinks = 0
    total_tags = 0
    total_embeds = 0

    for md_file in sorted(vault_path.rglob("*.md")):
        w, t, e = _count_file_relations(md_file)
        total_wikilinks += w
        total_tags += t
        total_embeds += e

    total = total_wikilinks + total_tags + total_embeds
    return {
        "wikilinks": total_wikilinks,
        "tags": total_tags,
        "embeds": total_embeds,
        "total": total,
    }


def graph_capture(graph: Any) -> dict[str, int]:
    """Count the semantic edges the *graph* captured, by kind.

    **Tolerant**: reads ``edge.kind`` attributes; works with ``GraphStore`` and
    foreign graph objects that expose an ``.edges`` iterable.  Non-semantic
    kinds (``has_heading``, ``calls``, ``imports``, etc.) are silently ignored.

    Returns ``{links_to: int, tagged: int, embeds: int, total: int}``.
    """
    links_to = 0
    tagged = 0
    embeds = 0

    for edge in graph.edges:
        kind = edge.kind
        if kind == "links_to":
            links_to += 1
        elif kind == "tagged":
            tagged += 1
        elif kind == "embeds":
            embeds += 1

    total = links_to + tagged + embeds
    return {
        "links_to": links_to,
        "tagged": tagged,
        "embeds": embeds,
        "total": total,
    }


def completeness(vault_path: Path, graph: Any) -> dict[str, object]:
    """Compute recall of semantic relations captured by *graph* vs vault source.

    A **sparse** graph that captures almost nothing scores low recall.
    A graph capturing the vault's real wikilinks, tags, and embeds scores high.

    The metric is also applicable to a foreign graph whose captured-counts are
    fed in via a duck-typed object — ``graph_capture`` reads only ``edge.kind``
    attributes, so any graph with an ``.edges`` iterable works.

    Returns::

        {
            "ground_truth": {"wikilinks": int, "tags": int,
                             "embeds": int,    "total": int},
            "captured":     {"links_to": int,  "tagged": int,
                             "embeds": int,    "total": int},
            "recall":       float,           # 0.0 – 1.0, clamped
            "recall_by_kind": {
                "links_to": float,  # captured.links_to / max(1, gt.wikilinks)
                "tagged":   float,  # captured.tagged   / max(1, gt.tags)
                "embeds":   float,  # captured.embeds   / max(1, gt.embeds)
            },
        }

    ``recall`` is clamped to ``[0.0, 1.0]`` to guard against graph over-capture.
    """
    gt = count_source_relations(vault_path)
    cap = graph_capture(graph)

    recall = min(1.0, cap["total"] / max(1, gt["total"]))

    recall_by_kind: dict[str, float] = {
        "links_to": min(1.0, cap["links_to"] / max(1, gt["wikilinks"])),
        "tagged": min(1.0, cap["tagged"] / max(1, gt["tags"])),
        "embeds": min(1.0, cap["embeds"] / max(1, gt["embeds"])),
    }

    return {
        "ground_truth": gt,
        "captured": cap,
        "recall": recall,
        "recall_by_kind": recall_by_kind,
    }


def load_graph(vault_path: Path) -> GraphStore:
    """Load a built graph from *vault_path* using the store loader.

    Returns an empty ``GraphStore`` if no graph has been built yet (i.e. no
    ``<vault>/.mneme/graph.json`` file exists).
    """
    return GraphStore.load(vault_path)
