"""Frame-to-graph resolution: match stack frames against mneme-graph nodes.

Design constraints
------------------
* No hard import of ``mneme_graph`` types — the dependency is optional at
  runtime (the store may be ``None``) and hard coupling would break mypy
  ``--strict`` when ``mneme_graph`` stubs are absent.
* A local ``typing.Protocol`` pair (``_NodeLike`` / ``_StoreLike``) defines
  the minimal structural interface required for matching.  Any object that
  satisfies the protocol works — including a real ``GraphStore``.
* Clean fallback: if ``graph_store`` is ``None`` or its ``.nodes`` sequence
  is empty, every frame maps to ``(frame, None)`` with no error.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from mneme_code.stacktrace import Frame

# ---------------------------------------------------------------------------
# Structural protocols (no mneme_graph import required)
# ---------------------------------------------------------------------------


@runtime_checkable
class _NodeLike(Protocol):
    """Minimal interface for a graph node used by :func:`resolve_frames`."""

    name: str
    source_path: str
    kind: str


@runtime_checkable
class _StoreLike(Protocol):
    """Minimal interface for a graph store used by :func:`resolve_frames`."""

    @property
    def nodes(self) -> Sequence[_NodeLike]:
        """Ordered sequence of graph nodes."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_frames(
    parsed: object,
    graph_store: object | None,
) -> list[tuple[Frame, object | None]]:
    """Match each frame in *parsed* against nodes in *graph_store*.

    Matching criteria: a node matches a frame when ``node.kind == "function"``,
    ``node.name == frame.function``, and the frame's (normalised) absolute
    ``file_path`` equals or ends with ``/<node.source_path>``. The suffix test
    bridges absolute traceback paths and vault-relative graph ``source_path``.

    Args:
        parsed:      A :class:`~mneme_code.stacktrace.ParsedTraceback` (or
                     any object with a ``.frames`` attribute that is a
                     sequence of :class:`Frame`).  Accepted as ``object``
                     to avoid a circular import; the attribute is accessed
                     via ``getattr``.
        graph_store: A :class:`~mneme_graph.store.GraphStore` or any object
                     satisfying :class:`_StoreLike`, or ``None``.  When
                     ``None`` or empty, every frame maps to ``(frame, None)``.

    Returns:
        A list of ``(frame, node_or_none)`` pairs in frame order.
        Each pair contains the original :class:`Frame` and either the
        matching node object or ``None`` if no match was found.
    """
    # Extract frames from parsed; tolerate any object with .frames.
    frames_raw = getattr(parsed, "frames", ())
    frames: list[Frame] = [f for f in frames_raw if isinstance(f, Frame)]

    # Fast path: no store or no nodes.
    if graph_store is None:
        return [(frame, None) for frame in frames]

    # Attempt to read .nodes from the store via the protocol.
    nodes_seq: Sequence[object] = ()
    if isinstance(graph_store, _StoreLike):
        try:
            nodes_seq = graph_store.nodes
        except Exception:  # noqa: BLE001
            nodes_seq = ()

    if not nodes_seq:
        return [(frame, None) for frame in frames]

    # Traceback file paths are absolute while graph source_paths are
    # vault-relative, so match by function name plus a path-suffix test rather
    # than exact equality (which would never match a real traceback).
    func_nodes: list[_NodeLike] = []
    for node in nodes_seq:
        if isinstance(node, _NodeLike) and node.kind == "function" and node.source_path:
            func_nodes.append(node)

    result: list[tuple[Frame, object | None]] = []
    for frame in frames:
        normalised = frame.file_path.replace("\\", "/")
        match: object | None = None
        for node in func_nodes:
            if node.name != frame.function:
                continue
            if normalised == node.source_path or normalised.endswith("/" + node.source_path):
                match = node
                break
        result.append((frame, match))

    return result
