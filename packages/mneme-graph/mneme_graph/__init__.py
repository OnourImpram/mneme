"""mneme-graph: local code knowledge graph with confidence-labelled nodes.

Derives a rebuildable graph from source files via tree-sitter extraction.
The ground truth is always the source files; graph.json is a derived artifact
that can be deleted and rebuilt identically from the same source.

Public surface:

    from mneme_graph.schema import GraphNode, GraphEdge, ConfidenceLabel
    from mneme_graph.store import GraphStore
    from mneme_graph.extractor.python_extractor import extract_file
"""

from importlib.metadata import version as _distribution_version

__version__ = _distribution_version("mneme-graph")
__all__ = ["__version__"]
