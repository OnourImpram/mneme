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

import { createHash, createHmac, randomBytes } from "node:crypto";
import {
	appendFileSync,
	existsSync,
	mkdirSync,
	readFileSync,
	writeFileSync,
} from "node:fs";
import { join } from "node:path";

const HMAC_KEY_FILE = "telemetry-hmac.key";
const HMAC_KEY_BYTES = 32;

function loadOrCreateHmacKey(stateDir: string): Buffer {
	const keyPath = join(stateDir, HMAC_KEY_FILE);
	try {
		if (existsSync(keyPath)) {
			const key = readFileSync(keyPath);
			if (key.length === HMAC_KEY_BYTES) return key;
		}
	} catch {
		// Fall through to generate a new key.
	}
	const key = randomBytes(HMAC_KEY_BYTES);
	try {
		mkdirSync(stateDir, { recursive: true });
		writeFileSync(keyPath, key, { mode: 0o600 });
	} catch {
		// Best-effort persist; return ephemeral key on write failure.
	}
	return key;
}

/**
 * Derive a privacy-preserving hash of a normalized query using a per-vault
 * HMAC-SHA256 key. Different vaults produce different hashes for the same
 * query, preventing cross-vault correlation while preserving within-vault
 * deduplication. Falls back to plain SHA-256 if key I/O fails.
 */
export function computeQueryHash(
	stateDir: string,
	normalizedQuery: string,
): string {
	try {
		const key = loadOrCreateHmacKey(stateDir);
		return createHmac("sha256", key).update(normalizedQuery).digest("hex");
	} catch {
		return createHash("sha256").update(normalizedQuery).digest("hex");
	}
}

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
