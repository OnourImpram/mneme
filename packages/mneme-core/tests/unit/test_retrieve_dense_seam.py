"""P2-4: the pluggable dense/KG retrieval seam in ``rrf.retrieve``.

mneme ships FTS5 only. Dense vector and temporal-KG retrieval are
optional, injected backends that fuse with FTS5 via RRF and fail soft.
These tests prove the seam works, so a dense backend can be added later
without touching the pipeline, and assert that no default dense backend
is wired (turning dense on is gated on the eval harness, gap P2-4).
"""

from __future__ import annotations

from pathlib import Path

from mneme_core.retrieval.rrf import Hit, RetrievalConfig, retrieve

_QUERY = "a sufficiently long query string for the gate"


def _cfg(tmp_path: Path) -> RetrievalConfig:
    # Point at a missing DB so the FTS5 leg returns [] and each test
    # isolates the behaviour of the injected backend(s).
    return RetrievalConfig(fts5_db=tmp_path / "missing.db", min_query_length=0)


def test_dense_backend_hits_flow_through(tmp_path: Path) -> None:
    def dense(query: str, limit: int) -> list[Hit]:
        return [
            Hit(id="d1", path="dense/only.md", title="Dense", score=0.9, source="dense")
        ]

    results = retrieve(_QUERY, _cfg(tmp_path), dense_backend=dense)
    assert [h.path for h in results] == ["dense/only.md"]
    assert results[0].sources == ["dense"]


def test_dense_and_kg_sources_merge(tmp_path: Path) -> None:
    def dense(query: str, limit: int) -> list[Hit]:
        return [Hit(id="shared", path="x.md", title="X", score=1.0, source="dense")]

    def kg(query: str, limit: int) -> list[Hit]:
        return [Hit(id="shared", path="x.md", title="X", score=1.0, source="kg")]

    results = retrieve(_QUERY, _cfg(tmp_path), dense_backend=dense, kg_backend=kg)
    assert len(results) == 1
    assert results[0].sources == ["dense", "kg"]


def test_failing_backend_is_soft(tmp_path: Path) -> None:
    def boom(query: str, limit: int) -> list[Hit]:
        raise RuntimeError("backend down")

    # Must not raise; the FTS5 leg is empty (missing db), so result is [].
    assert retrieve(_QUERY, _cfg(tmp_path), dense_backend=boom) == []


def test_no_default_dense_backend(tmp_path: Path) -> None:
    # Honesty guard (P2-4): retrieve without a dense backend must not
    # silently invoke one. Missing db + no backend yields no hits.
    assert retrieve(_QUERY, _cfg(tmp_path)) == []
