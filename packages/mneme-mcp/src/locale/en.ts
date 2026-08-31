/**
 * English (and default Latin) normalizer for FTS5.
 *
 * Mirrors `mneme_core.fts5.locale.en.normalize_en` so the Python indexer and
 * the TS retrieval path emit identical tokens.
 *
 * This is plain Unicode lowercase — deliberately NOT the Turkish fold. The
 * distinction matters in both directions:
 *
 *   normalizeTr("API")  -> "apı"   (I is a distinct Turkish letter)
 *   normalizeEn("API")  -> "api"
 *
 * Before 4.0 every vault was indexed with the Turkish fold regardless of
 * content, so an English corpus stored "apı" and "ı/o". Queries matched
 * because the same fold ran at query time, which made the defect invisible:
 * retrieval "worked" while the two languages were indistinguishable.
 *
 * There is no ASCII-fold sibling here. That key exists to bridge Turkish
 * dotted/dotless i, a problem English does not have; adding one would only
 * duplicate the index for no recall gain.
 *
 * LENGTH INVARIANT. `buildCenteredSnippet` locates a match in the NORMALIZED
 * body and then slices the ORIGINAL body at that offset, so a normalizer must
 * map one JS char unit to exactly one. Bare `.toLowerCase()` breaks this:
 *
 *   "İ".toLowerCase()  ->  "i̇"   (1 unit becomes 2)
 *
 * U+0130 is therefore folded to plain `i` explicitly, BEFORE lowercasing.
 * Dotless `I` is deliberately NOT touched — mapping it to `ı` is the Turkish
 * rule and would be wrong for English. An English note that mentions
 * "İstanbul" now normalizes without shifting every subsequent offset.
 */

export function normalizeEn(s: string): string {
	if (typeof s !== "string") return "";
	return s.replace(/İ/g, "i").toLowerCase();
}

/** Lowercase + collapse whitespace runs, for body text before FTS5 ingest. */
export function normalizeEnForFts(s: string): string {
	return normalizeEn(s).replace(/\s+/g, " ").trim();
}
