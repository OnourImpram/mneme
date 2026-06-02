"""Local-first dense vector retrieval backend for mneme.

This module provides a pure-Python, deterministic, network-free dense
retrieval backend that conforms to the ``RetrievalBackend`` Protocol
defined in ``mneme_core.retrieval.rrf``.

Default embedder
----------------
``hashing_embed`` implements signed feature-hashing (the "hashing trick")
over bag-of-words tokens. It requires no model, no network, and no
third-party dependencies beyond the standard library. It is fully
deterministic: the same text always produces the same vector.

Opt-in real embeddings
-----------------------
Operators who want semantic similarity can inject any embedding function
that accepts a ``str`` and returns a unit-norm ``tuple[float, ...]`` of
the configured ``dim``. For example, a sentence-transformers wrapper::

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")

    def st_embed(text: str) -> tuple[float, ...]:
        vec = model.encode(text, normalize_embeddings=True)
        return tuple(float(x) for x in vec)

    index = build_dense_index(db_path, embed_fn=st_embed, dim=384)
    backend = DenseBackend(index, embed_fn=st_embed)

Pass the *same* ``embed_fn`` to both ``build_dense_index`` and
``DenseBackend`` so query and document vectors live in the same space.
sentence-transformers is never imported by this module; the default
hashing embedder needs no model or network.

RRF dedup alignment
--------------------
``DenseDoc.doc_id`` mirrors the FTS5 ``documents.id`` INTEGER PRIMARY KEY
(the AUTOINCREMENT rowid). ``DenseBackend.__call__`` sets ``Hit.id`` to
that same integer so ``rrf_fuse`` in ``rrf.py`` deduplicates by the shared
key and a document retrieved by both legs appears once with
``sources=["dense","fts5"]``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rrf import Hit

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

EmbedFn = Callable[[str], tuple[float, ...]]


# ---------------------------------------------------------------------------
# Hashing embedder
# ---------------------------------------------------------------------------


def hashing_embed(text: str, *, dim: int = 256) -> tuple[float, ...]:
    """Deterministic feature-hashing bag-of-words embedder.

    Tokenizes *text* by lowercasing and splitting on non-alphanumeric runs,
    hashes each token with SHA-1 to select a bucket and sign, accumulates
    counts, then L2-normalizes the result. A zero-length or all-whitespace
    input returns an all-zero vector.

    Args:
        text: input text to embed.
        dim:  output vector dimension (default 256).

    Returns:
        A length-``dim`` tuple of floats. Non-empty input produces a
        unit-norm vector; empty/whitespace input produces all zeros.
    """
    import re

    acc: list[float] = [0.0] * dim
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    for tok in tokens:
        if not tok:
            continue
        # Primary hash: bucket index
        h1 = int.from_bytes(hashlib.sha1(tok.encode()).digest()[:4], "big")
        bucket = h1 % dim
        # Sign: second hash (use bytes 4-8 of the same digest)
        h_bytes = hashlib.sha1(tok.encode()).digest()
        sign = 1.0 if (h_bytes[4] & 1) == 0 else -1.0
        acc[bucket] += sign

    # L2-normalize
    norm = math.sqrt(sum(x * x for x in acc))
    if norm == 0.0:
        return tuple(acc)
    inv = 1.0 / norm
    return tuple(x * inv for x in acc)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of two unit-norm vectors (equals cosine similarity).

    Mismatched lengths or a zero vector both return 0.0. Never raises.

    Args:
        a: first vector.
        b: second vector.

    Returns:
        Cosine similarity in [-1.0, 1.0], or 0.0 on degenerate inputs.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    # Guard against floating-point drift pushing |dot| marginally > 1
    return max(-1.0, min(1.0, dot))


# ---------------------------------------------------------------------------
# DenseDoc
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DenseDoc:
    """A single indexed document with its pre-computed vector.

    Attributes:
        doc_id: mirrors ``documents.id`` (the FTS5 rowid) so RRF fusion
                deduplications by the same integer key across both legs.
        path:   relative path stored in ``documents.path``.
        title:  document title stored in ``documents.title``.
        vector: pre-computed unit-norm embedding tuple.
    """

    doc_id: int
    path: str
    title: str
    vector: tuple[float, ...]


# ---------------------------------------------------------------------------
# DenseIndex
# ---------------------------------------------------------------------------

_DEFAULT_DIM = 256


@dataclass
class DenseIndex:
    """In-memory dense index over a fixed set of ``DenseDoc`` records.

    Attributes:
        dim:  embedding dimension; all stored vectors must have this length.
        docs: list of indexed documents.
    """

    dim: int = _DEFAULT_DIM
    docs: list[DenseDoc] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query_vector: Sequence[float], limit: int) -> list[tuple[DenseDoc, float]]:
        """Return the top-``limit`` documents ranked by cosine similarity.

        Ties are broken by ascending ``doc_id`` for full determinism.

        Args:
            query_vector: query embedding (should be unit-norm).
            limit:        maximum results to return.

        Returns:
            List of ``(DenseDoc, score)`` pairs, best-first. Empty when
            ``docs`` is empty or ``limit`` <= 0.
        """
        if not self.docs or limit <= 0:
            return []
        scored = [(doc, cosine(query_vector, doc.vector)) for doc in self.docs]
        scored.sort(key=lambda t: (-t[1], t[0].doc_id))
        return scored[:limit]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize the index to a JSON string.

        Format::

            {
              "dim": 256,
              "provider": "hashing",
              "docs": [
                {"doc_id": 1, "path": "a.md", "title": "A",
                 "vector": [0.1, ...]},
                ...
              ]
            }
        """
        payload: dict[str, Any] = {
            "dim": self.dim,
            "provider": "hashing",
            "docs": [
                {
                    "doc_id": d.doc_id,
                    "path": d.path,
                    "title": d.title,
                    "vector": list(d.vector),
                }
                for d in self.docs
            ],
        }
        return json.dumps(payload, separators=(",", ":"))

    def save(self, path: Path) -> None:
        """Write the index to *path* as JSON.

        Creates parent directories as needed. Never raises; failures are
        silently swallowed so the caller's retrieval path stays non-fatal.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.to_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def from_json(cls, data: str) -> DenseIndex:
        """Deserialize an index from a JSON string.

        Returns an empty ``DenseIndex`` with default dim if *data* is
        malformed. Never raises.
        """
        try:
            obj = json.loads(data)
            dim = int(obj.get("dim", _DEFAULT_DIM))
            docs = [
                DenseDoc(
                    doc_id=int(d["doc_id"]),
                    path=str(d["path"]),
                    title=str(d["title"]),
                    vector=tuple(float(x) for x in d["vector"]),
                )
                for d in obj.get("docs", [])
            ]
            return cls(dim=dim, docs=docs)
        except Exception:  # noqa: BLE001
            return cls()

    @classmethod
    def load(cls, path: Path) -> DenseIndex:
        """Load an index from *path*.

        Returns an empty ``DenseIndex`` when the file is absent or
        malformed. Never raises.
        """
        try:
            return cls.from_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return cls()


# ---------------------------------------------------------------------------
# build_dense_index
# ---------------------------------------------------------------------------

# Column used to read body text from the documents table.
# Confirmed from mneme_core/fts5/indexer.py SCHEMA: the column is `body_text`.
_BODY_COLUMN = "body_text"


def build_dense_index(
    db_path: Path,
    *,
    embed_fn: EmbedFn = hashing_embed,
    dim: int = _DEFAULT_DIM,
) -> DenseIndex:
    """Build a ``DenseIndex`` from the FTS5 ``documents`` table.

    Opens the SQLite database at *db_path* read-only (mirroring
    ``fts5_search``'s ``file:...?mode=ro`` pattern). Reads every row from
    ``documents``, embeds the ``body_text`` column (falling back to
    ``title || ' ' || path`` when ``body_text`` is ``NULL`` or empty), and
    constructs a ``DenseDoc`` using ``documents.id`` as the dedup key.

    If the ``body_text`` column is absent from the schema (legacy database),
    the function gracefully falls back to embedding ``title + path`` for
    every row and continues without raising.

    Args:
        db_path:  path to the FTS5 SQLite database.
        embed_fn: embedding callable; defaults to ``hashing_embed``.
        dim:      embedding dimension passed to ``embed_fn`` when it is
                  the default hashing embedder. Ignored for injected
                  ``embed_fn`` (the caller controls dim there).

    Returns:
        A populated ``DenseIndex``. Returns an empty index (no docs) when
        the database is absent, unreadable, or has no rows. Never raises.
    """
    if not db_path.exists():
        return DenseIndex(dim=dim)

    docs: list[DenseDoc] = []
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only = ON")

            # Detect whether body_text exists in the schema
            col_names = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            has_body = _BODY_COLUMN in col_names

            if has_body:
                rows = conn.execute("SELECT id, path, title, body_text FROM documents").fetchall()
            else:
                # Graceful degrade: embed title+path when body_text absent
                rows = conn.execute("SELECT id, path, title FROM documents").fetchall()

            for row in rows:
                doc_id = int(row[0])
                path = str(row[1] or "")
                title = str(row[2] or "")
                if has_body:
                    body = str(row[3] or "").strip()
                    text_to_embed = body if body else f"{title} {path}"
                else:
                    text_to_embed = f"{title} {path}"

                # Call embed_fn: if it is hashing_embed, pass dim kwarg
                if embed_fn is hashing_embed:
                    vector = hashing_embed(text_to_embed, dim=dim)
                else:
                    vector = embed_fn(text_to_embed)

                docs.append(DenseDoc(doc_id=doc_id, path=path, title=title, vector=vector))

    except Exception:  # noqa: BLE001
        return DenseIndex(dim=dim)

    return DenseIndex(dim=dim, docs=docs)


# ---------------------------------------------------------------------------
# DenseBackend
# ---------------------------------------------------------------------------


class DenseBackend:
    """``RetrievalBackend``-conforming dense vector retrieval backend.

    Wraps a ``DenseIndex`` and an embedding function. On each call it
    embeds the query with ``embed_fn``, searches the index by cosine
    similarity, and maps results to ``Hit`` records whose ``id`` equals
    the ``DenseDoc.doc_id`` (the FTS5 ``documents.id`` rowid) so RRF
    fusion in ``rrf_fuse`` can deduplicate across the FTS5 and dense legs
    by the same integer key.

    Results are returned in best-first (highest cosine first) order as
    required by the ``RetrievalBackend`` Protocol. RRF uses rank order
    only, not the raw score, so returning cosine scores directly is
    correct even though FTS5 uses a different (lower-is-better) score
    convention.

    The ``__call__`` method never raises; any exception returns ``[]``
    to honour the Protocol's failure-soft contract.
    """

    def __init__(
        self,
        index: DenseIndex,
        *,
        embed_fn: EmbedFn = hashing_embed,
    ) -> None:
        self._index = index
        self._embed_fn = embed_fn

    def __call__(self, query: str, limit: int) -> list[Hit]:
        """Search the dense index for *query* and return up to *limit* hits.

        Args:
            query: normalized query string (already processed by the
                   pipeline's normalizer before reaching this backend).
            limit: maximum number of results to return.

        Returns:
            List of ``Hit`` records with ``source="dense"``, sorted
            best-first. Empty list on any error or empty index.
        """
        try:
            # Use the same dim as the index when calling the default embedder
            if self._embed_fn is hashing_embed:
                q_vec = hashing_embed(query, dim=self._index.dim)
            else:
                q_vec = self._embed_fn(query)

            pairs = self._index.search(q_vec, limit)
            return [
                Hit(
                    id=doc.doc_id,
                    path=doc.path,
                    title=doc.title,
                    score=score,
                    source="dense",
                )
                for doc, score in pairs
            ]
        except Exception:  # noqa: BLE001
            return []
