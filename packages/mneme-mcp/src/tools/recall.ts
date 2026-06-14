/**
 * mneme_recall — Retrieve sessions by id or date range.
 *
 * Returns the full markdown body of matching session-typed documents.
 * If no filter is provided, returns the most recently modified
 * `session` documents.
 */

import { existsSync, readFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import Database from "better-sqlite3";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { wrapUntrusted } from "../injection.js";
import { redact } from "../privacy.js";
import { assertWithinVault, VaultPathError } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import {
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";

export const RecallInputSchema = z
	.object({
		session_id: z.string().min(1).optional(),
		date_from: z
			.string()
			.regex(/^\d{4}-\d{2}-\d{2}$/)
			.optional(),
		date_to: z
			.string()
			.regex(/^\d{4}-\d{2}-\d{2}$/)
			.optional(),
		top_n: z.number().int().positive().max(50).default(10),
		/** When true, the response includes the full markdown body. */
		include_body: z.boolean().default(true),
	})
	.refine(
		(v) =>
			v.session_id !== undefined ||
			v.date_from !== undefined ||
			v.date_to !== undefined ||
			true,
		{ message: "any combination of session_id, date_from, date_to is allowed" },
	);

export type RecallInput = z.infer<typeof RecallInputSchema>;

export interface RecallEntry {
	path: string;
	title: string;
	mtime: number;
	frontmatter_type: string;
	session_id: string;
	body?: string;
}

export interface RecallOutput {
	entries: RecallEntry[];
}

export function recallTool(
	args: RecallInput,
	vault: VaultConfig,
): ToolResult<RecallOutput> {
	if (!existsSync(vault.fts5Db)) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_NOT_FOUND,
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme-core index rebuild' first.`,
			},
		};
	}

	const conditions: string[] = [];
	const bindings: (string | number)[] = [];
	if (args.session_id) {
		conditions.push("session_id = ?");
		bindings.push(args.session_id);
	}
	if (args.date_from) {
		conditions.push("mtime >= ?");
		bindings.push(isoDateToUnix(args.date_from));
	}
	if (args.date_to) {
		conditions.push("mtime <= ?");
		bindings.push(isoDateToUnixEndOfDay(args.date_to));
	}
	const whereClause =
		conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

	const sql = `
    SELECT path, COALESCE(title, '') AS title, mtime,
           COALESCE(frontmatter_type, '') AS frontmatter_type,
           COALESCE(session_id, '') AS session_id
    FROM documents
    ${whereClause}
    ORDER BY mtime DESC
    LIMIT ?
  `;
	bindings.push(args.top_n);

	const db = new Database(vault.fts5Db, {
		readonly: true,
		fileMustExist: true,
	});
	let rows: Array<{
		path: string;
		title: string;
		mtime: number;
		frontmatter_type: string;
		session_id: string;
	}>;
	try {
		db.pragma("query_only = ON");
		rows = db.prepare(sql).all(...bindings) as typeof rows;
	} catch (err) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: err instanceof Error ? err.message : String(err),
			},
		};
	} finally {
		db.close();
	}

	const entries: RecallEntry[] = rows.map((r) => {
		const e: RecallEntry = {
			path: r.path,
			title: r.title,
			mtime: r.mtime,
			frontmatter_type: r.frontmatter_type,
			session_id: r.session_id,
		};
		if (args.include_body) {
			// Phase J post-Codex-review fix: index rows can carry path traversal
			// segments through corruption or untrusted import sources. Resolve
			// and assert containment before any filesystem read.
			const full = resolvePath(join(vault.root, r.path));
			try {
				assertWithinVault(vault.root, full);
				// Full body is untrusted vault content returned on explicit
				// request; redact <private> blocks first (same as prime.ts
				// readBodySafe), then fence with the spotlighting guard (G-3).
				e.body = wrapUntrusted(
					redact(readFileSync(full, "utf8")).text,
					"vault-recall",
				);
			} catch (err) {
				// Leave body undefined and surface nothing to the caller about
				// the suppressed row, so a poisoned index cannot probe what is
				// reachable. A containment violation is a security signal, not a
				// benign missing file, so record it on stderr where the operator
				// and the MCP server log see it. The tool response stays clean
				// either way.
				if (err instanceof VaultPathError) {
					process.stderr.write(
						`[mneme_recall] index row path escaped vault root and was refused: ${err.message}\n`,
					);
				}
			}
		}
		return e;
	});

	return { ok: true, data: { entries } };
}
