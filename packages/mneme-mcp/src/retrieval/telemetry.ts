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

import { createHmac, randomBytes } from "node:crypto";
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
const QUERY_HASH_RE = /^[a-f0-9]{64}$/;
const TELEMETRY_BACKENDS = ["fts5", "dense", "kg", "graphiti"] as const;
type TelemetryBackend = (typeof TELEMETRY_BACKENDS)[number];

const ephemeralHmacKeys = new Map<string, Buffer>();

function ephemeralHmacKey(stateDir: string): Buffer {
	const existing = ephemeralHmacKeys.get(stateDir);
	if (existing) return existing;
	const key = randomBytes(HMAC_KEY_BYTES);
	ephemeralHmacKeys.set(stateDir, key);
	return key;
}

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
	const key = ephemeralHmacKey(stateDir);
	try {
		mkdirSync(stateDir, { recursive: true });
		writeFileSync(keyPath, key, { flag: "wx", mode: 0o600 });
		return key;
	} catch {
		try {
			const persisted = readFileSync(keyPath);
			if (persisted.length === HMAC_KEY_BYTES) return persisted;
		} catch {
			// Keep the process-local per-vault key when persistence is unavailable.
		}
	}
	return key;
}

/**
 * Derive a privacy-preserving hash of a normalized query using a per-vault
 * HMAC-SHA256 key. Different vaults produce different hashes for the same
 * query, preventing cross-vault correlation while preserving within-vault
 * deduplication. If key persistence is unavailable, a process-local per-vault
 * HMAC key is used. The function never falls back to an unkeyed digest.
 */
export function computeQueryHash(
	stateDir: string,
	normalizedQuery: string,
): string {
	try {
		const key = loadOrCreateHmacKey(stateDir);
		return createHmac("sha256", key).update(normalizedQuery).digest("hex");
	} catch {
		return "";
	}
}

function sanitizeMetric(
	value: number,
	{ integer = false }: { integer?: boolean } = {},
): number {
	if (!Number.isFinite(value) || value < 0) return 0;
	const bounded = Math.min(value, Number.MAX_SAFE_INTEGER);
	return integer ? Math.floor(bounded) : bounded;
}

function sanitizeMetricMap(
	values: Record<string, number>,
	{ integer }: { integer: boolean },
): Record<string, number> {
	const sanitized: Partial<Record<TelemetryBackend, number>> = {};
	for (const backend of TELEMETRY_BACKENDS) {
		if (!Object.hasOwn(values, backend)) continue;
		const value = values[backend];
		if (typeof value === "number") {
			sanitized[backend] = sanitizeMetric(value, { integer });
		}
	}
	return sanitized;
}

function sanitizeStatuses(
	values: Record<string, BackendTelemetryStatus>,
): Record<string, BackendTelemetryStatus> {
	const sanitized: Partial<Record<TelemetryBackend, BackendTelemetryStatus>> =
		{};
	for (const backend of TELEMETRY_BACKENDS) {
		if (!Object.hasOwn(values, backend)) continue;
		const status = values[backend];
		if (!status || typeof status !== "object") continue;
		const attempted = status.attempted === true;
		const failed = attempted && status.failed === true;
		const succeeded = attempted && !failed && status.succeeded === true;
		sanitized[backend] = {
			attempted,
			succeeded,
			failed,
			contributed: succeeded && status.contributed === true,
		};
	}
	return sanitized;
}

export interface TelemetryRecord {
	ts: string;
	query_hash: string;
	hit_count: number;
	elapsed_ms: number;
	backends: string[];
	per_backend_hits: Record<string, number>;
	per_backend_latency_ms: Record<string, number>;
	per_backend_status: Record<string, BackendTelemetryStatus>;
}

export interface BackendTelemetryStatus {
	attempted: boolean;
	succeeded: boolean;
	failed: boolean;
	contributed: boolean;
}

/**
 * Emit one search telemetry record to a date-stamped JSONL file.
 *
 * @param stateDir             Root state directory (vault's `.mneme/` equivalent).
 * @param queryHash            Lowercase HMAC-SHA256 hex digest returned by
 *                             `computeQueryHash`.
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
	perBackendStatus: Record<string, BackendTelemetryStatus> = {},
): void {
	try {
		if (!QUERY_HASH_RE.test(queryHash)) return;
		const today = new Date().toISOString().slice(0, 10);
		const telemetryDir = join(stateDir, "telemetry");
		mkdirSync(telemetryDir, { recursive: true });

		const record: TelemetryRecord = {
			ts: new Date().toISOString(),
			query_hash: queryHash,
			hit_count: sanitizeMetric(hitCount, { integer: true }),
			elapsed_ms: sanitizeMetric(elapsedMs),
			backends: [
				...new Set(
					backends.filter((backend): backend is TelemetryBackend =>
						TELEMETRY_BACKENDS.includes(backend as TelemetryBackend),
					),
				),
			],
			per_backend_hits: sanitizeMetricMap(perBackendHits, { integer: true }),
			per_backend_latency_ms: sanitizeMetricMap(perBackendLatencyMs, {
				integer: false,
			}),
			per_backend_status: sanitizeStatuses(perBackendStatus),
		};

		const filePath = join(telemetryDir, `retrieval-${today}.jsonl`);
		appendFileSync(filePath, `${JSON.stringify(record)}\n`, "utf8");
	} catch {
		// Non-fatal: telemetry must never interrupt the search hot path.
	}
}
