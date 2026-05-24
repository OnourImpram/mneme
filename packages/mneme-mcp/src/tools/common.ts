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
