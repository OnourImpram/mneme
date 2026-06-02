"""GraphNode and GraphEdge schema for mneme-graph.

ConfidenceLabel mirrors the same string union used in mneme-mcp EvidenceCard.
Both sides define it independently to avoid cross-package coupling — the values
are identical by convention, not by shared import.

Provenance fields on every node:
    source_path   — vault-relative posix path of the source file
    content_hash  — sha256 hex of the file bytes at extraction time
    line_start    — 0-based start row of the symbol in the source
    line_end      — 0-based end row (inclusive) of the symbol
    trust         — always 'extracted' for AST-derived nodes

GraphNode.node_id = sha256(f'{source_path}:{name}:{kind}:{line_start}') for local
nodes; external nodes (source_path '<external>') omit line_start so the same
imported symbol deduplicates across files. GraphEdge.edge_id =
sha256(f'{src_id}:{dst_id}:{kind}').

Both are frozen dataclasses so they are safely hashable and cannot be
mutated after construction. Serialization round-trips via to_dict/from_dict
(not dataclasses.asdict, which does not handle Literal types portably).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

# String union used for confidence on every node and edge.
# EXTRACTED  — directly observed from the AST; no inference involved.
# INFERRED   — derived from patterns, not directly observable.
# AMBIGUOUS  — the evidence is present but contradictory or incomplete.
ConfidenceLabel = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]

# Node kind values.
NodeKind = Literal["module", "class", "function", "variable"]

# Edge kind values.
EdgeKind = Literal["calls", "imports", "defines", "inherits"]


def _sha256_id(*parts: str) -> str:
    """Return a 16-hex-char content-addressed id from concatenated parts."""
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class GraphNode:
    """A symbol extracted from a source file.

    Attributes:
        node_id      Content-addressed id: sha256(source_path:name:kind:line_start)
                     [:16] for local nodes; external nodes omit line_start.
        kind         Symbol category.
        name         Symbol name as it appears in source.
        source_path  Vault-relative posix path (e.g. 'src/foo.py').
        content_hash sha256 hex of the file bytes at extraction time.
        line_start   0-based start row.
        line_end     0-based end row (inclusive).
        confidence   Always 'EXTRACTED' for AST-derived nodes.
        trust        Provenance tier; always 'extracted' for AST nodes.
    """

    node_id: str
    kind: NodeKind
    name: str
    source_path: str
    content_hash: str
    line_start: int
    line_end: int
    confidence: ConfidenceLabel = "EXTRACTED"
    trust: str = "extracted"

    @classmethod
    def make(
        cls,
        kind: NodeKind,
        name: str,
        source_path: str,
        content_hash: str,
        line_start: int,
        line_end: int,
        confidence: ConfidenceLabel = "EXTRACTED",
        trust: str = "extracted",
    ) -> GraphNode:
        """Construct a node with an auto-derived node_id."""
        # Local nodes fold in line_start so two same-named symbols in one file
        # (e.g. a method "run" in two different classes) get distinct ids and do
        # not collapse to a single node. External nodes (source_path
        # "<external>") deliberately omit the line so the same imported module or
        # symbol deduplicates across files and call sites.
        if source_path == "<external>":
            node_id = _sha256_id(source_path, name, kind)
        else:
            node_id = _sha256_id(source_path, name, kind, str(line_start))
        return cls(
            node_id=node_id,
            kind=kind,
            name=name,
            source_path=source_path,
            content_hash=content_hash,
            line_start=line_start,
            line_end=line_end,
            confidence=confidence,
            trust=trust,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "name": self.name,
            "source_path": self.source_path,
            "content_hash": self.content_hash,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "confidence": self.confidence,
            "trust": self.trust,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> GraphNode:
        raw_line_start = d["line_start"]
        raw_line_end = d["line_end"]
        if not isinstance(raw_line_start, (int, float, str)):
            raise TypeError(f"line_start must be numeric, got {type(raw_line_start)}")
        if not isinstance(raw_line_end, (int, float, str)):
            raise TypeError(f"line_end must be numeric, got {type(raw_line_end)}")
        return cls(
            node_id=str(d["node_id"]),
            kind=d["kind"],  # type: ignore[arg-type]
            name=str(d["name"]),
            source_path=str(d["source_path"]),
            content_hash=str(d["content_hash"]),
            line_start=int(raw_line_start),
            line_end=int(raw_line_end),
            confidence=d.get("confidence", "EXTRACTED"),  # type: ignore[arg-type]
            trust=str(d.get("trust", "extracted")),
        )


@dataclass(frozen=True)
class GraphEdge:
    """A directed relationship between two GraphNodes.

    Attributes:
        edge_id     Content-addressed id: sha256(src_id:dst_id:kind)[:16].
        src_id      node_id of the source node.
        dst_id      node_id of the destination node.
        kind        Relationship category.
        confidence  Confidence label for the relationship.
        valid_at    ISO-8601 timestamp when the edge was extracted.
    """

    edge_id: str
    src_id: str
    dst_id: str
    kind: EdgeKind
    confidence: ConfidenceLabel = "EXTRACTED"
    valid_at: str = ""

    @classmethod
    def make(
        cls,
        src_id: str,
        dst_id: str,
        kind: EdgeKind,
        confidence: ConfidenceLabel = "EXTRACTED",
        valid_at: str = "",
    ) -> GraphEdge:
        """Construct an edge with an auto-derived edge_id."""
        edge_id = _sha256_id(src_id, dst_id, kind)
        return cls(
            edge_id=edge_id,
            src_id=src_id,
            dst_id=dst_id,
            kind=kind,
            confidence=confidence,
            valid_at=valid_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "valid_at": self.valid_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> GraphEdge:
        return cls(
            edge_id=str(d["edge_id"]),
            src_id=str(d["src_id"]),
            dst_id=str(d["dst_id"]),
            kind=d["kind"],  # type: ignore[arg-type]
            confidence=d.get("confidence", "EXTRACTED"),  # type: ignore[arg-type]
            valid_at=str(d.get("valid_at", "")),
        )
