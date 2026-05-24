/**
 * mneme_search — FTS5 BM25 retrieval over the vault.
 *
 * v1.0 Phase C ships the FTS5 leg. The Backend interface mirrors the
 * Python `RetrievalBackend` Protocol so a dense (LEANN) or graph
 * (Graphiti) backend can fuse in later via RRF without touching this
 * file. The dense+KG fusion will live in `src/retrieval/rrf.ts` and
 * land alongside Phase E.
 */

import { existsSync } from "node:fs";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { normalizeTr } from "../locale/tr.js";
import { type Fts5Hit, buildFts5Query, fts5Search } from "../retrieval/fts5.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	type ToolResult,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
} from "./common.js";

export const SearchInputSchema = z.object({
	query: z.string().min(1),
	top_k: z.number().int().positive().max(50).default(10),
	filters: z
		.object({
			date_from: z
				.string()
				.regex(/^\d{4}-\d{2}-\d{2}$/)
				.optional(),
			date_to: z
				.string()
				.regex(/^\d{4}-\d{2}-\d{2}$/)
				.optional(),
			type: z.enum(["session", "topic", "reference"]).optional(),
		})
		.optional(),
	/** Hard floor below which the query is dropped to save context. */
	min_query_length: z.number().int().nonnegative().default(0),
});

export type SearchInput = z.infer<typeof SearchInputSchema>;

export interface SearchHit {
	path: string;
	title: string;
	score: number;
	snippet: string;
	type: string;
	mtime: number;
}

export interface SearchOutput {
	query: string;
	hits: SearchHit[];
}

const SNIPPET_CHARS = 200;

export function searchTool(
	args: SearchInput,
	vault: VaultConfig,
): ToolResult<SearchOutput> {
	if (args.query.length < args.min_query_length) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.QUERY_TOO_SHORT,
				message: `Query length ${args.query.length} below threshold ${args.min_query_length}.`,
			},
		};
	}
	if (!existsSync(vault.fts5Db)) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_NOT_FOUND,
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme index' first.`,
			},
		};
	}

	const ftsQuery = buildFts5Query(args.query, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: normalizeTr,
	});
	if (ftsQuery.length === 0) {
		return { ok: true, data: { query: args.query, hits: [] } };
	}

	const mtimeFrom = args.filters?.date_from
		? isoDateToUnix(args.filters.date_from)
		: undefined;
	const mtimeTo = args.filters?.date_to
		? isoDateToUnixEndOfDay(args.filters.date_to)
		: undefined;

	let raw: Fts5Hit[];
	try {
		raw = fts5Search({
			dbPath: vault.fts5Db,
			ftsQuery,
			limit: args.top_k,
			mtimeFrom,
			mtimeTo,
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

	const filtered = args.filters?.type
		? raw.filter((h) => h.frontmatterType === args.filters?.type)
		: raw;

	return {
		ok: true,
		data: {
			query: args.query,
			hits: filtered.map((h) => ({
				path: h.path,
				title: h.title,
				score: h.rank,
				snippet: h.contentRaw.slice(0, SNIPPET_CHARS),
				type: h.frontmatterType,
				mtime: h.mtime,
			})),
		},
	};
}
