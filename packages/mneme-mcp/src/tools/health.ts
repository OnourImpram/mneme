/**
 * mneme_health — what the index can say about itself.
 *
 * Every number this tool returns had to be dug out of the filesystem by hand
 * before 4.0: index age, document count, staging depth, last query time. A
 * memory system that cannot report its own staleness does not fail loudly, it
 * answers confidently from old data. This vault's previous memory layer ran
 * 105 days behind, returned plausible results the whole time, and only got
 * caught when someone compared two systems side by side.
 *
 * Each warning carries a `remedy`. A detector without one becomes noise, and
 * noise gets read as silence.
 *
 * Read-only, like every TS path into SQLite.
 */

import { type Dirent, existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import Database from "better-sqlite3";
import { z } from "zod";
import { toMnemeError } from "../errors.js";
import { profileById } from "../locale/index.js";
import { EXPECTED_SCHEMA_VERSION } from "../retrieval/fts5.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

/** Beyond this an index is reported stale. */
const STALE_INDEX_DAYS = 7;

/** Beyond this the staging queue is reported as not draining. */
const STAGING_BACKLOG_DAYS = 3;

/** Directory-walk ceiling, so a runaway queue cannot stall the tool. */
const MAX_STAGING_SCAN = 20_000;

const MS_PER_DAY = 86_400_000;

export const HealthInputSchema = z.object({
	include_language_breakdown: z
		.boolean()
		.default(true)
		.describe(
			"Group the document count by detected language. One extra scan of the documents table.",
		),
});

export type HealthInput = z.infer<typeof HealthInputSchema>;

export interface HealthWarning {
	code: string;
	detail: string;
	/** What to actually do. Never omit — a detector ships with its remedy. */
	remedy: string;
}

export interface HealthOutput {
	ok: boolean;
	schema: { expected: string; stored: string | null; matches: boolean };
	locale: {
		profile: string | null;
		asciiProfile: string | null;
		indexLanguage: string | null;
		recognized: boolean;
	};
	index: {
		path: string;
		exists: boolean;
		sizeBytes: number;
		documentCount: number | null;
		newestDocumentAgeDays: number | null;
		lastIndexRunAt: string | null;
	};
	languages: Record<string, number> | null;
	staging: { pendingFiles: number; oldestAgeDays: number | null };
	warnings: HealthWarning[];
}

function daysSince(epochMs: number): number {
	return Math.round(((Date.now() - epochMs) / MS_PER_DAY) * 10) / 10;
}

/**
 * Count staging files that have not been archived yet.
 *
 * Files under `staging/archive/` are already processed; counting the whole
 * tree conflates a healthy archive with a stuck queue. Measured on a real
 * vault: 7,734 total files, of which 3,254 were archived and 4,480 genuinely
 * pending — a distinction an earlier report missed entirely.
 */
function scanStaging(dir: string): {
	pending: number;
	oldestMs: number | null;
} {
	if (!existsSync(dir)) return { pending: 0, oldestMs: null };
	let pending = 0;
	let oldestMs: number | null = null;
	const stack: string[] = [dir];
	while (stack.length > 0 && pending < MAX_STAGING_SCAN) {
		const current = stack.pop();
		if (current === undefined) break;
		// Explicit type: the readdirSync overload set otherwise infers the
		// Buffer variant, which does not carry a string `name`.
		let entries: Dirent[];
		try {
			entries = readdirSync(current, { withFileTypes: true });
		} catch {
			continue;
		}
		for (const entry of entries) {
			const full = join(current, entry.name);
			if (entry.isDirectory()) {
				// Archived output is finished work, not backlog.
				if (current === dir && entry.name === "archive") continue;
				stack.push(full);
				continue;
			}
			pending += 1;
			try {
				const mtime = statSync(full).mtimeMs;
				if (oldestMs === null || mtime < oldestMs) oldestMs = mtime;
			} catch {
				// A file that vanished mid-scan is not a health signal.
			}
		}
	}
	return { pending, oldestMs };
}

function readMeta(db: Database.Database): Map<string, string> {
	const meta = new Map<string, string>();
	const table = db
		.prepare(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='index_meta'",
		)
		.get() as { name: string } | undefined;
	if (!table) return meta;
	for (const row of db
		.prepare("SELECT key, value FROM index_meta")
		.all() as Array<{
		key: string;
		value: string;
	}>) {
		meta.set(row.key, row.value);
	}
	return meta;
}

export function healthTool(
	args: HealthInput,
	vault: VaultConfig,
): ToolResult<HealthOutput> {
	const warnings: HealthWarning[] = [];
	const dbPath = vault.fts5Db;
	const exists = existsSync(dbPath);

	const staging = scanStaging(vault.stagingDir);
	const stagingOldestDays =
		staging.oldestMs === null ? null : daysSince(staging.oldestMs);
	if (stagingOldestDays !== null && stagingOldestDays > STAGING_BACKLOG_DAYS) {
		warnings.push({
			code: "STAGING_NOT_DRAINING",
			detail:
				`${staging.pending} pending staging files, oldest ${stagingOldestDays} days. ` +
				"New sessions keep writing while nothing archives them.",
			remedy:
				"Check the session-end hook that archives staging. Files older than " +
				"the newest archived batch mark where processing stopped.",
		});
	}

	if (!exists) {
		return {
			ok: true,
			data: {
				ok: false,
				schema: {
					expected: EXPECTED_SCHEMA_VERSION,
					stored: null,
					matches: false,
				},
				locale: {
					profile: null,
					asciiProfile: null,
					indexLanguage: null,
					recognized: false,
				},
				index: {
					path: dbPath,
					exists: false,
					sizeBytes: 0,
					documentCount: null,
					newestDocumentAgeDays: null,
					lastIndexRunAt: null,
				},
				languages: null,
				staging: {
					pendingFiles: staging.pending,
					oldestAgeDays: stagingOldestDays,
				},
				warnings: [
					...warnings,
					{
						code: "INDEX_NOT_FOUND",
						detail: `No FTS5 index at ${dbPath}.`,
						remedy:
							"Run 'mneme-core index rebuild' to build it from your markdown.",
					},
				],
			},
		};
	}

	try {
		const db = new Database(dbPath, { readonly: true, fileMustExist: true });
		try {
			db.pragma("query_only = ON");
			const meta = readMeta(db);
			const stored = meta.get("schema_version") ?? null;
			const matches = stored === EXPECTED_SCHEMA_VERSION;
			if (!matches) {
				warnings.push({
					code: "SCHEMA_MISMATCH",
					detail:
						`Index schema '${stored ?? "unversioned"}' but this client speaks ` +
						`'${EXPECTED_SCHEMA_VERSION}'. Queries will be refused.`,
					remedy:
						"Run 'mneme-core index rebuild'. Markdown is the source of truth, " +
						"so nothing is lost by regenerating the index.",
				});
			}

			const profile = meta.get("normalization_profile") ?? null;
			const recognized = profile !== null && profileById(profile) !== undefined;
			if (!recognized) {
				warnings.push({
					code: "LOCALE_PROFILE_UNRECOGNIZED",
					detail:
						`Normalizer profile '${profile ?? "absent"}' cannot serve ` +
						"locale-sensitive retrieval.",
					remedy:
						"Rebuild with 'mneme-core index rebuild --locale <tr|en>', or " +
						"upgrade mneme if a newer release wrote this index.",
				});
			}

			const documentCount = (
				db.prepare("SELECT COUNT(*) AS n FROM documents").get() as { n: number }
			).n;
			const newest = (
				db.prepare("SELECT MAX(mtime) AS m FROM documents").get() as {
					m: number | null;
				}
			).m;
			const newestAgeDays = newest === null ? null : daysSince(newest * 1000);
			if (newestAgeDays !== null && newestAgeDays > STALE_INDEX_DAYS) {
				warnings.push({
					code: "INDEX_STALE",
					detail:
						`Newest indexed document is ${newestAgeDays} days old. ` +
						"Anything written since is invisible to search.",
					remedy: "Run 'mneme-core index' to pick up new and changed files.",
				});
			}

			let languages: Record<string, number> | null = null;
			if (args.include_language_breakdown) {
				languages = {};
				for (const row of db
					.prepare(
						"SELECT COALESCE(language, '') AS lang, COUNT(*) AS n " +
							"FROM documents GROUP BY language ORDER BY n DESC",
					)
					.all() as Array<{ lang: string; n: number }>) {
					languages[row.lang === "" ? "(unset)" : row.lang] = row.n;
				}
				// A single language across a large corpus means detection never
				// ran — the exact shape of the pre-4.0 defect, where every row
				// carried the schema default.
				const distinct = Object.keys(languages).length;
				if (documentCount > 100 && distinct === 1) {
					warnings.push({
						code: "LANGUAGE_UNIFORM",
						detail:
							`All ${documentCount} documents share one language label ` +
							`(${Object.keys(languages)[0]}). Detection may not have run.`,
						remedy:
							"Rebuild the index so per-document language detection populates " +
							"the column, or set 'lang:' in frontmatter where it matters.",
					});
				}
			}

			return {
				ok: true,
				data: {
					ok: warnings.length === 0,
					schema: { expected: EXPECTED_SCHEMA_VERSION, stored, matches },
					locale: {
						profile,
						asciiProfile: meta.get("ascii_normalization_profile") ?? null,
						indexLanguage: meta.get("index_language") ?? null,
						recognized,
					},
					index: {
						path: dbPath,
						exists: true,
						sizeBytes: statSync(dbPath).size,
						documentCount,
						newestDocumentAgeDays: newestAgeDays,
						lastIndexRunAt: meta.get("last_index_run_at") ?? null,
					},
					languages,
					staging: {
						pendingFiles: staging.pending,
						oldestAgeDays: stagingOldestDays,
					},
					warnings,
				},
			};
		} finally {
			db.close();
		}
	} catch (err) {
		return { ok: false, error: toMnemeError(err) };
	}
}
