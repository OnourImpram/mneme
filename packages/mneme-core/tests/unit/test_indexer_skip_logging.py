"""Skipped files must name themselves in the log.

WHY THIS EXISTS
``IndexStats.skipped_error`` counted dropped files but nothing recorded WHICH
ones. Measured on a real vault, a rebuild reported ``skipped_error: 86`` and
answering "which 86?" required writing a separate script that re-walked the
vault and re-ran the indexer's own filters. A count with no evidence is not
actionable, and a detector ships with its remedy.

WHAT IS PINNED
Each skip path emits a WARNING naming the file and the cause, and — the half
that is easy to forget — a healthy file emits nothing. A logger that warned on
every document would satisfy the positive assertions alone while making the
signal useless, so the negative control carries as much weight here as the
positive one.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def _index(root: Path):
    db_path = root / ".mneme" / "fts5.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        return index_vault(
            conn,
            IndexerConfig(
                vault_root=root,
                db_path=db_path,
                normalize=normalize_tr,
                normalize_for_fts=normalize_tr_for_fts,
            ),
        )
    finally:
        conn.close()


def test_malformed_frontmatter_is_named_in_the_log(
    tmp_path: Path, caplog
) -> None:
    """The dominant real-world cause: YAML that will not parse."""
    root = _vault(tmp_path)
    # An unquoted scalar containing ": " — 47 of the 86 real failures.
    (root / "broken.md").write_text(
        "---\ndescription: a note: with a colon inside\n---\n\nbody\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="mneme_core.fts5.indexer"):
        stats = _index(root)

    assert stats.skipped_error == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("broken.md" in m for m in messages), messages
    # The cause must be named too, not just the path.
    assert any("DocumentScopeError" in m for m in messages), messages


def test_log_states_the_exception_type_so_causes_can_be_grouped(
    tmp_path: Path, caplog
) -> None:
    root = _vault(tmp_path)
    (root / "a.md").write_text(
        "---\nilgili: [[A]], [[B]]\n---\n\nbody\n", encoding="utf-8"
    )
    (root / "b.md").write_text(
        "---\ntarih: {{tarih}}\n---\n\nbody\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="mneme_core.fts5.indexer"):
        stats = _index(root)

    assert stats.skipped_error == 2
    messages = [r.getMessage() for r in caplog.records]
    assert sum("index skip" in m for m in messages) == 2, messages


def test_negative_control_a_healthy_vault_logs_nothing(
    tmp_path: Path, caplog
) -> None:
    """A logger that fires on every file would make the signal worthless."""
    root = _vault(tmp_path)
    (root / "fine.md").write_text(
        "---\ntitle: Fine\n---\n\nordinary body text\n", encoding="utf-8"
    )
    (root / "also-fine.md").write_text("no frontmatter at all\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="mneme_core.fts5.indexer"):
        stats = _index(root)

    assert stats.skipped_error == 0
    assert stats.indexed == 2
    assert [r.getMessage() for r in caplog.records] == []


def test_negative_control_excluded_files_are_not_logged_as_errors(
    tmp_path: Path, caplog
) -> None:
    """Exclusion is routine, not a fault; only genuine skips are warnings."""
    root = _vault(tmp_path)
    node_modules = root / "node_modules"
    node_modules.mkdir()
    (node_modules / "readme.md").write_text("vendor\n", encoding="utf-8")
    (root / "kept.md").write_text("kept\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="mneme_core.fts5.indexer"):
        stats = _index(root)

    assert stats.skipped_excluded >= 1
    assert stats.skipped_error == 0
    assert [r.getMessage() for r in caplog.records] == []
