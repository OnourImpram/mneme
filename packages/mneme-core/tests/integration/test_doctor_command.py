"""Integration tests for the ``mneme doctor`` CLI command.

Uses ``click.testing.CliRunner`` with a minimal tmp_path vault so no
real user vault is ever touched. Follows the same patterns used in the
existing integration tests (connect / ensure_schema / index_vault directly).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.fts5.indexer import (
    SCHEMA_VERSION,
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault root with the .mneme marker directory."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / ".mneme").mkdir()
    return vault_root


def _add_md_file(vault_root: Path, name: str = "note.md") -> Path:
    """Write a small markdown file into the vault root."""
    p = vault_root / name
    p.write_text(
        "---\nid: test-note\ntype: session\ntags: alpha\n---\n# Test Note\n\nbody\n",
        encoding="utf-8",
    )
    return p


def _build_index(vault_root: Path) -> Path:
    """Create and populate an FTS5 index for *vault_root*."""
    vault = VaultConfig.from_path(vault_root)
    db_path = vault.fts5_db
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        cfg = IndexerConfig(vault_root=vault_root, db_path=db_path)
        index_vault(conn, cfg)
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDoctorOutputShape:
    def test_output_has_overall_and_checks_keys(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        assert "overall" in data
        assert "checks" in data

    def test_checks_is_list_of_dicts(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        assert isinstance(data["checks"], list)
        for check in data["checks"]:
            assert "name" in check
            assert "status" in check
            assert "detail" in check

    def test_all_expected_check_names_present(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        names = {c["name"] for c in data["checks"]}
        expected = {
            "vault_resolves",
            "index_present",
            "index_schema",
            "index_freshness",
            "compression_config",
        }
        assert expected.issubset(names)


class TestDoctorNoIndex:
    """When no FTS5 index exists the command should warn, not fail."""

    def test_exit_code_zero_on_warn(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        # Missing index => warn overall => exit 0
        assert result.exit_code == 0

    def test_overall_is_warn_when_index_missing(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        assert data["overall"] == "warn"

    def test_index_present_check_is_warn(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        ip = next(c for c in data["checks"] if c["name"] == "index_present")
        assert ip["status"] == "warn"

    def test_index_schema_is_na_when_index_missing(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        s = next(c for c in data["checks"] if c["name"] == "index_schema")
        assert s["status"] == "na"

    def test_index_freshness_is_na_when_index_missing(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        f = next(c for c in data["checks"] if c["name"] == "index_freshness")
        assert f["status"] == "na"


class TestDoctorWithIndex:
    """When a populated FTS5 index exists the command should report ok."""

    def test_exit_code_zero_on_ok(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        assert result.exit_code == 0

    def test_overall_is_ok_with_populated_index(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        assert data["overall"] == "ok"

    def test_index_present_check_is_ok(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        ip = next(c for c in data["checks"] if c["name"] == "index_present")
        assert ip["status"] == "ok"

    def test_index_schema_check_is_ok(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        s = next(c for c in data["checks"] if c["name"] == "index_schema")
        assert s["status"] == "ok"
        assert SCHEMA_VERSION in s["detail"]

    def test_index_freshness_ok_with_rows(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        f = next(c for c in data["checks"] if c["name"] == "index_freshness")
        assert f["status"] == "ok"
        assert "documents=1" in f["detail"]

    def test_vault_resolves_check_is_ok(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _build_index(vault_root)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        vr = next(c for c in data["checks"] if c["name"] == "vault_resolves")
        assert vr["status"] == "ok"


class TestDoctorEmptyIndex:
    """An index that exists but has zero rows triggers a freshness warning."""

    def test_empty_index_freshness_is_warn(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        # Build the index structure but add no markdown files.
        vault = VaultConfig.from_path(vault_root)
        conn = connect(vault.fts5_db)
        try:
            ensure_schema(conn)
            # index_vault with empty vault => 0 rows
            cfg = IndexerConfig(vault_root=vault_root, db_path=vault.fts5_db)
            index_vault(conn, cfg)
        finally:
            conn.close()

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        f = next(c for c in data["checks"] if c["name"] == "index_freshness")
        assert f["status"] == "warn"


class TestDoctorSchemaVersionMismatch:
    """An index with a stale schema_version triggers an index_schema warning."""

    def test_stale_schema_version_is_warn(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _add_md_file(vault_root)
        _build_index(vault_root)

        # Force the stored schema_version to a fake old value.
        vault = VaultConfig.from_path(vault_root)
        conn = sqlite3.connect(vault.fts5_db)
        try:
            conn.execute("UPDATE documents SET schema_version='0'")
            conn.commit()
        finally:
            conn.close()

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        data = json.loads(result.output)
        s = next(c for c in data["checks"] if c["name"] == "index_schema")
        assert s["status"] == "warn"
        assert "rebuild" in s["detail"]
