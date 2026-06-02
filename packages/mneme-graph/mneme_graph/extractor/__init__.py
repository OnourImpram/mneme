"""Extractor sub-package for mneme-graph.

Each module in this package handles one language. The public contract for
every extractor module is:

    extract_file(path: Path, vault_root: Path) -> tuple[list[GraphNode], list[GraphEdge]]

Currently shipped:
    mneme_graph.extractor.python_extractor     — tree-sitter Python extractor
    mneme_graph.extractor.javascript_extractor — tree-sitter JS/TS extractor

Registry
--------
SUPPORTED_SUFFIXES  tuple of lowercase file-extension strings handled by this package.
EXTRACTORS          dict mapping each suffix to its extract_file callable.
extract_any()       dispatch by path.suffix.lower(); unknown suffix → ([], []).

Importing this package never crashes when an optional grammar (tree-sitter-javascript,
tree-sitter-typescript) is not installed. Each extractor guards its own grammar import
inside extract_file and returns ([], []) on ImportError.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..schema import GraphEdge, GraphNode

# Public type alias for the extractor callable contract.
ExtractFn = Callable[[Path, Path], tuple[list[GraphNode], list[GraphEdge]]]


def _py_extract(path: Path, vault_root: Path) -> tuple[list[GraphNode], list[GraphEdge]]:
    from .python_extractor import extract_file

    return extract_file(path, vault_root)


def _js_extract(path: Path, vault_root: Path) -> tuple[list[GraphNode], list[GraphEdge]]:
    from .javascript_extractor import extract_file

    return extract_file(path, vault_root)


# Map of lowercase suffix → extractor callable.
# JS and TS both route through javascript_extractor which picks the right grammar
# based on the file suffix at call time.
EXTRACTORS: dict[str, ExtractFn] = {
    ".py": _py_extract,
    ".js": _js_extract,
    ".jsx": _js_extract,
    ".mjs": _js_extract,
    ".cjs": _js_extract,
    ".ts": _js_extract,
    ".tsx": _js_extract,
}

# Ordered tuple of all supported suffixes (stable, for rglob patterns in cli.py).
SUPPORTED_SUFFIXES: tuple[str, ...] = tuple(EXTRACTORS.keys())


def extract_any(
    path: Path,
    vault_root: Path,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Dispatch extraction to the appropriate extractor by file suffix.

    Args:
        path:       Absolute path to the source file.
        vault_root: Absolute vault root for vault-relative path computation.

    Returns:
        A 2-tuple (nodes, edges). Returns ([], []) for unknown suffixes.
        Never raises (individual extractors handle their own errors).
    """
    fn = EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        return [], []
    try:
        return fn(path, vault_root)
    except Exception:  # noqa: BLE001
        return [], []
