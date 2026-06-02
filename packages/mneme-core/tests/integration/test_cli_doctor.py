"""Integration tests for doctor checks 7-10 (Phase 1 lane-A additions).

CHECK 7 — privacy_index: fail when content_raw contains raw <private> tag.
CHECK 8 — locale_profile: na when index_meta absent; warn when profile missing or wrong.
CHECK 9 — type_parity: ok when KNOWN_TYPES matches canonical_memory_types.json.
CHECK 10 — content_hash_coverage: warn when any row has content_hash IS NULL.

All tests seed a real (tmp) SQLite database directly so they do not depend on
the vault config, index rebuild, or any network access.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.fts5.indexer import (
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# Helpers shared across checks
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / ".mneme").mkdir()
    return vault_root


def _open_rw(db_path: Path) -> sqlite3.Connection:
    """Open db read-write (for seeding)."""
    return sqlite3.connect(db_path)


def _run_doctor(tmp_path: Path, vault_root: Path) -> dict:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
    assert result.exit_code in (0, 1), f"Unexpected exit code: {result.exit_code}\n{result.output}"
    return json.loads(result.output)


def _check(data: dict, name: str) -> dict:
    found = [c for c in data["checks"] if c["name"] == name]
    assert found, f"check {name!r} not found in {[c['name'] for c in data['checks']]}"
    return found[0]


def _build_index_with_schema(vault_root: Path) -> Path:
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
# CHECK 7 — privacy_index
# ---------------------------------------------------------------------------

class TestPrivacyIndexCheck:
    """Check 7: any row with literal <private in content_raw is a P3 violation."""

    def test_check_present_in_output(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        names = {c["name"] for c in data["checks"]}
        assert "privacy_index" in names

    def test_fail_when_raw_private_tag_in_content_raw(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)

        # Seed a row with a raw <private> tag that was NOT redacted.
        conn = _open_rw(db_path)
        try:
            conn.execute(
                "INSERT INTO documents (title, path, content_raw, content_size, mtime, indexed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ("Leaked", "leaked.md", "<private>secret</private>", 30, 1.0, "2026-01-01"),
            )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "privacy_index")
        assert c["status"] == "fail", f"Expected fail, got: {c}"

    def test_ok_when_no_raw_private_tags(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        # Write a normal redacted note and index it.
        (vault_root / "clean.md").write_text(
            "---\nid: clean\ntype: session\n---\n# Clean\n\n[REDACTED] body\n",
            encoding="utf-8",
        )
        _build_index_with_schema(vault_root)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "privacy_index")
        assert c["status"] == "ok", f"Expected ok, got: {c}"

    def test_na_when_no_index(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        # No index built — check must not crash; it should be na.
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "privacy_index")
        assert c["status"] == "na"

    def test_fail_detail_mentions_count(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)
        conn = _open_rw(db_path)
        try:
            for i in range(2):
                conn.execute(
                    "INSERT INTO documents"
                    " (title, path, content_raw, content_size, mtime, indexed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (f"L{i}", f"l{i}.md", f"<private>s{i}</private>", 20, float(i), "2026-01-01"),
                )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "privacy_index")
        assert c["status"] == "fail"
        assert "2" in c["detail"]


# ---------------------------------------------------------------------------
# CHECK 8 — locale_profile
# ---------------------------------------------------------------------------

class TestLocaleProfileCheck:
    """Check 8: index_meta must carry a normalization_profile."""

    def test_check_present_in_output(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _build_index_with_schema(vault_root)
        data = _run_doctor(tmp_path, vault_root)
        names = {c["name"] for c in data["checks"]}
        assert "locale_profile" in names

    def test_na_when_index_meta_table_absent(self, tmp_path: Path) -> None:
        """index_meta table not present at all → na (legacy index)."""
        vault_root = _make_vault(tmp_path)
        vault = VaultConfig.from_path(vault_root)
        db_path = vault.fts5_db
        # Build a minimal DB without index_meta.
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    title_normalized TEXT,
                    path TEXT UNIQUE NOT NULL,
                    content_raw TEXT,
                    body_text TEXT,
                    content_size INTEGER,
                    mtime REAL,
                    tags TEXT,
                    frontmatter_type TEXT,
                    session_id TEXT,
                    linked_notes TEXT,
                    schema_version TEXT DEFAULT '2',
                    language TEXT DEFAULT 'en',
                    indexed_at TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "locale_profile")
        assert c["status"] == "na", f"Expected na, got: {c}"

    def test_warn_when_normalization_profile_row_absent(self, tmp_path: Path) -> None:
        """index_meta table exists but has no normalization_profile key → warn."""
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)

        # Remove the normalization_profile row that ensure_schema/index_vault writes.
        conn = _open_rw(db_path)
        try:
            conn.execute("DELETE FROM index_meta WHERE key='normalization_profile'")
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "locale_profile")
        assert c["status"] == "warn", f"Expected warn, got: {c}"

    def test_warn_when_profile_value_unexpected(self, tmp_path: Path) -> None:
        """A stored profile value not in ('tr-cldr', 'identity', 'tr-ascii-fold') → warn."""
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)

        conn = _open_rw(db_path)
        try:
            conn.execute(
                "INSERT INTO index_meta(key, value) VALUES('normalization_profile', 'wrong')"
                " ON CONFLICT(key) DO UPDATE SET value='wrong'"
            )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "locale_profile")
        assert c["status"] == "warn", f"Expected warn, got: {c}"

    def test_ok_when_identity_profile_stored(self, tmp_path: Path) -> None:
        """Default index (identity normalizer) stores 'identity' → ok."""
        vault_root = _make_vault(tmp_path)
        _build_index_with_schema(vault_root)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "locale_profile")
        assert c["status"] == "ok", f"Expected ok, got: {c}"
        assert "identity" in c["detail"]

    def test_na_when_no_index(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "locale_profile")
        assert c["status"] == "na"


# ---------------------------------------------------------------------------
# CHECK 9 — type_parity
# ---------------------------------------------------------------------------

class TestTypeParity:
    """Check 9: KNOWN_TYPES must equal types in canonical_memory_types.json."""

    def test_check_present_in_output(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        names = {c["name"] for c in data["checks"]}
        assert "type_parity" in names

    def test_ok_when_types_match(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "type_parity")
        assert c["status"] == "ok", f"Expected ok, got: {c}"

    def test_check_always_runs_without_index(self, tmp_path: Path) -> None:
        """type_parity does not depend on the DB; it runs even with no index."""
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "type_parity")
        # No index → the check still runs (it only reads the JSON fixture).
        assert c["status"] in ("ok", "fail")

    def test_na_when_fixture_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When canonical_memory_types.json is not present (installed package),
        type_parity must return na, not warn."""
        # Patch the fixture path inside the doctor command to a nonexistent file.
        original_exists = Path.exists

        def _patched_exists(self: Path) -> bool:
            if self.name == "canonical_memory_types.json":
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", _patched_exists)

        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "type_parity")
        assert c["status"] == "na", f"Expected na when fixture absent, got: {c}"
        assert "fixture" in c["detail"].lower()


# ---------------------------------------------------------------------------
# CHECK 10 — content_hash_coverage
# ---------------------------------------------------------------------------

class TestContentHashCoverage:
    """Check 10: warn when any document row has content_hash IS NULL."""

    def test_check_present_in_output(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        _build_index_with_schema(vault_root)
        data = _run_doctor(tmp_path, vault_root)
        names = {c["name"] for c in data["checks"]}
        assert "content_hash_coverage" in names

    def test_warn_when_content_hash_null(self, tmp_path: Path) -> None:
        """A row with content_hash=NULL triggers warn."""
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)

        conn = _open_rw(db_path)
        try:
            # Insert a row with NULL content_hash to simulate a pre-migration row.
            conn.execute(
                "INSERT INTO documents (title, path, content_raw, content_size, mtime, indexed_at,"
                " content_hash) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                ("NullHash", "nullhash.md", "body", 4, 1.0, "2026-01-01"),
            )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "content_hash_coverage")
        assert c["status"] == "warn", f"Expected warn, got: {c}"

    def test_ok_when_all_hashes_present(self, tmp_path: Path) -> None:
        """All rows having a non-NULL content_hash → ok."""
        vault_root = _make_vault(tmp_path)
        (vault_root / "doc.md").write_text(
            "---\nid: d\ntype: session\n---\n# Doc\n\nbody\n",
            encoding="utf-8",
        )
        _build_index_with_schema(vault_root)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "content_hash_coverage")
        # After Phase 1 migration, content_hash is populated by index_vault.
        assert c["status"] == "ok", f"Expected ok, got: {c}"

    def test_na_when_content_hash_column_absent(self, tmp_path: Path) -> None:
        """When the column doesn't exist yet (very old schema) → na."""
        vault_root = _make_vault(tmp_path)
        vault = VaultConfig.from_path(vault_root)
        db_path = vault.fts5_db

        # Build a DB without the content_hash column.
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    path TEXT UNIQUE NOT NULL,
                    content_raw TEXT,
                    content_size INTEGER,
                    mtime REAL,
                    indexed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO index_meta(key, value) VALUES('normalization_profile', 'identity');
                """
            )
            conn.execute(
                "INSERT INTO documents (title, path, content_raw, content_size, mtime, indexed_at)"
                " VALUES ('T', 'x.md', 'body', 4, 1.0, '2026-01-01')"
            )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "content_hash_coverage")
        assert c["status"] == "na", f"Expected na, got: {c}"

    def test_na_when_no_index(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "content_hash_coverage")
        assert c["status"] == "na"

    def test_warn_detail_mentions_count(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        db_path = _build_index_with_schema(vault_root)

        conn = _open_rw(db_path)
        try:
            for i in range(3):
                conn.execute(
                    "INSERT INTO documents"
                    " (title, path, content_raw, content_size, mtime, indexed_at,"
                    " content_hash) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (f"N{i}", f"n{i}.md", "body", 4, float(i), "2026-01-01"),
                )
            conn.commit()
        finally:
            conn.close()

        data = _run_doctor(tmp_path, vault_root)
        c = _check(data, "content_hash_coverage")
        assert c["status"] == "warn"
        assert "3" in c["detail"]
