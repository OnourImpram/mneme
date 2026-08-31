/**
 * Resolve the locale profile an index was BUILT with, for the query side.
 *
 * WHY THIS EXISTS
 * `mneme_search` already did this: read `index_meta.normalization_profile`,
 * look the profile up, and fold queries the way the stored tokens were folded.
 * `mneme_prime`, `mneme_summarize` and `mneme_timeline` did not — they imported
 * `normalizeTr`/`normalizeTrAsciiFold` directly and passed an ASCII arm on
 * every call. `fts5Search` refuses an ASCII arm unless the index declares
 * `ascii_normalization_profile = 'tr-ascii-fold'`, so on an English index all
 * three tools failed outright with INDEX_STALE_OR_LOCALE_MISMATCH — measured,
 * for every query, including ones with no Turkish characters at all.
 *
 * That stayed invisible while `--locale en` could not produce a usable index
 * in the first place. Fixing the CLI is what made this reachable, so the two
 * belong in the same release.
 *
 * The index declares its own normalizer; the query side adopts it. An unknown
 * id means the index was written by a newer client, so this fails closed
 * rather than normalizing differently than the stored tokens.
 */

import Database from "better-sqlite3";
import { ERROR_CODES, type MnemeError } from "../errors.js";
import {
	type LocaleProfile,
	profileById,
	supportedProfileIds,
} from "./index.js";

/**
 * Read `index_meta.normalization_profile`, tolerating databases that predate
 * the table. Returns null when the table or the row is absent, so the caller
 * can reject an unverified legacy index rather than guessing at one.
 */
export function readIndexProfile(dbPath: string): string | null {
	const db = new Database(dbPath, { readonly: true, fileMustExist: true });
	try {
		db.pragma("query_only = ON");
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

export type ProfileResolution =
	| { readonly ok: true; readonly profile: LocaleProfile }
	| { readonly ok: false; readonly error: MnemeError };

/**
 * Resolve the profile for an index, or the error explaining why it cannot be
 * served. Callers hand the error straight back as their own tool result, so
 * every tool reports a locale problem identically.
 */
export function resolveIndexProfile(dbPath: string): ProfileResolution {
	const indexProfile = readIndexProfile(dbPath);
	if (indexProfile === null) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH,
				message:
					"The FTS5 index has no normalizer profile and cannot be trusted for locale-sensitive retrieval. " +
					"Run 'mneme-core index rebuild' before retrying.",
			},
		};
	}
	const profile = profileById(indexProfile);
	if (profile === undefined) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH,
				message:
					`Index normalizer profile '${indexProfile}' cannot serve locale-sensitive retrieval. ` +
					`Supported profiles: ${supportedProfileIds()}. ` +
					"Rebuild the index with 'mneme-core index rebuild --locale <tr|en>', " +
					"or upgrade mneme if this index was written by a newer release.",
			},
		};
	}
	return { ok: true, profile };
}
