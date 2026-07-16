/**
 * mneme_search — FTS5 BM25 retrieval over the vault.
 *
 * v1.0 ships the FTS5 leg. Dense retrieval is roadmap. KG enrichment
 * is available through summarize and timeline when full-profile graph
 * state is active.
 */

import { existsSync } from "node:fs";
import Database from "better-sqlite3";
import { z } from "zod";
import { ERROR_CODES, toMnemeError } from "../errors.js";
import { type EvidenceCard, hitToEvidenceCard } from "../evidence_card.js";
import { neutralize } from "../injection.js";
import { normalizeTr } from "../locale/tr.js";
import { redact } from "../privacy.js";
import { buildFts5Query, type Fts5Hit, fts5Search } from "../retrieval/fts5.js";
import {
	computeQueryHash,
	emitSearchTelemetry,
} from "../retrieval/telemetry.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";
import { ScopeSchema } from "../scope.js";

/**
 * The full set of canonical memory types indexed by mneme_core.
 *
 * Mirrors packages/mneme-core/tests/fixtures/canonical_memory_types.json.
 * A parity test in tests/tools/search.test.ts asserts these two lists
 * are identical so they cannot drift independently.
 */

export const CANONICAL_MEMORY_TYPES = [
	"session",
	"topic",
	"reference",
	"pattern",
	"trajectory",
	"compressed",
	"observation",
	"session_summary",
	"user_prompt",
	"claim",
	"failure",
] as const;

export type CanonicalMemoryType = (typeof CANONICAL_MEMORY_TYPES)[number];

/**
 * The normalizer profile written into index_meta by `mneme index rebuild
 * --locale tr`. The TS query path always uses normalizeTr which implements
 * the tr-cldr fold, so this is the expected profile value.
 */
const QUERY_SIDE_NORMALIZER_PROFILE = "tr-cldr";

export const SearchInputSchema = z.object({
	query: z.string().min(1).max(2048),
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
			type: z.enum(CANONICAL_MEMORY_TYPES).optional(),
		})
		.optional(),
	/** Hard floor below which the query is dropped to save context. */
	min_query_length: z.number().int().nonnegative().default(0),
	/**
	 * Scope filter. Omit to use config.defaultScope(). Pass "*" for
	 * cross-scope. Concrete-scope reads require a scope-aware index.
	 */
	scope: ScopeSchema.optional(),
});

export type SearchInput = z.infer<typeof SearchInputSchema>;

/** @deprecated Prefer EvidenceCard from the cards field. */
export interface SearchHit {
	path: string;
	title: string;
	score: number;
	snippet: string;
	type: string;
	mtime: number;
	contentHash: string;
	trust: string;
}

export interface SearchOutput {
	query: string;
	hits: SearchHit[];
	cards: EvidenceCard[];
	/**
	 * Deduplicated list of backend identifiers that contributed at least one
	 * hit to the cards array. 'fts5' is always present when hits are returned.
	 * Empty array when the query produced no hits (all tokens stripped, etc.).
	 * Future legs ('dense', 'kg', 'graphiti') appear here once wired.
	 */
	backends_used: string[];
}

const SNIPPET_CHARS = 200;
const SNIPPET_HALF = Math.floor(SNIPPET_CHARS / 2);
const ELLIPSIS = "…";

/**
 * Build a match-centered snippet from bodyText.
 *
 * Finds the first occurrence of any query token (using the same normalized
 * tokens that the FTS query used) and returns a window of up to SNIPPET_CHARS
 * characters centred on that match, padded with ellipses where text was
 * trimmed. Falls back to a prefix slice when no token is found.
 *
 * Redaction and neutralization are applied by the caller AFTER this function
 * so that the token-search works on raw text but the returned slice is still
 * safe.
 *
 * INVARIANT: the `normalize` function passed here MUST be length-preserving
 * in JS char units (i.e. normalize(s).length === s.length for all s). This
 * is satisfied by `normalizeTr`, which only substitutes one char for one char
 * (İ→i, I→ı, then toLowerCase which is length-stable for the BMP characters
 * present in the vault). If this invariant ever breaks, matchPos found in
 * normalizedBody would index incorrectly into the original bodyText.
 */
function buildCenteredSnippet(
	bodyText: string,
	queryTokens: string[],
	normalize: (s: string) => string,
): string {
	const normalizedBody = normalize(bodyText);

	let matchPos = -1;
	for (const token of queryTokens) {
		if (token.length === 0) continue;
		const idx = normalizedBody.indexOf(token);
		if (idx !== -1 && (matchPos === -1 || idx < matchPos)) {
			matchPos = idx;
		}
	}

	if (matchPos === -1) {
		// No token found — fall back to prefix.
		return bodyText.slice(0, SNIPPET_CHARS);
	}

	let start = Math.max(0, matchPos - SNIPPET_HALF);
	// Guard: never split a UTF-16 surrogate pair. If `start` lands on a low
	// surrogate (U+DC00–U+DFFF), nudge it one code unit back so the pair
	// remains intact.
	if (start > 0) {
		const c = bodyText.charCodeAt(start);
		if (c >= 0xdc00 && c <= 0xdfff) {
			start -= 1;
		}
	}

	let end = Math.min(bodyText.length, start + SNIPPET_CHARS);
	// Guard: never split a UTF-16 surrogate pair at the trailing edge. If
	// `end` lands on a low surrogate, nudge it one code unit forward.
	if (end < bodyText.length) {
		const c = bodyText.charCodeAt(end);
		if (c >= 0xdc00 && c <= 0xdfff) {
			end += 1;
		}
	}

	const slice = bodyText.slice(start, end);

	const prefix = start > 0 ? ELLIPSIS : "";
	const suffix = end < bodyText.length ? ELLIPSIS : "";
	return prefix + slice + suffix;
}

/**
 * Extract the normalized tokens that survived the FTS query filter.
 * Mirrors the logic in buildFts5Query so we search the same tokens
 * for snippet centering.
 */
function extractQueryTokens(
	rawQuery: string,
	normalize: (s: string) => string,
): string[] {
	const tokens: string[] = [];
	for (const word of rawQuery.split(/\s+/)) {
		for (const part of word.split(/[-":^*()]+/)) {
			const normed = normalize(part);
			if (normed.length >= 2 && !DEFAULT_STOPWORDS.has(normed)) {
				tokens.push(normed);
			}
		}
	}
	return tokens;
}

/**
 * Read the normalization_profile row from index_meta if the table and row
 * exist. Returns null when the table is absent or the row is missing (legacy
 * index — soft-degrade). Throws only on unexpected I/O failures.
 */
function readIndexProfile(dbPath: string): string | null {
	const db = new Database(dbPath, { readonly: true, fileMustExist: true });
	try {
		db.pragma("query_only = ON");
		// Check whether the index_meta table exists at all.
		const tableRow = db
			.prepare(
				"SELECT name FROM sqlite_master WHERE type='table' AND name='index_meta'",
			)
			.get() as { name: string } | undefined;
		if (!tableRow) return null;

		const row = db
			.prepare(
				"SELECT value FROM index_meta WHERE key = 'normalization_profile'",
			)
			.get() as { value: string } | undefined;
		return row ? row.value : null;
	} finally {
		db.close();
	}
}

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

	// P6: locale mismatch guard.
	// Read the profile stored at index-build time. If it IS present and
	// differs from what the query side uses, fail fast — results would be
	// garbled. If absent (legacy index), proceed with a warning.
	let indexProfile: string | null;
	try {
		indexProfile = readIndexProfile(vault.fts5Db);
	} catch (err) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: err instanceof Error ? err.message : String(err),
			},
		};
	}

	if (indexProfile !== null && indexProfile !== QUERY_SIDE_NORMALIZER_PROFILE) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH,
				message: `Index normalizer profile '${indexProfile}' does not match query-side profile '${QUERY_SIDE_NORMALIZER_PROFILE}'. Rebuild the index with 'mneme index rebuild --locale tr' to fix.`,
			},
		};
	}

	if (indexProfile === null) {
		// Legacy index — no profile row. Soft-degrade: log and proceed.
		// (console.warn is acceptable; this is not on a critical/Stop path.)
		console.warn(
			"[mneme-mcp] index_meta.normalization_profile absent — " +
				"legacy index detected; proceeding without locale validation.",
		);
	}

	const ftsQuery = buildFts5Query(args.query, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: normalizeTr,
	});
	if (ftsQuery.length === 0) {
		return {
			ok: true,
			data: { query: args.query, hits: [], cards: [], backends_used: [] },
		};
	}

	const mtimeFrom = args.filters?.date_from
		? isoDateToUnix(args.filters.date_from)
		: undefined;
	const mtimeTo = args.filters?.date_to
		? isoDateToUnixEndOfDay(args.filters.date_to)
		: undefined;

	const scope = args.scope ?? vault.defaultScope();

	let raw: Fts5Hit[];
	const searchStart = Date.now();
	try {
		raw = fts5Search({
			dbPath: vault.fts5Db,
			ftsQuery,
			limit: args.top_k,
			mtimeFrom,
			mtimeTo,
			scope,
		});
	} catch (err) {
		return { ok: false, error: toMnemeError(err) };
	}
	const elapsedMs = Date.now() - searchStart;

	const filtered = args.filters?.type
		? raw.filter((h) => h.frontmatterType === args.filters?.type)
		: raw;

	// Emit retrieval telemetry (non-fatal — wrapped inside emitSearchTelemetry).
	const queryHash = computeQueryHash(vault.stateDir, normalizeTr(args.query));
	emitSearchTelemetry(
		vault.stateDir,
		queryHash,
		filtered.length,
		elapsedMs,
		["fts5"],
		{ fts5: filtered.length },
		{ fts5: elapsedMs },
	);

	// Extract normalized tokens for match-centered snippeting (T7).
	const queryTokens = extractQueryTokens(args.query, normalizeTr);

	const hits: SearchHit[] = [];
	const cards: EvidenceCard[] = [];

	for (const h of filtered) {
		// Search returns STRUCTURED data (ranked hits), not a narrative
		// injection into the model's instruction stream, so wrapUntrusted is
		// intentionally absent here. neutralize is still applied to defang any
		// embedded fence sentinel in snippets/titles so a crafted note cannot
		// forge an untrusted-memory boundary inside a search result (G-3).
		const snippetStr = neutralize(
			redact(buildCenteredSnippet(h.bodyText, queryTokens, normalizeTr)).text,
		);
		hits.push({
			path: h.path,
			title: neutralize(h.title),
			score: h.rank,
			snippet: snippetStr,
			type: h.frontmatterType,
			mtime: h.mtime,
			contentHash: h.contentHash,
			trust: h.trust,
		});
		cards.push(hitToEvidenceCard(h, args.query, snippetStr));
	}

	const backends_used = Array.from(new Set(cards.map((c) => c.backend)));

	return {
		ok: true,
		data: {
			query: args.query,
			hits,
			cards,
			backends_used,
		},
	};
}
