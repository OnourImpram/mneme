/**
 * mneme_prime — Preflight context bundle for a new session.
 *
 * Builds a compact markdown preamble that combines the most recent
 * session-typed docs with topic-relevant documents matching the
 * task description. The bundle is truncated to fit `budget_tokens`
 * using a coarse 4-chars-per-token approximation; this is intentional.
 *
 * Phase F.5 (Adaptive Context Layer) will swap the chars-per-token
 * heuristic for the `mneme audit` token-meter and add the
 * keypoints/ref compression levels. The current implementation is the
 * `full` level only.
 */

import { existsSync, readFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import Database from "better-sqlite3";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { normalizeTr } from "../locale/tr.js";
import { buildFts5Query, fts5Search } from "../retrieval/fts5.js";
import { VaultPathError, assertWithinVault } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import { DEFAULT_STOPWORDS, type ToolResult } from "./common.js";

export const PrimeInputSchema = z.object({
	task_description: z.string().min(1),
	budget_tokens: z.number().int().positive().max(20_000).default(4_000),
	recent_session_count: z.number().int().nonnegative().max(20).default(3),
	topic_doc_count: z.number().int().nonnegative().max(20).default(5),
});

export type PrimeInput = z.infer<typeof PrimeInputSchema>;

export interface PrimeOutput {
	preamble: string;
	bytes: number;
	approx_tokens: number;
	truncated: boolean;
	sources: Array<{ path: string; kind: "recent" | "topic" }>;
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
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme index' first.`,
			},
		};
	}

	let recentDocs: SessionRow[];
	let topicHits: TopicHit[];
	try {
		recentDocs = listRecentSessions(vault, args.recent_session_count);
		topicHits = topicMatches(
			vault,
			args.task_description,
			args.topic_doc_count,
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

	const sections: string[] = [];
	const sources: PrimeOutput["sources"] = [];
	const budgetChars = args.budget_tokens * CHARS_PER_TOKEN;
	let used = 0;
	let truncated = false;

	function pushSection(
		title: string,
		path: string,
		body: string,
		kind: "recent" | "topic",
	): boolean {
		const block = `## ${title}\n\n*${path}*\n\n${body.slice(0, PER_DOC_SNIPPET_CHARS)}\n`;
		if (used + block.length > budgetChars) {
			truncated = true;
			return false;
		}
		sections.push(block);
		sources.push({ path, kind });
		used += block.length;
		return true;
	}

	for (const d of recentDocs) {
		const ok = pushSection(
			d.title || d.path,
			d.path,
			readBodySafe(vault, d.path),
			"recent",
		);
		if (!ok) break;
	}
	for (const h of topicHits) {
		const ok = pushSection(h.title || h.path, h.path, h.snippet, "topic");
		if (!ok) break;
	}

	const preamble = sections.join("\n");
	return {
		ok: true,
		data: {
			preamble,
			bytes: Buffer.byteLength(preamble, "utf8"),
			approx_tokens: Math.ceil(preamble.length / CHARS_PER_TOKEN),
			truncated,
			sources,
		},
	};
}

interface SessionRow {
	path: string;
	title: string;
}

function listRecentSessions(vault: VaultConfig, limit: number): SessionRow[] {
	if (limit <= 0) return [];
	const db = new Database(vault.fts5Db, {
		readonly: true,
		fileMustExist: true,
	});
	try {
		db.pragma("query_only = ON");
		const rows = db
			.prepare(
				`SELECT path, COALESCE(title, '') AS title
         FROM documents
         WHERE frontmatter_type = 'session'
         ORDER BY mtime DESC
         LIMIT ?`,
			)
			.all(limit) as SessionRow[];
		return rows;
	} finally {
		db.close();
	}
}

interface TopicHit {
	path: string;
	title: string;
	snippet: string;
}

function topicMatches(
	vault: VaultConfig,
	description: string,
	limit: number,
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
	});
	return hits.map((h) => ({
		path: h.path,
		title: h.title,
		snippet: h.contentRaw.slice(0, PER_DOC_SNIPPET_CHARS),
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
		return readFileSync(full, "utf8");
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
