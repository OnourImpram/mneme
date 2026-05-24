"""Tiny FTS5 test DB builder mirroring the indexer schema.

Used by hook tests that need a populated database without spawning
the Python indexer over a real markdown corpus.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  title_normalized TEXT,
  path TEXT UNIQUE NOT NULL,
  content_raw TEXT,
  content_size INTEGER,
  mtime REAL,
  tags TEXT,
  frontmatter_type TEXT,
  session_id TEXT,
  linked_notes TEXT,
  schema_version TEXT DEFAULT '1',
  language TEXT DEFAULT 'en',
  indexed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title, content, tags, linked_notes,
  tokenize='unicode61 remove_diacritics 2'
);
"""


def build_minimal_db(db_path: Path, docs: list[dict[str, Any]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        for d in docs:
            cur = conn.execute(
                "INSERT INTO documents "
                "(title, title_normalized, path, content_raw, content_size, "
                " mtime, tags, frontmatter_type, session_id, linked_notes, "
                " indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '', ?, '', '', '2026-05-19T00:00:00Z')",
                (
                    d.get("title", ""),
                    d.get("title", "").lower(),
                    d["path"],
                    d.get("content", ""),
                    len(d.get("content", "")),
                    float(d.get("mtime", 0.0)),
                    d.get("type", ""),
                ),
            )
            conn.execute(
                "INSERT INTO documents_fts (rowid, title, content, tags, linked_notes) "
                "VALUES (?, ?, ?, '', '')",
                (cur.lastrowid, d.get("title", ""), d.get("content", "")),
            )
        conn.commit()
    finally:
        conn.close()
