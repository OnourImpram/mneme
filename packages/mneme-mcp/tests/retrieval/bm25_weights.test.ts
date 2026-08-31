/**
 * BM25 column-weight ranking contract (4.0).
 *
 * Before 4.0 the query ordered by bare `fts.rank`, which weights every FTS5
 * column equally. A note whose TITLE is the query then lost to a long note
 * that merely repeats the term in its body. Measured on a 24-query golden set
 * over a real vault: hit@1 29% -> 46%, hit@5 50% -> 67%, with Turkish gaining
 * most (hit@5 6/12 -> 10/12).
 *
 * These tests pin the behaviour AND prove the gate can fail: the same corpus
 * with flattened weights produces the pre-4.0 ordering. Without that negative
 * arm the assertions would pass for a build that had silently reverted to
 * equal weighting.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
	DEFAULT_BM25_WEIGHTS,
	fts5Search,
} from "../../src/retrieval/fts5.js";

let dir: string;
let dbPath: string;

/**
 * Two documents that mirror the real inversion seen in the operator vault,
 * where "kapalı karar listesi" ranked the canonical note #6 while short
 * agent-output files took the top slots:
 *
 *  - TITLE_MATCH : the query IS its title, but the body is LONG and mentions
 *                  the term only in passing (a canonical reference note).
 *  - BODY_FLOOD  : title is unrelated, body is SHORT and term-dense (an agent
 *                  run output).
 *
 * BM25 normalises by document length, so the short, term-dense file wins on
 * equal weights. Only a title weight rescues the note a person was actually
 * looking for. Getting this fixture wrong in the other direction (making the
 * flood document long) makes the negative control pass for the wrong reason:
 * length normalisation alone would sink it regardless of weighting.
 */
const TITLE_MATCH = "notes/kapali-operator-kararlari.md";
const BODY_FLOOD = "notes/agent-run-output.md";

beforeAll(() => {
	dir = mkdtempSync(join(tmpdir(), "mneme-bm25-"));
	dbPath = join(dir, "fts5.sqlite");
	const db = new Database(dbPath);
	db.exec(`
		CREATE TABLE documents(
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT, title_normalized TEXT, path TEXT UNIQUE NOT NULL,
			content_raw TEXT, body_text TEXT, content_size INTEGER, mtime REAL,
			tags TEXT, frontmatter_type TEXT, session_id TEXT,
			scope TEXT NOT NULL DEFAULT 'default', linked_notes TEXT,
			schema_version TEXT DEFAULT '4', language TEXT DEFAULT 'en',
			indexed_at TEXT, content_hash TEXT, trust TEXT,
			valid_from TEXT, valid_until TEXT
		);
		CREATE VIRTUAL TABLE documents_fts USING fts5(
			title, content, tags, linked_notes, path_tokens,
			tokenize='unicode61 remove_diacritics 2'
		);
		CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
		INSERT INTO index_meta(key, value) VALUES('schema_version', '4');
	`);

	// Long canonical note: title is the query, body is mostly unrelated prose.
	const uzunGovde = `${"Bu bolum surecin ayrintilarini anlatir. ".repeat(80)}kararlar`;
	// Short agent output: title unrelated, body short and term-dense.
	const kisaYogun = "kararlar kararlar kararlar";
	const rows: Array<[string, string, string, string]> = [
		[TITLE_MATCH, "kapali operator kararlari", uzunGovde, ""],
		[BODY_FLOOD, "ajan kosum ciktisi", kisaYogun, "hub-a hub-b hub-c"],
	];
	const insDoc = db.prepare(
		"INSERT INTO documents(path, title, body_text, linked_notes, mtime, " +
			"frontmatter_type, session_id, content_hash, trust, scope) " +
			"VALUES(?, ?, ?, ?, 1700000000, 'topic', 's1', 'h', 'user', 'default')",
	);
	const insFts = db.prepare(
		"INSERT INTO documents_fts" +
			"(rowid, title, content, tags, linked_notes, path_tokens) " +
			"VALUES(?, ?, ?, '', ?, ?)",
	);
	for (const [path, title, body, links] of rows) {
		const info = insDoc.run(path, title, body, links);
		// Path tokens are deliberately EMPTY here. This suite isolates the
		// title-vs-content weighting; leaving the path signal out keeps the
		// negative control honest, since both fixture paths contain the query
		// term and would otherwise mask the effect being measured.
		insFts.run(info.lastInsertRowid, title, body, links, "");
	}
	db.close();
});

afterAll(() => {
	rmSync(dir, { recursive: true, force: true });
});

describe("BM25 column weights", () => {
	it("defaults promote title and demote linked_notes", () => {
		expect(DEFAULT_BM25_WEIGHTS.title).toBe(10.0);
		expect(DEFAULT_BM25_WEIGHTS.content).toBe(1.0);
		expect(DEFAULT_BM25_WEIGHTS.tags).toBe(1.0);
		expect(DEFAULT_BM25_WEIGHTS.linkedNotes).toBe(0.1);
		// Path sits between title and content: strong evidence of aboutness,
		// but machine-assigned rather than authored.
		expect(DEFAULT_BM25_WEIGHTS.pathTokens).toBe(5.0);
		expect(DEFAULT_BM25_WEIGHTS.pathTokens).toBeLessThan(
			DEFAULT_BM25_WEIGHTS.title,
		);
		expect(DEFAULT_BM25_WEIGHTS.pathTokens).toBeGreaterThan(
			DEFAULT_BM25_WEIGHTS.content,
		);
		expect(DEFAULT_BM25_WEIGHTS.title).toBeGreaterThan(
			DEFAULT_BM25_WEIGHTS.content,
		);
	});

	it("ranks a title match above a body-frequency flood", () => {
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"kararlar" OR "kararlari"',
			limit: 5,
			scope: "*",
		});
		expect(hits.length).toBe(2);
		expect(hits[0]?.path).toBe(TITLE_MATCH);
	});

	/**
	 * NEGATIVE CONTROL — proves the assertion above is load-bearing.
	 *
	 * Flattening the weights restores pre-4.0 behaviour and the body-flood
	 * document takes first place. If this test ever passes with TITLE_MATCH
	 * first, the weighting is no longer doing the work the previous test
	 * claims it does.
	 */
	it("negative control: equal weights let the body flood win", () => {
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"kararlar" OR "kararlari"',
			limit: 5,
			scope: "*",
			weights: {
				title: 1.0,
				content: 1.0,
				tags: 1.0,
				linkedNotes: 1.0,
				pathTokens: 1.0,
			},
		});
		expect(hits.length).toBe(2);
		expect(hits[0]?.path).toBe(BODY_FLOOD);
	});

	it("weights are caller-overridable without touching the index", () => {
		const heavy = fts5Search({
			dbPath,
			ftsQuery: '"kararlar" OR "kararlari"',
			limit: 5,
			scope: "*",
			weights: {
				title: 50.0,
				content: 1.0,
				tags: 1.0,
				linkedNotes: 0.1,
				pathTokens: 5.0,
			},
		});
		expect(heavy[0]?.path).toBe(TITLE_MATCH);
	});
});
