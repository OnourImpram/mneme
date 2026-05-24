"""Operator-fillable ground-truth ParityAdapter template.

Phase J Day 4 of the dogfood week compares mneme retrieval against the
operator's existing memory system. The comparison needs an adapter
implementing the lightweight ``ParityAdapter`` Protocol from
``tools.parity.adapter``. This file is the template for that adapter.

Usage:

1. Copy this file to your vault-private directory, OUTSIDE the mneme
   repo. Example:

       cp tools/parity/template_adapter.py \
          ~/your-vault/_bench/ground_truth_adapter.py

   Keeping the adapter outside the mneme repo keeps your own memory
   system's name and internals out of the public mneme tree.

2. Edit the module so ``_query_ground_truth_system(query, top_k)``
   returns the actual retrieval results from your existing memory
   tool. The method's return shape and an example are documented
   below.

3. Point ``parity_check`` at the adapter via the module-path notation
   ``--ground-truth-adapter your_module:GroundTruthAdapter``. The
   harness imports the module dynamically at runtime, so no
   installation is needed.

4. The adapter is the only operator-authored code in the parity
   pipeline. Everything else (metrics, harness, MnemeParityAdapter)
   is shipped in mneme.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ParityHit:
    """One retrieval hit from the ground-truth system.

    Attributes:
        doc_id: stable identifier the comparison uses to align
            mneme hits with ground-truth hits. Anything that uniquely
            identifies one document in your memory system. URL paths,
            file paths, content hashes, and SQLite row IDs all work.
        score: per-system relevance score in any unit. Used for rank
            order only; the parity metrics never compare scores across
            systems, only positions.
        title: optional human-readable label. Useful when reviewing
            the parity-check JSON output by hand.
        body: optional snippet that justifies the hit. Useful when a
            divergent query needs operator investigation.
    """

    doc_id: str
    score: float
    title: str = ""
    body: str = ""


class GroundTruthAdapter:
    """Operator-authored adapter against your existing memory tool.

    The class name is ``GroundTruthAdapter`` so the parity_check
    invocation looks like:

        --ground-truth-adapter my_module:GroundTruthAdapter

    Rename freely if you prefer. The Protocol only requires the two
    methods ``status`` and ``search``.
    """

    def __init__(self) -> None:
        """Zero-argument constructor. The harness instantiates the
        adapter once and reuses it across queries.

        Initialize whatever connection or in-memory index your tool
        needs here. Keep it cheap; the harness calls ``status`` right
        after construction so a slow init blocks the whole run.
        """
        # TODO: wire your memory tool's session/connection/index here.
        # Example:
        #
        #   self._client = MyMemoryClient(db_path="...")
        #   self._index = self._client.open_index()
        self._ready = True

    def status(self) -> dict[str, Any]:
        """Return a structured status snapshot. The harness calls this
        once at start-up and copies the result into the parity report.

        Recommended keys:

        - ``ready``: boolean. False short-circuits the run.
        - ``backend``: short identifier of the underlying tool
          (use a generic label, not your private system's real name).
        - ``doc_count``: total documents in the index, if cheap.
        - ``version``: backend version string, if available.
        """
        return {
            "ready": self._ready,
            "backend": "your-system-here",
            "doc_count": 0,
            "version": "unknown",
        }

    def search(self, query: str, top_k: int = 10) -> list[ParityHit]:
        """Run one query against your memory tool and return ranked hits.

        Args:
            query: free-text query string. Already redacted; no
                ``<private>`` tags should appear here.
            top_k: maximum number of hits to return. The harness uses
                10 by default. Returning fewer is fine.

        Returns:
            A list of ``ParityHit`` in descending relevance order.
            Length must be at most ``top_k``. Empty list is allowed
            when your system finds nothing; the parity metrics
            handle empty results correctly.

        Example: a system that has its own ``run_query`` method might
        wrap it like this:

            def search(self, query, top_k=10):
                raw = self._client.run_query(query, limit=top_k)
                return [
                    ParityHit(
                        doc_id=row["path"],
                        score=row["relevance"],
                        title=row.get("title", ""),
                        body=row.get("snippet", ""),
                    )
                    for row in raw
                ]
        """
        # TODO: replace this stub with your real retrieval call.
        results = self._query_ground_truth_system(query, top_k)
        return [
            ParityHit(
                doc_id=row["doc_id"],
                score=float(row.get("score", 0.0)),
                title=row.get("title", ""),
                body=row.get("body", ""),
            )
            for row in results
        ]

    def _query_ground_truth_system(
        self, query: str, top_k: int
    ) -> list[dict[str, Any]]:
        """The ONE method you actually need to write.

        Returns a list of dicts shaped like::

            [
                {"doc_id": "abs/path/or/id", "score": 0.91, "title": "...", "body": "..."},
                ...
            ]

        Order matters: position 0 is the top hit.

        Empty list is valid when your system has no answer.

        Any exception raised here surfaces as a failed query in the
        parity report. Catch internal errors and return ``[]`` if you
        prefer silent failure over loud failure.
        """
        # TODO: implement against your memory tool. Examples:
        #
        # - REST endpoint:
        #     resp = requests.post(self._url, json={"q": query, "k": top_k})
        #     return resp.json()["hits"]
        #
        # - Local SQLite:
        #     rows = self._conn.execute(
        #         "SELECT path AS doc_id, bm25 AS score, title, snippet AS body "
        #         "FROM idx WHERE idx MATCH ? LIMIT ?",
        #         (query, top_k),
        #     ).fetchall()
        #     return [dict(row) for row in rows]
        #
        # - In-memory index:
        #     return self._index.search(query, top_k)
        raise NotImplementedError(
            "Implement _query_ground_truth_system against your memory tool. "
            "Read the docstring above for the expected return shape."
        )
