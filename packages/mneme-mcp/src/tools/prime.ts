/**
 * mneme_prime — Preflight context bundle for a new session.
 *
 * Builds a compact markdown preamble that combines the most recent
 * session-typed docs with topic-relevant documents matching the
 * task description. The bundle is truncated to fit `budget_tokens`
 * using a coarse 4-chars-per-token approximation; this is intentional.
 *
 * Phase 2 (Adaptive Context Layer): per-doc redaction is applied before
 * assembly, the InjectionTracker deduplicates across calls in the same
 * session, and selectFormat/renderInjection pick the appropriate
 * compression level (full / keypoints / ref) based on context pressure
 * and prior injection state.
 */

import { existsSync, readFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import Database from "better-sqlite3";
import { z } from "zod";
import {
	type InjectionFormat,
	renderInjection,
	selectFormat,
} from "../distill/injection_format.js";
import {
	hasInjected,
	loadTracker,
	markInjected,
	saveTracker,
} from "../distill/injection_tracker.js";
import { ERROR_CODES } from "../errors.js";
import { wrapUntrusted } from "../injection.js";
import { normalizeTr } from "../locale/tr.js";
import { redact } from "../privacy.js";
import {
	buildFts5Query,
	fts5Search,
	hasScopeColumn,
} from "../retrieval/fts5.js";
import { assertWithinVault, VaultPathError } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import { DEFAULT_STOPWORDS, type ToolResult } from "./common.js";

export const PrimeInputSchema = z.object({
	task_description: z
		.string()
		.min(1)
		.max(2048)
		.describe("Current task used to retrieve topic-relevant context."),
	budget_tokens: z
		.number()
		.int()
		.positive()
		.max(20_000)
		.default(4_000)
		.describe("Approximate token budget for the returned preflight bundle."),
	recent_session_count: z
		.number()
		.int()
		.nonnegative()
		.max(20)
		.default(3)
		.describe("Maximum number of recent session documents to consider."),
	topic_doc_count: z
		.number()
		.int()
		.nonnegative()
		.max(20)
		.default(5)
		.describe("Maximum number of topic-relevant documents to consider."),
	session_id: z
		.string()
		.max(256)
		.describe(
			"Caller session identifier used for injection deduplication and progressive formatting.",
		)
		.optional(),
	/**
	 * Scope filter. Omit to use config.defaultScope(). Pass "*" for
	 * cross-scope. Skipped when the index lacks the scope column.
	 */
	scope: z
		.string()
		.max(256)
		.describe(
			"Scope to prime. Omit for the configured default scope. Pass '*' only for an explicit cross-scope query.",
		)
		.optional(),
});

export type PrimeInput = z.infer<typeof PrimeInputSchema>;

export interface PrimeOutput {
	preamble: string;
	bytes: number;
	approx_tokens: number;
	truncated: boolean;
	sources: Array<{ path: string; kind: "recent" | "topic" }>;
	/** Count of docs rendered at each format level for observability. */
	injection_format_counts: { full: number; keypoints: number; ref: number };
}

const CHARS_PER_TOKEN = 4;
const PER_DOC_SNIPPET_CHARS = 600;

export function primeTool(
	args: PrimeInput,
	vault: VaultConfig,
): ToolResult<PrimeOutput> {
	if (!existsSync(vault.fts5Db)) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_NOT_FOUND,
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme-core index rebuild' first.`,
			},
		};
	}

	// Resolve scope once; both sub-calls share the same resolved value so
	// recent sessions and topic matches stay consistent within one prime call.
	const scope = args.scope ?? vault.defaultScope();

	let recentDocs: SessionRow[];
	let topicHits: TopicHit[];
	try {
		recentDocs = listRecentSessions(vault, args.recent_session_count, scope);
		topicHits = topicMatches(
			vault,
			args.task_description,
			args.topic_doc_count,
			scope,
		);
	} catch (err) {
		// Match the structured error contract every other tool returns: a
		// failed SQLite read becomes an IO_ERROR envelope rather than an
		// unhandled exception that breaks the MCP tool-call response.
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: err instanceof Error ? err.message : String(err),
			},
		};
	}

	// Load the per-session injection tracker. When no session_id is provided
	// we use a fresh in-memory tracker (no persistence) so dedup still works
	// within this single call while not polluting the state dir.
	const tracker = args.session_id
		? loadTracker(vault.stateDir, args.session_id)
		: {
				sessionId: "anonymous",
				seenHashes: new Set<string>(),
				hits: 0,
				skips: 0,
			};

	const sections: string[] = [];
	const sources: PrimeOutput["sources"] = [];
	const budgetChars = args.budget_tokens * CHARS_PER_TOKEN;
	let used = 0;
	let truncated = false;

	// Format-level counters for observability output.
	const formatCounts: Record<InjectionFormat, number> = {
		full: 0,
		keypoints: 0,
		ref: 0,
	};

	function pushDoc(
		path: string,
		title: string,
		body: string,
		contentHash: string,
		kind: "recent" | "topic",
		keyPoints: string[] = [],
	): boolean {
		// Compute context pressure at the moment this doc is considered.
		const contextPressure = Math.min(1.0, used / Math.max(1, budgetChars));
		const alreadySeen = hasInjected(tracker, contentHash || path);
		const fmt = selectFormat(alreadySeen, contextPressure);

		const block = renderInjection(
			{ path, title: title || path, body, keyPoints },
			fmt,
		);

		if (used + block.length > budgetChars) {
			truncated = true;
			return false;
		}

		sections.push(block);
		sources.push({ path, kind });
		used += block.length;
		formatCounts[fmt] += 1;

		// Mark injected using contentHash when available, falling back to path.
		markInjected(tracker, contentHash || path);
		return true;
	}

	for (const d of recentDocs) {
		const body = readBodySafe(vault, d.path);
		let kp: string[] = [];
		try {
			kp = JSON.parse(d.keyPoints) as string[];
		} catch {
			kp = [];
		}
		const ok = pushDoc(
			d.path,
			d.title || d.path,
			body,
			d.contentHash || "",
			"recent",
			kp,
		);
		if (!ok) break;
	}
	for (const h of topicHits) {
		let kp: string[] = [];
		try {
			kp = JSON.parse(h.keyPoints) as string[];
		} catch {
			kp = [];
		}
		const ok = pushDoc(
			h.path,
			h.title || h.path,
			h.snippet,
			h.contentHash || "",
			"topic",
			kp,
		);
		if (!ok) break;
	}

	// Persist the tracker for the session when a session_id was supplied.
	if (args.session_id) {
		saveTracker(vault.stateDir, tracker);
	}

	// The preamble is assembled from untrusted vault bodies and is
	// surfaced as authoritative session context, so fence it with the
	// spotlighting guard (gap G-3). bytes/approx_tokens reflect the
	// fenced output that is actually returned. Empty input yields "".
	const preamble = wrapUntrusted(sections.join("\n"), "vault-prime");
	return {
		ok: true,
		data: {
			preamble,
			bytes: Buffer.byteLength(preamble, "utf8"),
			approx_tokens: Math.ceil(preamble.length / CHARS_PER_TOKEN),
			truncated,
			sources,
			injection_format_counts: {
				full: formatCounts.full,
				keypoints: formatCounts.keypoints,
				ref: formatCounts.ref,
			},
		},
	};
}

interface SessionRow {
	path: string;
	title: string;
	contentHash: string;
	keyPoints: string;
}

function listRecentSessions(
	vault: VaultConfig,
	limit: number,
	scope: string,
): SessionRow[] {
	if (limit <= 0) return [];
	const db = new Database(vault.fts5Db, {
		readonly: true,
		fileMustExist: true,
	});
	try {
		db.pragma("query_only = ON");
		// Defensive: scope column may be absent on pre-M1 indexes. Check once
		// with the live connection so the result is cached for subsequent calls.
		const scopeOk = hasScopeColumn(db, vault.fts5Db);
		const scopePred = scopeOk && scope !== "*" ? "AND scope = ?" : "";
		const scopeArgs: string[] = scopeOk && scope !== "*" ? [scope] : [];

		// key_points is added by the indexer migration; older DBs may not have it.
		// Try the full query first, fall back to a no-keypoints query if the column
		// is absent so that tests and legacy indexes continue to work.
		try {
			const rows = db
				.prepare(
					`SELECT path, COALESCE(title, '') AS title, COALESCE(content_hash, '') AS contentHash,
                    COALESCE(key_points, '[]') AS keyPoints
             FROM documents
             WHERE frontmatter_type = 'session' ${scopePred}
             ORDER BY mtime DESC
             LIMIT ?`,
				)
				.all(...scopeArgs, limit) as SessionRow[];
			return rows;
		} catch {
			// key_points column absent — return rows with empty keyPoints.
			const rows = db
				.prepare(
					`SELECT path, COALESCE(title, '') AS title, COALESCE(content_hash, '') AS contentHash
             FROM documents
             WHERE frontmatter_type = 'session' ${scopePred}
             ORDER BY mtime DESC
             LIMIT ?`,
				)
				.all(...scopeArgs, limit) as Omit<SessionRow, "keyPoints">[];
			return rows.map((r) => ({ ...r, keyPoints: "[]" }));
		}
	} finally {
		db.close();
	}
}

interface TopicHit {
	path: string;
	title: string;
	snippet: string;
	contentHash: string;
	keyPoints: string;
}

function topicMatches(
	vault: VaultConfig,
	description: string,
	limit: number,
	scope: string,
): TopicHit[] {
	if (limit <= 0) return [];
	const ftsQuery = buildFts5Query(description, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: normalizeTr,
	});
	if (ftsQuery.length === 0) return [];
	const hits = fts5Search({
		dbPath: vault.fts5Db,
		ftsQuery,
		limit,
		scope,
	});
	return hits.map((h) => ({
		path: h.path,
		title: h.title,
		// Apply redact before slicing so a <private> tag that straddles the
		// snippet boundary cannot leak its visible head. Mirrors the C4
		// invariant: every vault string is redacted before surfacing.
		snippet: redact(h.bodyText).text.slice(0, PER_DOC_SNIPPET_CHARS),
		contentHash: h.contentHash,
		keyPoints: "[]",
	}));
}

function readBodySafe(vault: VaultConfig, relPath: string): string {
	// Codex Pass 2 review fix: index rows can carry path traversal segments
	// through corruption or untrusted import sources. Resolve and assert
	// containment before any filesystem read. Failures return empty so a
	// poisoned index row drops the body silently rather than surfacing the
	// suppressed path to callers.
	try {
		const full = resolvePath(join(vault.root, relPath));
		assertWithinVault(vault.root, full);
		const raw = readFileSync(full, "utf8");
		// Apply redaction before the caller assembles the body into any section.
		// This closes the gap where a <private> block in a vault file could
		// pass through the body read unredacted. Mirrors mneme_recall behaviour.
		return redact(raw).text;
	} catch (err) {
		// A containment violation is a security signal, not a benign missing
		// file: record it on stderr where the operator and the MCP server log
		// see it, while the returned body stays empty so a poisoned index row
		// surfaces nothing to the caller. Mirrors mneme_recall.
		if (err instanceof VaultPathError) {
			process.stderr.write(
				`[mneme_prime] index row path escaped vault root and was refused: ${err.message}\n`,
			);
		}
		return "";
	}
}
