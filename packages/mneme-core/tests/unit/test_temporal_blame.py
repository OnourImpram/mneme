"""Blame lineage walks over the claims table (memory time-travel)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from mneme_core.temporal.blame import blame
from mneme_core.temporal.index import ensure_temporal_schema


def _insert(conn: sqlite3.Connection, claim_id: str, **kw: object) -> None:
    row = {
        "claim_id": claim_id,
        "path": kw.get("path", f"notes/{claim_id}.md"),
        "statement": kw.get("statement", f"statement {claim_id}"),
        "statement_normalized": kw.get("statement", f"statement {claim_id}"),
        "valid_from": kw.get("valid_from"),
        "valid_to": kw.get("valid_to"),
        "observed_at": kw.get("observed_at", datetime.now(UTC).isoformat()),
        "supersedes": kw.get("supersedes"),
        "superseded_by": kw.get("superseded_by"),
        "claim_key": kw.get("claim_key"),
        "confidence_label": "EXTRACTED",
        "trust": "user",
        "content_hash": "0" * 64,
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO claims ({cols}) VALUES ({marks})", list(row.values()))


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    ensure_temporal_schema(c)
    return c


class TestBlame:
    def test_missing_table_returns_empty(self) -> None:
        bare = sqlite3.connect(":memory:")
        assert blame(bare, "anything") == []

    def test_unknown_ref_returns_empty(self, conn: sqlite3.Connection) -> None:
        assert blame(conn, "nope") == []

    def test_supersession_chain_both_directions(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "c1", observed_at="2026-01-01T00:00:00+00:00")
        _insert(conn, "c2", supersedes="c1", observed_at="2026-02-01T00:00:00+00:00")
        _insert(conn, "c3", supersedes="c2", observed_at="2026-03-01T00:00:00+00:00")
        reports = blame(conn, "c2")
        assert len(reports) == 1
        rep = reports[0]
        assert rep.target.claim_id == "c2"
        assert [c.claim_id for c in rep.ancestors] == ["c1"]
        assert [c.claim_id for c in rep.descendants] == ["c3"]

    def test_path_ref_returns_all_claims_in_file(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "a1", path="facts/loc.md", observed_at="2026-01-01T00:00:00+00:00")
        _insert(conn, "a2", path="facts/loc.md", observed_at="2026-01-02T00:00:00+00:00")
        reports = blame(conn, "facts/loc.md")
        assert [r.target.claim_id for r in reports] == ["a1", "a2"]

    def test_backslash_path_normalized(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "w1", path="facts/loc.md")
        assert len(blame(conn, "facts\\loc.md")) == 1

    def test_rivals_share_claim_key(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "k1", claim_key="user.location")
        _insert(conn, "k2", claim_key="user.location")
        _insert(conn, "k3", claim_key="user.role")
        rep = blame(conn, "k1")[0]
        assert [c.claim_id for c in rep.rivals] == ["k2"]

    def test_cyclic_supersedes_terminates(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "x1", supersedes="x2")
        _insert(conn, "x2", supersedes="x1")
        rep = blame(conn, "x1")[0]
        # Walks terminate without infinite loop; each side sees the other once.
        assert [c.claim_id for c in rep.ancestors] == ["x2"]
        assert [c.claim_id for c in rep.descendants] == ["x2"]

    def test_self_supersedes_terminates(self, conn: sqlite3.Connection) -> None:
        _insert(conn, "s1", supersedes="s1")
        rep = blame(conn, "s1")[0]
        assert rep.ancestors == []
        assert rep.descendants == []
