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

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import Database from "better-sqlite3";

export const TEST_SCHEMA_STATEMENTS: string[] = [
	`CREATE TABLE IF NOT EXISTS documents(
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
     scope TEXT NOT NULL DEFAULT 'default',
     linked_notes TEXT,
     schema_version TEXT DEFAULT '4',
     language TEXT DEFAULT 'en',
     indexed_at TEXT,
     content_hash TEXT,
     trust TEXT,
     valid_from TEXT,
     valid_until TEXT
   )`,
	`CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime)`,
	`CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path)`,
	`CREATE INDEX IF NOT EXISTS idx_documents_frontmatter_type ON documents(frontmatter_type)`,
	`CREATE INDEX IF NOT EXISTS idx_documents_scope ON documents(scope)`,
	`CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
     title,
     content,
     tags,
     linked_notes,
     path_tokens,
     tokenize='unicode61 remove_diacritics 2'
   )`,
	`CREATE VIRTUAL TABLE IF NOT EXISTS documents_ascii_fts USING fts5(
     title,
     content,
     tags,
     linked_notes,
     path_tokens,
     tokenize='unicode61 remove_diacritics 2'
   )`,
	`CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`,
];

export interface TestDoc {
	path: string;
	title: string;
	titleNormalized: string;
	contentRaw: string;
	/** Document body with frontmatter stripped. Defaults to contentRaw. */
	bodyText?: string;
	contentNormalized: string;
	/** Optional Turkish ASCII-i-fold keys. Defaults to the primary normalized values. */
	titleAsciiNormalized?: string;
	contentAsciiNormalized?: string;
	mtime: number;
	tags?: string;
	tagsNormalized?: string;
	tagsAsciiNormalized?: string;
	frontmatterType?: string;
	sessionId?: string;
	/** Scope dimension (frontmatter scope: → project: → 'default'). */
	scope?: string;
	linkedNotes?: string;
	linkedNotesNormalized?: string;
	linkedNotesAsciiNormalized?: string;
	/** SHA-256 hex digest of redacted UTF-8 content. Empty string for legacy fixture rows. */
	contentHash?: string;
	/** Origin trust label. Defaults to 'user'. */
	trust?: string;
	/**
	 * Tokenised path. Defaults to the document path split on vault separators,
	 * mirroring `mneme_core.fts5.indexer._path_tokens`.
	 */
	pathTokens?: string;
}

/**
 * Split a vault path into searchable tokens.
 *
 * Mirrors the Python indexer. The hyphen is last in the character class so it
 * stays a literal instead of forming a range — writing it mid-class silently
 * stops splitting on the separator vault filenames use most.
 */
function defaultPathTokens(path: string): string {
	return path
		.split(/[/\_.\s-]+/)
		.filter((part) => part.length > 0)
		.join(" ")
		.toLowerCase();
}

/**
 * Which locale profile the fixture index should declare.
 *
 * This used to be hardcoded to Turkish, and that is precisely why a whole
 * class of defect went unmeasured: every fixture was a Turkish index, so
 * three tools that only worked on Turkish indexes passed every test. An
 * English fixture declares no ascii key, matching what the Python indexer
 * writes for `--locale en`.
 */
export type FixtureLocale = "tr" | "en";

export function buildTestDb(
	dbPath: string,
	docs: TestDoc[],
	locale: FixtureLocale = "tr",
): void {
	mkdirSync(dirname(dbPath), { recursive: true });
	const db = new Database(dbPath);
	try {
		for (const stmt of TEST_SCHEMA_STATEMENTS) {
			db.prepare(stmt).run();
		}
		const insertDoc = db.prepare(
			`INSERT INTO documents
       (title, title_normalized, path, content_raw, body_text, content_size, mtime,
        tags, frontmatter_type, session_id, linked_notes, indexed_at,
        content_hash, trust, scope)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		);
		const insertFts = db.prepare(
			`INSERT INTO documents_fts
       (rowid, title, content, tags, linked_notes, path_tokens)
       VALUES (?, ?, ?, ?, ?, ?)`,
		);
		const insertAsciiFts = db.prepare(
			`INSERT INTO documents_ascii_fts
       (rowid, title, content, tags, linked_notes, path_tokens)
       VALUES (?, ?, ?, ?, ?, ?)`,
		);
		db.prepare(
			"INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
		).run("normalization_profile", locale === "tr" ? "tr-cldr" : "en-unicode");
		db.prepare(
			"INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
		).run(
			"ascii_normalization_profile",
			locale === "tr" ? "tr-ascii-fold" : "disabled",
		);
		db.prepare(
			"INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
		).run("index_language", locale);
		// The schema gate in fts5Search reads this; without it every fixture
		// would be rejected as unversioned.
		db.prepare(
			"INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
		).run("schema_version", "4");
		const tx = db.transaction(() => {
			for (const d of docs) {
				const bodyText = d.bodyText ?? d.contentRaw;
				const info = insertDoc.run(
					d.title,
					d.titleNormalized,
					d.path,
					d.contentRaw,
					bodyText,
					d.contentRaw.length,
					d.mtime,
					d.tags ?? "",
					d.frontmatterType ?? "",
					d.sessionId ?? "",
					d.linkedNotes ?? "",
					new Date().toISOString(),
					d.contentHash ?? "",
					d.trust ?? "user",
					d.scope ?? "default",
				);
				const pathTokens = d.pathTokens ?? defaultPathTokens(d.path);
				insertFts.run(
					info.lastInsertRowid,
					d.titleNormalized,
					d.contentNormalized,
					d.tagsNormalized ?? d.tags ?? "",
					d.linkedNotesNormalized ?? d.linkedNotes ?? "",
					pathTokens,
				);
				insertAsciiFts.run(
					info.lastInsertRowid,
					d.titleAsciiNormalized ?? d.titleNormalized,
					d.contentAsciiNormalized ?? d.contentNormalized,
					d.tagsAsciiNormalized ?? d.tagsNormalized ?? d.tags ?? "",
					d.linkedNotesAsciiNormalized ??
						d.linkedNotesNormalized ??
						d.linkedNotes ??
						"",
					pathTokens,
				);
			}
		});
		tx();
	} finally {
		db.close();
	}
}
