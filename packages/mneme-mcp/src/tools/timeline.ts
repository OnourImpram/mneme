/**
 * mneme_timeline - Temporal-ordered references for a subject.
 *
 * FTS5 mtime-sorted entries are the always-on baseline. When the KG
 * leg is active, the tool also queries Graphiti for bi-temporal
 * RELATES_TO facts about the subject and returns those alongside the
 * markdown timeline. The ``source`` field flips from ``"fts5"`` to
 * ``"graphiti+fts5"`` when KG facts are present, and the
 * ``as_of_applied`` reports whether the returned data was constrained by
 * the deterministic transaction-time snapshot. The always-on FTS5 leg uses
 * file mtime as its transaction-time provenance. Graphiti uses created_at
 * and expired_at when that optional leg contributes facts.
 *
 * Output shape grows additively: existing v1.0 callers that only
 * read ``entries`` continue to work after KG enrichment turns on.
 */

import { existsSync } from "node:fs";
import { z } from "zod";
import { ERROR_CODES, toMnemeError } from "../errors.js";
import { neutralize } from "../injection.js";
import { resolveIndexProfile } from "../locale/resolve.js";
import { buildFts5Query, fts5Search } from "../retrieval/fts5.js";
import {
	closeDriver,
	createDriverFromVault,
	isKgActive,
	type Neo4jDriverLike,
	queryTimelineForSubject,
	type TimelineFact,
	type TimelineQueryOptions,
	type TimelineQueryResult,
} from "../retrieval/graphiti.js";
import { ScopeSchema } from "../scope.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isCalendarDate(value: string): boolean {
	if (!ISO_DATE_RE.test(value)) return false;
	const [year, month, day] = value.split("-").map(Number);
	const parsed = new Date(Date.UTC(year ?? 0, (month ?? 0) - 1, day ?? 0));
	return (
		parsed.getUTCFullYear() === year &&
		parsed.getUTCMonth() + 1 === month &&
		parsed.getUTCDate() === day
	);
}

const IsoDateSchema = z
	.string()
	.regex(ISO_DATE_RE)
	.refine(isCalendarDate, "Expected a valid calendar date in YYYY-MM-DD form.");

export const TimelineInputSchema = z
	.object({
		subject: z
			.string()
			.min(1)
			.max(2048)
			.describe("Subject query for the timeline."),
		valid_from: IsoDateSchema.describe(
			"Inclusive valid-time lower bound in YYYY-MM-DD form.",
		).optional(),
		valid_to: IsoDateSchema.describe(
			"Inclusive valid-time upper bound in YYYY-MM-DD form.",
		).optional(),
		/** Transaction-time snapshot applied to every contributing backend. */
		as_of: IsoDateSchema.describe(
			"Transaction-time snapshot at the start of the UTC date for Graphiti facts.",
		).optional(),
		top_k: z
			.number()
			.int()
			.positive()
			.max(100)
			.default(25)
			.describe("Maximum number of FTS5 timeline entries to return."),
		/**
		 * Scope filter. Omit to use config.defaultScope(). Pass "*" for
		 * cross-scope. Concrete reads require a scope-aware index.
		 */
		scope: ScopeSchema.describe(
			"Scope to query. Omit for the configured default scope. Pass '*' only for an explicit cross-scope query.",
		).optional(),
	})
	.superRefine((value, ctx) => {
		if (
			value.valid_from !== undefined &&
			value.valid_to !== undefined &&
			value.valid_from > value.valid_to
		) {
			ctx.addIssue({
				code: "custom",
				path: ["valid_to"],
				message: "valid_to must be on or after valid_from.",
			});
		}
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

export interface TimelineGraphAdapter {
	isActive(vault: VaultConfig): boolean;
	createDriver(vault: VaultConfig): Promise<Neo4jDriverLike | null>;
	query(
		driver: Neo4jDriverLike,
		subject: string,
		opts: TimelineQueryOptions,
	): Promise<TimelineQueryResult>;
	close(driver: Neo4jDriverLike | null): Promise<void>;
}

interface TimelinePlan {
	ftsMtimeFrom?: number;
	ftsMtimeTo?: number;
	graph: TimelineQueryOptions;
	asOfRequested: boolean;
}

const DEFAULT_GRAPH_ADAPTER: TimelineGraphAdapter = {
	isActive: isKgActive,
	createDriver: createDriverFromVault,
	query: queryTimelineForSubject,
	close: closeDriver,
};

function isoToEpoch(iso: string | undefined): string | undefined {
	if (iso === undefined) return undefined;
	return new Date(`${iso}T00:00:00Z`).toISOString();
}

function isoToNextDay(iso: string | undefined): string | undefined {
	if (iso === undefined) return undefined;
	const [year, month, day] = iso.split("-").map(Number);
	return new Date(
		Date.UTC(year ?? 0, (month ?? 0) - 1, (day ?? 0) + 1),
	).toISOString();
}

function minimumDefined(
	left: number | undefined,
	right: number | undefined,
): number | undefined {
	if (left === undefined) return right;
	if (right === undefined) return left;
	return Math.min(left, right);
}

function buildTimelinePlan(args: TimelineInput, scope: string): TimelinePlan {
	const validTo = args.valid_to
		? isoDateToUnixEndOfDay(args.valid_to)
		: undefined;
	const asOf = args.as_of ? isoDateToUnix(args.as_of) : undefined;
	return {
		ftsMtimeFrom: args.valid_from ? isoDateToUnix(args.valid_from) : undefined,
		ftsMtimeTo: minimumDefined(validTo, asOf),
		graph: {
			validFrom: isoToEpoch(args.valid_from),
			validToExclusive: isoToNextDay(args.valid_to),
			asOf: isoToEpoch(args.as_of),
			scope,
		},
		asOfRequested: args.as_of !== undefined,
	};
}

export async function timelineTool(
	args: TimelineInput,
	vault: VaultConfig,
	graph: TimelineGraphAdapter = DEFAULT_GRAPH_ADAPTER,
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

	const scope = args.scope ?? vault.defaultScope();
	const plan = buildTimelinePlan(args, scope);

	// The index declares its normalizer; the query side adopts it. Hardcoding
	// the Turkish one always produced an ASCII arm, which fts5Search refuses
	// unless the index carries the Turkish ASCII key — so every call against
	// an English index failed, whatever the subject.
	const resolved = resolveIndexProfile(vault.fts5Db);
	if (!resolved.ok) return { ok: false, error: resolved.error };
	const { profile } = resolved;

	const ftsQuery = buildFts5Query(args.subject, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: profile.normalize,
	});
	const ftsQueryAscii = profile.asciiFold
		? buildFts5Query(args.subject, {
				minTokenLength: 2,
				stopwords: DEFAULT_STOPWORDS,
				normalize: profile.asciiFold,
			})
		: undefined;

	let hits: ReturnType<typeof fts5Search> = [];
	if (ftsQuery.length > 0 || ftsQueryAscii) {
		try {
			hits = fts5Search({
				dbPath: vault.fts5Db,
				ftsQuery,
				ftsQueryAscii,
				limit: args.top_k,
				mtimeFrom: plan.ftsMtimeFrom,
				mtimeTo: plan.ftsMtimeTo,
				scope,
			});
		} catch (err) {
			return { ok: false, error: toMnemeError(err) };
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
		.sort((a, b) => {
			const timeOrder = a.mtime - b.mtime;
			if (timeOrder !== 0) return timeOrder;
			if (a.path === b.path) return 0;
			return a.path < b.path ? -1 : 1;
		});

	const kgEnabled = graph.isActive(vault);
	let facts: TimelineFact[] = [];
	const asOfApplied = plan.asOfRequested;
	if (kgEnabled) {
		const driver = await graph.createDriver(vault);
		if (driver !== null) {
			try {
				const graphResult = await graph.query(driver, args.subject, plan.graph);
				const graphSnapshotApplied =
					!plan.asOfRequested || graphResult.asOfApplied;
				facts =
					graphResult.querySucceeded && graphSnapshotApplied
						? graphResult.facts
						: [];
			} finally {
				await graph.close(driver);
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
