/**
 * Shared types for MCP tool handlers.
 *
 * Each handler returns a discriminated union so callers can branch on
 * `ok` instead of catching exceptions. The `index.ts` MCP wrapper
 * translates this union into the `{ content, isError }` envelope the
 * MCP SDK expects.
 */

import type { MnemeError } from "../errors.js";

export type ToolResult<T> =
	| { ok: true; data: T }
	| { ok: false; error: MnemeError };

const STOPWORDS_TR: ReadonlySet<string> = new Set([
	"ve",
	"ile",
	"icin",
	"bir",
	"bu",
	"su",
	"o",
	"da",
	"de",
	"gibi",
	"kadar",
	"ama",
	"fakat",
	"ya",
	"hem",
	// Question and filler words, added in 4.1. These carry the shape of a
	// question but distinguish no document, and they were actively harmful:
	// ranking measures how many query terms a document covers, so "ne zaman
	// aciliyor" inflated the denominator and pushed a correct 2-of-2 match
	// down to 2-of-6. Measured on the golden set: +4% hit@1.
	"ne",
	"neden",
	"nasil",
	"nedir",
	"kim",
	"kime",
	"nerede",
	"nereye",
	"hangi",
	"kac",
	"zaman",
	"aciliyor",
	"oluyor",
	"olur",
	"var",
	"yok",
	"mi",
	"mu",
	"midir",
]);

const STOPWORDS_EN: ReadonlySet<string> = new Set([
	"the",
	"a",
	"an",
	"and",
	"or",
	"but",
	"of",
	"to",
	"in",
	"on",
	"for",
	"is",
	"are",
	"was",
	"were",
	"be",
	"been",
	"being",
	// English question words, same reasoning as the Turkish additions above.
	"what",
	"when",
	"where",
	"which",
	"how",
	"why",
	"who",
	"does",
	"did",
	"do",
]);

/**
 * Default stopword set combining the English and Turkish samplers.
 * Locale-tuned stopword lists can replace this via tool config.
 */
export const DEFAULT_STOPWORDS: ReadonlySet<string> = new Set([
	...STOPWORDS_TR,
	...STOPWORDS_EN,
]);

/**
 * Convert a YYYY-MM-DD date string to a Unix timestamp at UTC midnight.
 * Throws on malformed input.
 */
export function isoDateToUnix(dateStr: string): number {
	if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
		throw new Error(
			`Invalid ISO date format (expected YYYY-MM-DD): ${dateStr}`,
		);
	}
	const t = Date.parse(`${dateStr}T00:00:00Z`);
	if (Number.isNaN(t)) throw new Error(`Unparseable date: ${dateStr}`);
	return Math.floor(t / 1000);
}

/** Inclusive end-of-day Unix timestamp for a YYYY-MM-DD date. */
export function isoDateToUnixEndOfDay(dateStr: string): number {
	return isoDateToUnix(dateStr) + 86399;
}
