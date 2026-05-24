/**
 * Test fixture builder: creates an FTS5 database that mirrors the
 * `mneme_core.fts5.indexer` schema in pure TS for self-contained vitest
 * runs without spawning Python.
 *
 * The schema string is intentionally a near-verbatim copy of the
 * Python source. Drift detection lives in `tests/schema-parity.test.ts`
 * which compares this snapshot against the canonical Python schema.
 *
 * Synthetic docs are passed pre-normalized (the test owns normalization
 * so we can exercise specific dotted-vs-dotless cases without
 * importing the locale module here).
 */

import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

export const TEST_SCHEMA_STATEMENTS: string[] = [
  `CREATE TABLE IF NOT EXISTS documents(
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
   )`,
  `CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime)`,
  `CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path)`,
  `CREATE INDEX IF NOT EXISTS idx_documents_frontmatter_type ON documents(frontmatter_type)`,
  `CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
     title,
     content,
     tags,
     linked_notes,
     tokenize='unicode61 remove_diacritics 2'
   )`,
];

export interface TestDoc {
  path: string;
  title: string;
  titleNormalized: string;
  contentRaw: string;
  contentNormalized: string;
  mtime: number;
  tags?: string;
  tagsNormalized?: string;
  frontmatterType?: string;
  sessionId?: string;
  linkedNotes?: string;
  linkedNotesNormalized?: string;
}

export function buildTestDb(dbPath: string, docs: TestDoc[]): void {
  mkdirSync(dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  try {
    for (const stmt of TEST_SCHEMA_STATEMENTS) {
      db.prepare(stmt).run();
    }
    const insertDoc = db.prepare(
      `INSERT INTO documents
       (title, title_normalized, path, content_raw, content_size, mtime,
        tags, frontmatter_type, session_id, linked_notes, indexed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    const insertFts = db.prepare(
      `INSERT INTO documents_fts (rowid, title, content, tags, linked_notes)
       VALUES (?, ?, ?, ?, ?)`,
    );
    const tx = db.transaction(() => {
      for (const d of docs) {
        const info = insertDoc.run(
          d.title,
          d.titleNormalized,
          d.path,
          d.contentRaw,
          d.contentRaw.length,
          d.mtime,
          d.tags ?? "",
          d.frontmatterType ?? "",
          d.sessionId ?? "",
          d.linkedNotes ?? "",
          new Date().toISOString(),
        );
        insertFts.run(
          info.lastInsertRowid,
          d.titleNormalized,
          d.contentNormalized,
          d.tagsNormalized ?? d.tags ?? "",
          d.linkedNotesNormalized ?? d.linkedNotes ?? "",
        );
      }
    });
    tx();
  } finally {
    db.close();
  }
}
