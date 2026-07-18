"""CLI surface for ``temporal blame`` and ``temporal contradictions``."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.temporal.index import ensure_temporal_schema
from mneme_core.vault.config import VaultConfig


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
        "scope": kw.get("scope", "default"),
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO claims ({cols}) VALUES ({marks})", list(row.values()))


@pytest.fixture()
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    v.state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(v.fts5_db)
    ensure_temporal_schema(conn)
    _insert(conn, "c1", observed_at="2026-01-01T00:00:00+00:00")
    _insert(
        conn,
        "c2",
        supersedes="c1",
        observed_at="2026-02-01T00:00:00+00:00",
        claim_key="user.location",
        valid_from="2026-02-01T00:00:00+00:00",
    )
    _insert(
        conn,
        "c3",
        claim_key="user.location",
        observed_at="2026-02-15T00:00:00+00:00",
        valid_from="2026-02-10T00:00:00+00:00",
    )
    conn.commit()
    conn.close()
    return v


class TestTemporalBlameCli:
    def test_blame_by_claim_id(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["temporal", "blame", "c2", "--vault", str(vault.root)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] == 1
        report = data["reports"][0]
        assert report["target"]["claim_id"] == "c2"
        assert [c["claim_id"] for c in report["ancestors"]] == ["c1"]
        assert [c["claim_id"] for c in report["rivals"]] == ["c3"]
        assert report["target"]["scope"] == "default"

    def test_blame_by_path(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["temporal", "blame", "notes/c1.md", "--vault", str(vault.root)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] == 1
        assert data["reports"][0]["target"]["claim_id"] == "c1"
        assert [c["claim_id"] for c in data["reports"][0]["descendants"]] == ["c2"]

    def test_blame_unknown_ref_empty(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["temporal", "blame", "ghost", "--vault", str(vault.root)]
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["count"] == 0

    def test_blame_without_index(self, tmp_path: Path) -> None:
        (tmp_path / ".mneme").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            cli, ["temporal", "blame", "c1", "--vault", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["note"] == "index not found"


class TestTemporalContradictionsCli:
    def test_contradiction_pair_reported(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["temporal", "contradictions", "--vault", str(vault.root)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] >= 1
        ids = {data["contradictions"][0]["a"]["claim_id"],
               data["contradictions"][0]["b"]["claim_id"]}
        assert ids == {"c2", "c3"}

    def test_wildcard_preserves_equal_pair_identity_per_scope(
        self, vault: VaultConfig
    ) -> None:
        conn = sqlite3.connect(vault.fts5_db)
        _insert(
            conn,
            "c2",
            scope="clinical",
            claim_key="user.location",
            observed_at="2026-02-01T00:00:00+00:00",
        )
        _insert(
            conn,
            "c3",
            scope="clinical",
            claim_key="user.location",
            observed_at="2026-02-15T00:00:00+00:00",
        )
        conn.commit()
        conn.close()

        result = CliRunner().invoke(
            cli,
            [
                "temporal",
                "contradictions",
                "--vault",
                str(vault.root),
                "--scope",
                "*",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["scope"] == "*"
        assert data["count"] == 2
        assert {item["scope"] for item in data["contradictions"]} == {
            "default",
            "clinical",
        }


class TestTemporalSnapshotScopeCli:
    def test_as_of_uses_configured_default_scope(self, vault: VaultConfig) -> None:
        conn = sqlite3.connect(vault.fts5_db)
        _insert(
            conn,
            "clinical-only",
            scope="clinical",
            observed_at="2026-01-15T00:00:00+00:00",
        )
        conn.commit()
        conn.close()

        result = CliRunner().invoke(
            cli,
            [
                "temporal",
                "as-of",
                "2026-03-01T00:00:00+00:00",
                "--vault",
                str(vault.root),
            ],
            env={"MNEME_SCOPE": "clinical"},
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["scope"] == "clinical"
        assert {claim["claim_id"] for claim in data["claims"]} == {
            "clinical-only"
        }
        assert {claim["scope"] for claim in data["claims"]} == {"clinical"}

    def test_invalid_scope_is_rejected(self, vault: VaultConfig) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "temporal",
                "current",
                "--vault",
                str(vault.root),
                "--scope",
                " invalid ",
            ],
        )
        assert result.exit_code != 0
        assert "scope must be a valid identifier" in result.output
