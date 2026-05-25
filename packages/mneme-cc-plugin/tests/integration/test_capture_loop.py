"""End-to-end regression test for the default capture loop.

Before the fix, the Stop hook wrote the daily session log with no YAML
frontmatter, so the indexer recorded ``frontmatter_type=''`` while
SessionStart queried ``WHERE frontmatter_type='session'``. The two
never matched, so the recent-sessions context block was always empty
on a default install. This test drives the real producer (Stop) and
the real consumer (SessionStart) through a real FTS5 index to prove
the loop is closed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from mneme_core.fts5.indexer import (
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.vault import frontmatter
from mneme_core.vault.config import VaultConfig

from mneme_cc_plugin.hooks import session_start, stop


def test_capture_loop_session_is_discoverable(tmp_path: Path) -> None:
    vault = VaultConfig.from_path(tmp_path)

    # Producer: a real Stop run. A non-git tmp vault is never treated as
    # an empty session, so the handler writes the daily log.
    stop.handle({"session_id": "sess-cap-1", "transcript_path": ""}, vault)

    today = date.today().isoformat()
    session_file = tmp_path / "sessions" / f"{today}.md"
    assert session_file.exists()

    # Producer contract: the log now carries type: session frontmatter.
    fm, _body = frontmatter.parse(session_file.read_text(encoding="utf-8"))
    assert fm is not None
    assert fm.type == "session"

    # Index into the real vault db so the real consumer can read it.
    vault.state_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(vault.fts5_db)
    try:
        ensure_schema(conn)
        index_vault(
            conn, IndexerConfig(vault_root=tmp_path, db_path=vault.fts5_db)
        )
    finally:
        conn.close()

    # Consumer: SessionStart's recent-sessions block only emits its
    # header when the frontmatter_type='session' query returns rows.
    block = session_start._block_recent_sessions(vault)
    assert "Most recent session documents" in block
    assert today in block
