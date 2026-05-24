/**
 * FTS5 query helper shared by search, summarize, and timeline tools.
 *
 * Reads the SQLite database that `mneme_core.fts5.indexer` populates.
 * The TS layer is read-only here. Writes happen exclusively through
 * the Python indexer, which owns schema migrations and tokenizer
 * configuration. This separation lets the TS MCP server start in
 * milliseconds with no spawning cost on the hot path.
 *
 * The function is intentionally narrow. Token filtering, stopword
 * choice, and locale normalization are caller responsibilities so
 * we keep one place to change behavior per tool.
 */

import Database from "better-sqlite3";

export interface Fts5Hit {
	path: string;
	title: string;
	rank: number;
	contentRaw: string;
	mtime: number;
	frontmatterType: string;
	sessionId: string;
}

export interface Fts5SearchOptions {
	dbPath: string;
	ftsQuery: string;
	limit: number;
	/** Optional inclusive lower bound for `mtime` (unix seconds). */
	mtimeFrom?: number;
	/** Optional inclusive upper bound for `mtime` (unix seconds). */
	mtimeTo?: number;
}

/**
 * Compile a free-text query into a safe FTS5 MATCH expression.
 *
 * - Drops empty tokens.
 * - Filters by `minTokenLength` to skip noisy single letters.
 * - Drops tokens in the `stopwords` set.
 * - Quotes each token so reserved FTS5 syntax (`OR`, `NEAR`, `*`)
 *   never gets interpreted, and strips embedded double-quotes so the
 *   query cannot escape its own quoting.
 *
 * Returns an empty string when nothing survives filtering. Callers
 * MUST treat empty output as "no search" and short-circuit.
 */
export function buildFts5Query(
	rawQuery: string,
	opts: {
		minTokenLength?: number;
		stopwords?: ReadonlySet<string>;
		normalize?: (s: string) => string;
	} = {},
): string {
	const minLen = opts.minTokenLength ?? 2;
	const stopwords = opts.stopwords ?? EMPTY_STOPWORDS;
	const norm = opts.normalize ?? identity;
	if (typeof rawQuery !== "string" || rawQuery.length === 0) return "";
	const tokens = rawQuery
		.split(/\s+/)
		.map((t) => norm(t.replace(/[":*]/g, "")))
		.filter((t) => t.length >= minLen && !stopwords.has(t));
	if (tokens.length === 0) return "";
	return tokens.map((t) => `"${t}"`).join(" OR ");
}

/**
 * Run an FTS5 MATCH against the documents virtual table.
 *
 * The connection is opened readonly with PRAGMA query_only so the FTS5
 * mutation surface is closed off entirely. `prepare`/`all` pattern
 * keeps prepared statements cached for the duration of the call.
 */
export function fts5Search(opts: Fts5SearchOptions): Fts5Hit[] {
	if (opts.ftsQuery.length === 0) return [];
	const db = new Database(opts.dbPath, { readonly: true, fileMustExist: true });
	try {
		db.pragma("query_only = ON");

		const filters: string[] = ["documents_fts MATCH ?"];
		const bindings: (string | number)[] = [opts.ftsQuery];
		if (opts.mtimeFrom !== undefined) {
			filters.push("d.mtime >= ?");
			bindings.push(opts.mtimeFrom);
		}
		if (opts.mtimeTo !== undefined) {
			filters.push("d.mtime <= ?");
			bindings.push(opts.mtimeTo);
		}

		const sql = `
      SELECT
        d.path AS path,
        COALESCE(d.title, '') AS title,
        fts.rank AS rank,
        COALESCE(d.content_raw, '') AS content_raw,
        COALESCE(d.mtime, 0) AS mtime,
        COALESCE(d.frontmatter_type, '') AS frontmatter_type,
        COALESCE(d.session_id, '') AS session_id
      FROM documents_fts fts
      JOIN documents d ON d.rowid = fts.rowid
      WHERE ${filters.join(" AND ")}
      ORDER BY fts.rank
      LIMIT ?
    `;
		bindings.push(opts.limit);

		const rows = db.prepare(sql).all(...bindings) as Array<{
			path: string;
			title: string;
			rank: number;
			content_raw: string;
			mtime: number;
			frontmatter_type: string;
			session_id: string;
		}>;
		return rows.map((r) => ({
			path: r.path,
			title: r.title,
			rank: r.rank,
			contentRaw: r.content_raw,
			mtime: r.mtime,
			frontmatterType: r.frontmatter_type,
			sessionId: r.session_id,
		}));
	} finally {
		db.close();
	}
}

const EMPTY_STOPWORDS: ReadonlySet<string> = new Set();
function identity(s: string): string {
	return s;
}
