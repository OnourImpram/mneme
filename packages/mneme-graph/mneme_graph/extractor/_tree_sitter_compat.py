"""Runtime guard for the tree-sitter ABI supported by mneme-graph.

The 0.26.0 Python binding is not ABI-compatible with the currently released
language wheels used by mneme-graph.  The mismatch can terminate the process
with SIGSEGV or SIGBUS while iterating a parsed tree.  Package metadata pins
``tree-sitter<0.26``.  This guard also protects users who bypass dependency
resolution or import mneme-graph from a mixed environment.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_MIN_SUPPORTED = (0, 25)
_MAX_EXCLUSIVE = (0, 26)


def _major_minor(raw: str) -> tuple[int, int]:
    """Parse the numeric major and minor prefix of a PEP 440 version."""
    numeric: list[int] = []
    for part in raw.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        numeric.append(int(digits))
        if len(numeric) == 2:
            break
    if len(numeric) != 2:
        raise RuntimeError(f"Unsupported tree-sitter version string: {raw!r}")
    return numeric[0], numeric[1]


def assert_tree_sitter_compatible(raw_version: str | None = None) -> None:
    """Fail safely before parsing when the installed binding is unsupported."""
    if raw_version is None:
        try:
            raw_version = version("tree-sitter")
        except PackageNotFoundError as exc:
            raise ImportError(
                "tree-sitter is required for source extraction. "
                "Install mneme-graph with its declared dependencies."
            ) from exc

    parsed = _major_minor(raw_version)
    if parsed < _MIN_SUPPORTED or parsed >= _MAX_EXCLUSIVE:
        raise RuntimeError(
            "mneme-graph requires tree-sitter>=0.25,<0.26 because the "
            "currently supported language wheels are not ABI-compatible with "
            f"tree-sitter {raw_version}. Reinstall mneme-graph so the resolver "
            "can select a compatible binding."
        )
