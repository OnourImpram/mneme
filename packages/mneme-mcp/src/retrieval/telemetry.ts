/**
 * Retrieval telemetry emitter — TS side.
 *
 * Appends one JSONL line per search call to
 * `{stateDir}/telemetry/retrieval-YYYY-MM-DD.jsonl`.
 *
 * Design constraints (from lane spec):
 *   - Uses appendFileSync (not atomicWriteText) — append semantics match
 *     the Python side and are correct for multi-call accumulation.
 *   - mkdirSync with recursive:true before every append so the directory
 *     is created lazily on first use.
 *   - Entire function is wrapped in try/catch: telemetry is non-fatal and
 *     must never interrupt the search hot path.
 *   - No LLM/network I/O. No external dependencies beyond node builtins.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

export interface TelemetryRecord {
	ts: string;
	query_hash: string;
	hit_count: number;
	elapsed_ms: number;
	backends: string[];
	per_backend_hits: Record<string, number>;
	per_backend_latency_ms: Record<string, number>;
}

/**
 * Emit one search telemetry record to a date-stamped JSONL file.
 *
 * @param stateDir             Root state directory (vault's `.mneme/` equivalent).
 * @param queryHash            SHA-256 hex digest of the normalised query. The caller
 *                             (searchTool) computes this via
 *                             `createHash('sha256').update(normalizeTr(args.query)).digest('hex')`.
 * @param hitCount             Number of hits returned by fts5Search after filtering.
 * @param elapsedMs            Wall-clock time from search start to result return (ms).
 * @param backends             Deduplicated list of backend identifiers that contributed hits.
 * @param perBackendHits       Map of backend → hit count for that backend.
 * @param perBackendLatencyMs  Map of backend → wall-clock latency in ms for that backend.
 */
export function emitSearchTelemetry(
	stateDir: string,
	queryHash: string,
	hitCount: number,
	elapsedMs: number,
	backends: string[] = [],
	perBackendHits: Record<string, number> = {},
	perBackendLatencyMs: Record<string, number> = {},
): void {
	try {
		const today = new Date().toISOString().slice(0, 10);
		const telemetryDir = join(stateDir, "telemetry");
		mkdirSync(telemetryDir, { recursive: true });

		const record: TelemetryRecord = {
			ts: new Date().toISOString(),
			query_hash: queryHash,
			hit_count: hitCount,
			elapsed_ms: elapsedMs,
			backends,
			per_backend_hits: perBackendHits,
			per_backend_latency_ms: perBackendLatencyMs,
		};

		const filePath = join(telemetryDir, `retrieval-${today}.jsonl`);
		appendFileSync(filePath, `${JSON.stringify(record)}\n`, "utf8");
	} catch {
		// Non-fatal: telemetry must never interrupt the search hot path.
	}
}
