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
import { ERROR_CODES, MnemeToolError } from "../errors.js";

export interface Fts5Hit {
	path: string;
	title: string;
	rank: number;
	/**
	 * Unsafe for direct display: contains frontmatter. Run through
	 * redact()/neutralize() and strip frontmatter before surfacing to any
	 * client.
	 */
	contentRaw: string;
	bodyText: string;
	mtime: number;
	frontmatterType: string;
	sessionId: string;
	/** SHA-256 hex digest of raw file bytes computed before redaction. Empty string when column absent (legacy index). */
	contentHash: string;
	/** Origin trust label. 'user' for all vault-origin files. */
	trust: string;
}

export interface Fts5SearchOptions {
	dbPath: string;
	ftsQuery: string;
	limit: number;
	/** Optional inclusive lower bound for `mtime` (unix seconds). */
	mtimeFrom?: number;
	/** Optional inclusive upper bound for `mtime` (unix seconds). */
	mtimeTo?: number;
	/**
	 * Required scope filter. Callers resolve this from args or
	 * config.defaultScope() before passing it in. Pass `"*"` to disable
	 * filtering only through an explicit cross-scope read. Concrete-scope
	 * reads fail closed when a pre-scope index is detected.
	 */
	scope: string;
}

/**
 * Compile a free-text query into a safe FTS5 MATCH expression.
 *
 * - Drops empty tokens.
 * - Filters by `minTokenLength` to skip noisy single letters.
 * - Drops tokens in the `stopwords` set.
 * - Splits each word on FTS5-reserved and tokenizer-separator chars and
 *   rejoins it as one quoted phrase, so `claude-mem` becomes
 *   `"claude mem"` and matches the adjacent tokens unicode61 indexed
 *   rather than the fused, unmatchable `"claudemem"`. Quoting keeps any
 *   surviving operator literal and stops the query escaping its quoting.
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
	const phrases: string[] = [];
	for (const word of rawQuery.split(/\s+/)) {
		const parts = word
			.split(/[-":^*()]+/)
			.map((p) => norm(p))
			.filter((p) => p.length >= minLen && !stopwords.has(p));
		if (parts.length > 0) phrases.push(`"${parts.join(" ")}"`);
	}
	if (phrases.length === 0) return "";
	return phrases.join(" OR ");
}

/**
 * Run an FTS5 MATCH against the documents virtual table.
 *
 * The connection is opened readonly with PRAGMA query_only so the FTS5
 * mutation surface is closed off entirely. `prepare`/`all` pattern
 * keeps prepared statements cached for the duration of the call.
 */
export function fts5Search(opts: Fts5SearchOptions): Fts5Hit[] {
	if (opts.scope === undefined)
		throw new Error(
			'fts5Search: scope must be resolved by the caller — pass vault.defaultScope() for the default, or "*" for cross-scope',
		);
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
		// Concrete-scope reads must never silently widen on a legacy index.
		// Explicit `"*"` remains the only cross-scope selector.
		requireScopeColumn(db, opts.dbPath, opts.scope);
		if (opts.scope !== "*") {
			filters.push("d.scope = ?");
			bindings.push(opts.scope);
		}

		const sql = `
      SELECT
        d.path AS path,
        COALESCE(d.title, '') AS title,
        fts.rank AS rank,
        COALESCE(d.content_raw, '') AS content_raw,
        COALESCE(d.body_text, '') AS body_text,
        COALESCE(d.mtime, 0) AS mtime,
        COALESCE(d.frontmatter_type, '') AS frontmatter_type,
        COALESCE(d.session_id, '') AS session_id,
        COALESCE(d.content_hash, '') AS content_hash,
        COALESCE(d.trust, 'user') AS trust
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
			body_text: string;
			mtime: number;
			frontmatter_type: string;
			session_id: string;
			content_hash: string;
			trust: string;
		}>;
		return rows.map((r) => ({
			path: r.path,
			title: r.title,
			rank: r.rank,
			contentRaw: r.content_raw,
			bodyText: r.body_text,
			mtime: r.mtime,
			frontmatterType: r.frontmatter_type,
			sessionId: r.session_id,
			contentHash: r.content_hash,
			trust: r.trust,
		}));
	} finally {
		db.close();
	}
}

const EMPTY_STOPWORDS: ReadonlySet<string> = new Set();
function identity(s: string): string {
	return s;
}

/**
 * Per-DB scope-column presence cache. Keyed by absolute db file path so
 * different vault databases (including in-test temp files) never share state.
 *
 * Only positive results (column present) are cached. Absence is NOT cached so
 * that an in-process reindex which ADDS the scope column is detected on the
 * next call without a process restart.
 */
const scopeColumnPresence = new Map<string, boolean>();
/** Tracks which db paths have already received the one-time refusal warning. */
const scopeColumnAbsentWarned = new Set<string>();

/**
 * Return true when the `documents` table has a `scope` column.
 *
 * Caches positive results per db path (column present: PRAGMA runs once per
 * process lifetime). When the column is absent, the result is NOT cached so a
 * later in-process reindex that adds the column is detected automatically on
 * the next call. The one-time refusal warning is suppressed after the first
 * emission per path to avoid log spam.
 *
 * Requires an already-open, query_only connection so we reuse the
 * caller's transaction without paying an extra open/close round-trip.
 */
export function hasScopeColumn(db: Database.Database, dbPath: string): boolean {
	if (scopeColumnPresence.get(dbPath) === true) return true;
	const rows = db.prepare("PRAGMA table_info(documents)").all() as Array<{
		name: string;
	}>;
	const has = rows.some((r) => r.name === "scope");
	if (has) {
		scopeColumnPresence.set(dbPath, true);
	} else if (!scopeColumnAbsentWarned.has(dbPath)) {
		scopeColumnAbsentWarned.add(dbPath);
		process.stderr.write(
			"[mneme-mcp] documents.scope column absent — pre-scope index detected; concrete-scope reads are refused until the index is rebuilt.\n",
		);
	}
	return has;
}


/**
 * Enforce the scope-isolation schema boundary for an already-open database.
 *
 * A legacy index has no trustworthy tenant label. Returning its rows for a
 * concrete scope would silently widen access, so the only safe behavior is to
 * refuse the read and require a deterministic local rebuild. Explicit `"*"`
 * is allowed because the caller has deliberately requested cross-scope access.
 */
export function requireScopeColumn(
	db: Database.Database,
	dbPath: string,
	scope: string,
): void {
	if (scope === "*") return;
	if (hasScopeColumn(db, dbPath)) return;
	throw new MnemeToolError(
		ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH,
		"The FTS5 index predates scope isolation and cannot satisfy a scoped read. " +
			"Run 'mneme-core index rebuild' before retrying, or pass scope='*' only for an intentional cross-scope read.",
	);
}
