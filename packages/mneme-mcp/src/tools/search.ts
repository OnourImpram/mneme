/**
 * mneme_search — FTS5 BM25 retrieval over the vault.
 *
 * v1.0 ships the FTS5 leg. Dense retrieval is roadmap. KG enrichment
 * is available through summarize and timeline when full-profile graph
 * state is active.
 */

import { existsSync } from "node:fs";
import { z } from "zod";
import { ERROR_CODES, toMnemeError } from "../errors.js";
import { type EvidenceCard, hitToEvidenceCard } from "../evidence_card.js";
import { neutralize } from "../injection.js";
import { resolveIndexProfile } from "../locale/resolve.js";
import { redact } from "../privacy.js";
import { bridgeTerms } from "../retrieval/bridge.js";
import { buildFts5Query, type Fts5Hit, fts5Search } from "../retrieval/fts5.js";
import { rerank } from "../retrieval/rerank.js";
import {
	computeQueryHash,
	emitSearchTelemetry,
} from "../retrieval/telemetry.js";
import { ScopeSchema } from "../scope.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";

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

export const SearchInputSchema = z.object({
	query: z
		.string()
		.min(1)
		.max(2048)
		.describe("Free-text query. Maximum 2048 characters."),
	top_k: z
		.number()
		.int()
		.positive()
		.max(50)
		.default(10)
		.describe("Maximum number of ranked results to return."),
	filters: z
		.object({
			date_from: z
				.string()
				.regex(/^\d{4}-\d{2}-\d{2}$/)
				.describe("Inclusive UTC date lower bound in YYYY-MM-DD form.")
				.optional(),
			date_to: z
				.string()
				.regex(/^\d{4}-\d{2}-\d{2}$/)
				.describe("Inclusive UTC date upper bound in YYYY-MM-DD form.")
				.optional(),
			type: z
				.enum(CANONICAL_MEMORY_TYPES)
				.describe("Canonical memory type filter.")
				.optional(),
		})
		.describe("Optional date and canonical memory-type filters.")
		.optional(),
	/** Hard floor below which the query is dropped to save context. */
	min_query_length: z
		.number()
		.int()
		.nonnegative()
		.default(0)
		.describe("Reject queries shorter than this many characters."),
	/**
	 * Scope filter. Omit to use config.defaultScope(). Pass "*" for
	 * cross-scope. Skipped when the index lacks the scope column.
	 */
	scope: ScopeSchema.describe(
		"Scope to search. Omit for the configured default scope. Pass '*' only for an explicit cross-scope query.",
	).optional(),
});

export type SearchInput = z.infer<typeof SearchInputSchema>;

export interface SearchOutput {
	query: string;
	/**
	 * Ranked evidence cards. Single source of results since 4.0.
	 *
	 * BREAKING (4.0): the `hits` array was removed. It duplicated every field
	 * of `cards` on the wire, doubling response size for no added information.
	 * Callers read `cards`; EvidenceCard is a superset of the old SearchHit
	 * (same fields plus confidenceLabel, backend and query).
	 */
	cards: EvidenceCard[];
	/**
	 * Deduplicated list of backend identifiers that contributed at least one
	 * hit to the cards array. 'fts5' is always present when hits are returned.
	 * Empty array when the query produced no hits (all tokens stripped, etc.).
	 * Future legs ('dense', 'kg', 'graphiti') appear here once wired.
	 */
	backends_used: string[];
}

/**
 * BM25 candidates fetched before reranking.
 *
 * Coverage reranking can only promote a document that is in the pool. On the
 * golden set the correct answer sat as deep as BM25 position 75, so a pool
 * the size of the requested page would have made the rerank a no-op for
 * exactly the queries it exists to fix. 200 covers every measured case with
 * headroom; the cost is one wider SQLite scan, not extra round trips.
 */
const RERANK_POOL = 200;

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

	// P6: locale mismatch guard. The index declares which normalizer built it
	// and the query path adopts that profile rather than imposing one; an
	// unknown or absent id fails closed instead of normalizing differently
	// than the stored tokens. Shared with prime/summarize/timeline, which
	// previously hardcoded the Turkish normalizer and so could not serve an
	// English index at all — one definition, four call sites.
	let resolved: ReturnType<typeof resolveIndexProfile>;
	try {
		resolved = resolveIndexProfile(vault.fts5Db);
	} catch (err) {
		return { ok: false, error: toMnemeError(err) };
	}
	if (!resolved.ok) return { ok: false, error: resolved.error };
	const { profile } = resolved;

	const ftsQuery = buildFts5Query(args.query, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: profile.normalize,
		expandTerm: bridgeTerms,
	});
	// Only locales that declare an ascii-fold key query the sibling table.
	// English has no dotted/dotless ambiguity to bridge, so it skips the leg
	// entirely rather than paying for a duplicate index scan.
	const ftsQueryAscii = profile.asciiFold
		? buildFts5Query(args.query, {
				minTokenLength: 2,
				stopwords: DEFAULT_STOPWORDS,
				normalize: profile.asciiFold,
				expandTerm: bridgeTerms,
			})
		: undefined;
	if (ftsQuery.length === 0 && !ftsQueryAscii) {
		const queryHash = computeQueryHash(
			vault.stateDir,
			profile.normalize(args.query),
		);
		emitSearchTelemetry(vault.stateDir, queryHash, 0, 0);
		return {
			ok: true,
			data: { query: args.query, cards: [], backends_used: [] },
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
			ftsQueryAscii,
			limit: args.top_k,
			poolSize: RERANK_POOL,
			mtimeFrom,
			mtimeTo,
			scope,
		});
	} catch (err) {
		const elapsedMs = Date.now() - searchStart;
		const queryHash = computeQueryHash(
			vault.stateDir,
			profile.normalize(args.query),
		);
		emitSearchTelemetry(
			vault.stateDir,
			queryHash,
			0,
			elapsedMs,
			[],
			{ fts5: 0 },
			{ fts5: elapsedMs },
			{
				fts5: {
					attempted: true,
					succeeded: false,
					failed: true,
					contributed: false,
				},
			},
		);
		return { ok: false, error: toMnemeError(err) };
	}
	const elapsedMs = Date.now() - searchStart;

	const typeFiltered = args.filters?.type
		? raw.filter((h) => h.frontmatterType === args.filters?.type)
		: raw;

	// Rerank the pool by term coverage, then cut to the requested page.
	// BM25 ranks term density; coverage ranks how many DISTINCT query terms a
	// document's title and path carry. See retrieval/rerank.ts for the
	// measured effect (hit@1 59% -> 85% on a 46-query golden set).
	const queryTokens = extractQueryTokens(args.query, profile.normalize);
	const filtered = rerank(typeFiltered, queryTokens)
		.slice(0, args.top_k)
		.map((r) => r.hit);

	// Emit retrieval telemetry (non-fatal — wrapped inside emitSearchTelemetry).
	const queryHash = computeQueryHash(
		vault.stateDir,
		profile.normalize(args.query),
	);
	emitSearchTelemetry(
		vault.stateDir,
		queryHash,
		filtered.length,
		elapsedMs,
		filtered.length > 0 ? ["fts5"] : [],
		{ fts5: filtered.length },
		{ fts5: elapsedMs },
		{
			fts5: {
				attempted: true,
				succeeded: true,
				failed: false,
				contributed: filtered.length > 0,
			},
		},
	);

	const cards: EvidenceCard[] = [];

	for (const h of filtered) {
		// Search returns STRUCTURED data (ranked hits), not a narrative
		// injection into the model's instruction stream, so wrapUntrusted is
		// intentionally absent here. neutralize is still applied to defang any
		// embedded fence sentinel in snippets/titles so a crafted note cannot
		// forge an untrusted-memory boundary inside a search result (G-3).
		const snippetStr = neutralize(
			redact(buildCenteredSnippet(h.bodyText, queryTokens, profile.normalize))
				.text,
		);
		cards.push(hitToEvidenceCard(h, args.query, snippetStr));
	}

	const backends_used = Array.from(new Set(cards.map((c) => c.backend)));

	return {
		ok: true,
		data: {
			query: args.query,
			cards,
			backends_used,
		},
	};
}
