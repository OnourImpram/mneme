/**
 * EvidenceCard — the typed boundary between FTS5 retrieval and Phase-2
 * composition.
 *
 * FTS5 hits carry provenance (source_path, content_hash, timestamp, trust)
 * per Invariant 4 and a confidence label per Invariant 5. For FTS5 hits the
 * label is always EXTRACTED: the index extracted this content verbatim from
 * the vault. INFERRED and AMBIGUOUS are reserved for KG-derived and
 * probabilistic relations introduced in Phase 2+.
 */

import type { Fts5Hit } from "./retrieval/fts5.js";

/**
 * Provenance confidence label for an EvidenceCard.
 *
 * EXTRACTED — content was read directly from the vault (FTS5 retrieval leg).
 * INFERRED  — relation or claim derived by reasoning over multiple sources.
 * AMBIGUOUS — confidence is low or the source is uncertain.
 *
 * String union (not enum) for transparent JSON serialisation that matches
 * the Python-side string literals used in the KG and audit modules.
 */
export type ConfidenceLabel = "EXTRACTED" | "INFERRED" | "AMBIGUOUS";

/**
 * A single memory unit ready for presentation to the caller.
 *
 * Fields mirror SearchHit for backward compat while adding the provenance
 * fields required by Invariant 4 (contentHash, trust) and Invariant 5
 * (confidenceLabel). The query field enables round-trip attribution so
 * Phase-2 composition can group cards by originating query.
 *
 * backend identifies which retrieval leg produced this card. Current values:
 *   'fts5'    — BM25 full-text search (the only active leg in v1.x)
 *   'dense'   — dense vector retrieval (roadmap)
 *   'kg'      — knowledge-graph leg (roadmap)
 *   'graphiti'— Graphiti temporal KG (roadmap)
 */
export interface EvidenceCard {
	path: string;
	title: string;
	score: number;
	snippet: string;
	type: string;
	mtime: number;
	contentHash: string;
	trust: string;
	confidenceLabel: ConfidenceLabel;
	backend: string;
	query: string;
}

/**
 * Convert an Fts5Hit and its post-processed snippet into an EvidenceCard.
 *
 * The snippet MUST already be redacted and neutralized by the caller before
 * passing here — this function does not apply privacy transforms.
 *
 * confidenceLabel is unconditionally EXTRACTED for FTS5 hits: the retrieval
 * engine read this content from the vault index, making it a direct
 * extraction rather than an inference.
 *
 * backend identifies which retrieval leg produced the hit. Defaults to 'fts5'
 * for all current callers. Future legs (dense, kg, graphiti) pass their own
 * identifier so callers can observe which backend answered.
 */
export function hitToEvidenceCard(
	hit: Fts5Hit,
	query: string,
	snippet: string,
	backend = "fts5",
): EvidenceCard {
	return {
		path: hit.path,
		title: hit.title,
		score: hit.rank,
		snippet,
		type: hit.frontmatterType,
		mtime: hit.mtime,
		contentHash: hit.contentHash,
		trust: hit.trust,
		confidenceLabel: "EXTRACTED",
		backend,
		query,
	};
}
