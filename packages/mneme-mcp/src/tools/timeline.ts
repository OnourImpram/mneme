/**
 * mneme_timeline - Temporal-ordered references for a subject.
 *
 * FTS5 mtime-sorted entries are the always-on baseline. When the KG
 * leg is active, the tool also queries Graphiti for bi-temporal
 * RELATES_TO facts about the subject and returns those alongside the
 * markdown timeline. The ``source`` field flips from ``"fts5"`` to
 * ``"graphiti+fts5"`` when KG facts are present, and the
 * ``as_of_applied`` boolean reports whether the operator's ``as_of``
 * point-in-time filter was honored at the graph layer.
 *
 * Output shape grows additively: existing v1.0 callers that only
 * read ``entries`` continue to work after KG enrichment turns on.
 */

import { existsSync } from "node:fs";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize } from "../injection.js";
import { normalizeTr } from "../locale/tr.js";
import { buildFts5Query, fts5Search } from "../retrieval/fts5.js";
import {
	closeDriver,
	createDriverFromVault,
	isKgActive,
	type TimelineFact,
	timelineForSubject,
} from "../retrieval/graphiti.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";

export const TimelineInputSchema = z.object({
	subject: z.string().min(1),
	valid_from: z
		.string()
		.regex(/^\d{4}-\d{2}-\d{2}$/)
		.optional(),
	valid_to: z
		.string()
		.regex(/^\d{4}-\d{2}-\d{2}$/)
		.optional(),
	/** Bi-temporal point-in-time. Honored at the Graphiti layer only. */
	as_of: z
		.string()
		.regex(/^\d{4}-\d{2}-\d{2}$/)
		.optional(),
	top_k: z.number().int().positive().max(100).default(25),
});

export type TimelineInput = z.infer<typeof TimelineInputSchema>;

export interface TimelineEntry {
	path: string;
	title: string;
	mtime: number;
	frontmatter_type: string;
}

export interface TimelineOutput {
	subject: string;
	entries: TimelineEntry[];
	facts?: TimelineFact[];
	source: "fts5" | "graphiti+fts5";
	as_of_applied: boolean;
}

function isoToEpoch(iso: string | undefined): string | undefined {
	if (iso === undefined) return undefined;
	return new Date(`${iso}T00:00:00Z`).toISOString();
}

export async function timelineTool(
	args: TimelineInput,
	vault: VaultConfig,
): Promise<ToolResult<TimelineOutput>> {
	if (!existsSync(vault.fts5Db)) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_NOT_FOUND,
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme-core index rebuild' first.`,
			},
		};
	}

	const ftsQuery = buildFts5Query(args.subject, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: normalizeTr,
	});

	let hits: ReturnType<typeof fts5Search> = [];
	if (ftsQuery.length > 0) {
		try {
			hits = fts5Search({
				dbPath: vault.fts5Db,
				ftsQuery,
				limit: args.top_k,
				mtimeFrom: args.valid_from ? isoDateToUnix(args.valid_from) : undefined,
				mtimeTo: args.valid_to
					? isoDateToUnixEndOfDay(args.valid_to)
					: undefined,
			});
		} catch (err) {
			return {
				ok: false,
				error: {
					code: ERROR_CODES.IO_ERROR,
					message: err instanceof Error ? err.message : String(err),
				},
			};
		}
	}

	const entries: TimelineEntry[] = hits
		.map((h) => ({
			path: h.path,
			// Title is untrusted vault text; defang the fence sentinel (G-3).
			title: neutralize(h.title),
			mtime: h.mtime,
			frontmatter_type: h.frontmatterType,
		}))
		.sort((a, b) => a.mtime - b.mtime);

	const kgEnabled = isKgActive(vault);
	let facts: TimelineFact[] = [];
	let asOfApplied = false;
	if (kgEnabled) {
		const driver = await createDriverFromVault(vault);
		if (driver !== null) {
			try {
				facts = await timelineForSubject(driver, args.subject, {
					validFrom: isoToEpoch(args.valid_from),
					validTo: isoToEpoch(args.valid_to),
					asOf: isoToEpoch(args.as_of),
				});
				asOfApplied = args.as_of !== undefined;
			} finally {
				await closeDriver(driver);
			}
		}
	}

	return {
		ok: true,
		data: {
			subject: args.subject,
			entries,
			...(facts.length > 0 ? { facts } : {}),
			source: facts.length > 0 ? "graphiti+fts5" : "fts5",
			as_of_applied: asOfApplied,
		},
	};
}
