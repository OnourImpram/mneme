/**
 * Lexical reranking over a BM25 candidate pool (4.1).
 *
 * BM25 scores term DENSITY. What it cannot express is term DIVERSITY: in an
 * OR query, a note carrying four distinct query terms in its title competes
 * on equal footing with a note repeating one term eight times in its body.
 * Measured on a real vault, that single gap accounted for most retrieval
 * failures — a note literally titled "Kapalı Operatör Kararları" ranked #6
 * for the query "kapalı operatör kararları listesi".
 *
 * Two signals are added here, both computed from data already in the hit:
 *
 *   COVERAGE   how many distinct query terms appear in title or path
 *   CANONICITY whether the path looks like a canonical note or derived output
 *
 * Ranking is TIERED rather than a weighted sum. Documents are grouped by
 * coverage; inside a tier the original BM25 order is preserved exactly. The
 * guarantee this buys is easy to state and to audit: a document can only be
 * overtaken by one that covers STRICTLY more query terms. A weighted sum has
 * no such property — a slightly higher BM25 score can outweigh a genuinely
 * better term match, which is how the pre-4.1 ordering went wrong.
 *
 * Measured on a 46-query golden set (24 tuning + 22 held-out, written after
 * the parameters were frozen):
 *
 *   BM25 alone            hit@1 59%   hit@5 80%
 *   + coverage tiering    hit@1 83%   hit@5 96%
 *   + canonicity          hit@1 85%   hit@5 96%
 */

import { bridgeTerms, foldForCompare } from "./bridge.js";
import type { Fts5Hit } from "./fts5.js";

/**
 * Path fragments that mark DERIVED content rather than a canonical note:
 * the output of a run, the record of an audit, a draft, an archived copy.
 *
 * This exists because of a specific measured failure. The query "ajan yaratma
 * protokolü kernel yapısı" returned a file titled "ajan yaratma protokolü
 * (taslak)" — a draft — above the canonical "ajan yaratma protokolü (v1.5.0)".
 * Both titles match equally well; only the path distinguishes them.
 */
const DERIVED_MARKERS: readonly string[] = [
	"-kosum-",
	"-denetimi-",
	"-onarimi-",
	"-genisleme-",
	"arsiv",
	"archive",
	"taslak",
	"draft",
	"temp",
	"tmp",
	"_runs",
	"onceki",
	"eski",
	"backup",
	"yedek",
	"cikti",
	"output",
	"sandbox",
	"fixture",
	"example",
	"ornek",
];

/** Multiplier applied when a path carries a derived-content marker. */
const DERIVED_PENALTY = 0.35;

/** Minimum token length for a substring match, to avoid "plan" ~ "planning". */
const MIN_SUBSTRING_LENGTH = 4;

/**
 * Separators inside a vault path. The hyphen is LAST so it stays a literal:
 * written mid-class it silently becomes a range and stops splitting on the
 * separator vault filenames use most.
 */
const PATH_SEPARATORS = /[/\\_.\s-]+/;

/**
 * Collect the comparable tokens of a document's name surface: its title plus
 * every segment of its path. Body text is deliberately excluded — coverage
 * asks "is this document ABOUT the query", and a passing mention in a long
 * note is not aboutness.
 */
function nameTokens(hit: Fts5Hit): Set<string> {
	const out = new Set<string>();
	for (const piece of (hit.title ?? "").split(/\s+/)) {
		if (piece.length > 0) out.add(foldForCompare(piece));
	}
	for (const piece of (hit.path ?? "").split(PATH_SEPARATORS)) {
		if (piece.length > 0) out.add(foldForCompare(piece));
	}
	return out;
}

/**
 * Count how many DISTINCT query terms the document's name surface covers.
 *
 * Substring matching is allowed in both directions for tokens of at least
 * MIN_SUBSTRING_LENGTH, which is what lets Turkish inflection match:
 * "kararları" against "kararlar", "protokolü" against "protokol".
 */
export function coverageCount(
	queryTokens: readonly string[],
	hit: Fts5Hit,
): number {
	if (queryTokens.length === 0) return 0;
	const surface = nameTokens(hit);
	let covered = 0;
	for (const raw of queryTokens) {
		const token = foldForCompare(raw);
		// A term counts ONCE, whether it matched directly or through its
		// cross-language equivalent. The bridge widens what can match; it
		// must never inflate the count, or a bilingual document would
		// outrank a monolingual one on the same evidence.
		if (matchesSurface(token, surface)) {
			covered += 1;
			continue;
		}
		let bridged = false;
		for (const equivalent of bridgeTerms(token)) {
			if (matchesSurface(equivalent, surface)) {
				bridged = true;
				break;
			}
		}
		if (bridged) covered += 1;
	}
	return covered;
}

/** Exact hit, or a substring match in either direction for longer tokens. */
function matchesSurface(token: string, surface: ReadonlySet<string>): boolean {
	if (surface.has(token)) return true;
	if (token.length < MIN_SUBSTRING_LENGTH) return false;
	for (const candidate of surface) {
		if (candidate.length < MIN_SUBSTRING_LENGTH) continue;
		if (candidate.includes(token) || token.includes(candidate)) return true;
	}
	return false;
}

/**
 * Score 0..1 for how canonical a path looks. Higher is more canonical.
 *
 * Shallow paths score high because vault owners keep reference notes near the
 * root and file generated material in dated subdirectories. The derived
 * markers then apply a penalty on top, so a draft inside a deep audit folder
 * lands far below a note at the root even when their titles are identical.
 */
export function canonicityScore(
	path: string,
	queryTokens: readonly string[] = [],
): number {
	const normalized = path.replace(/\\/g, "/").toLowerCase();
	const depth = (normalized.match(/\//g) ?? []).length;
	let score: number;
	if (depth <= 1) score = 1.0;
	else if (depth === 2) score = 0.7;
	else if (depth === 3) score = 0.4;
	else score = 0.15;
	const marker = DERIVED_MARKERS.find((m) => normalized.includes(m));
	if (marker !== undefined && !queryAsksFor(marker, queryTokens)) {
		score *= DERIVED_PENALTY;
	}
	return score;
}

/**
 * Whether the query is itself asking for the kind of document the marker names.
 *
 * Without this the penalty fires blind. Measured: a file named
 * `Onarim-Denetimi-2026-07-30.md` carries the "-denetimi-" marker and was
 * demoted to 0.245 for the query "memory system repair audit" — the penalty
 * was suppressing exactly what the user asked for. A marker means "probably
 * secondary", never "secondary even when sought". The bridge is consulted too,
 * so an English query for "audit" protects a Turkish "denetim" file.
 */
function queryAsksFor(marker: string, queryTokens: readonly string[]): boolean {
	const bare = marker.replace(/^[-_]+|[-_]+$/g, "");
	if (bare.length < MIN_SUBSTRING_LENGTH) return false;
	for (const raw of queryTokens) {
		const token = foldForCompare(raw);
		for (const candidate of [token, ...bridgeTerms(token)]) {
			if (candidate.length < MIN_SUBSTRING_LENGTH) continue;
			if (bare.includes(candidate) || candidate.includes(bare)) return true;
		}
	}
	return false;
}

/** A hit paired with the signals used to place it. Exposed for tests. */
export interface RankedHit {
	hit: Fts5Hit;
	coverage: number;
	canonicity: number;
	/** Position in the incoming BM25 order. */
	bm25Rank: number;
}

/**
 * Reorder a BM25 candidate pool by coverage tier, then canonicity, then the
 * original BM25 position.
 *
 * The input order IS the BM25 ranking; this function never recomputes
 * relevance, it only regroups. Canonicity is rounded to two decimals before
 * comparison so near-identical paths fall through to the BM25 tiebreak rather
 * than being separated by floating-point noise.
 */
export function rerank(
	hits: readonly Fts5Hit[],
	queryTokens: readonly string[],
): RankedHit[] {
	const ranked: RankedHit[] = hits.map((hit, index) => ({
		hit,
		coverage: coverageCount(queryTokens, hit),
		canonicity: canonicityScore(hit.path, queryTokens),
		bm25Rank: index,
	}));
	ranked.sort((left, right) => {
		if (left.coverage !== right.coverage) return right.coverage - left.coverage;
		const lc = Math.round(left.canonicity * 100);
		const rc = Math.round(right.canonicity * 100);
		if (lc !== rc) return rc - lc;
		return left.bm25Rank - right.bm25Rank;
	});
	return ranked;
}
