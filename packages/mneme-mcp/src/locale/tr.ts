/**
 * Turkish casefold normalizer.
 *
 * Mirrors `mneme_core.fts5.locale.tr.normalize_tr` so Python indexer and
 * TS retrieval emit identical tokens. Two semantic rules apply BEFORE
 * standard lowercase, which is the order that fixes the I/ı bug:
 *
 *   dotted capital I    (U+0130, İ)  -> dotted small i    (U+0069, i)
 *   dotless capital I   (U+0049, I)  -> dotless small ı   (U+0131, ı)
 *
 * Naive `.toLowerCase()` collapses both to `i`, which breaks dotted vs
 * dotless distinction (e.g. "KIYASLAMA" -> "kıyaslama" correct,
 * "kiyaslama" incorrect).
 *
 * `normalizeTrForFts` collapses internal whitespace runs for body text
 * before FTS5 indexing.
 */

export function normalizeTr(s: string): string {
	if (typeof s !== "string") return "";
	return s.replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase();
}

export function normalizeTrForFts(s: string): string {
	return normalizeTr(s).replace(/\s+/g, " ").trim();
}

/**
 * Recall-aid fold that collapses both Turkish i-forms onto ASCII `i`.
 *
 * Mirrors `mneme_core.fts5.locale.tr.normalize_tr_ascii_fold`. Unlike
 * `normalizeTr` (CLDR-faithful, keeps dotted vs dotless distinct), this
 * deliberately maps İ/I/ı/i all to plain `i` so that an ASCII-capital query
 * recalls a document stored with the proper dotted Turkish spelling. It is a
 * retrieval key only, never a display transform.
 */
export function normalizeTrAsciiFold(s: string): string {
	if (typeof s !== "string") return "";
	return normalizeTr(s).replace(/ı/g, "i");
}

export function normalizeTrAsciiFoldForFts(s: string): string {
	return normalizeTrAsciiFold(s).replace(/\s+/g, " ").trim();
}
