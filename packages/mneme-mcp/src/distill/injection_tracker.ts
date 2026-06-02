/**
 * InjectionTracker — per-session dedup state for mneme_prime ACL.
 *
 * Tracks which content hashes have already been injected in the current
 * session so the Adaptive Context Layer can select a cheaper format
 * (keypoints or ref) for documents the model has already seen in full.
 *
 * State is persisted as JSON in:
 *   {stateDir}/injection-tracker/{sanitizedSessionId}.json
 *
 * On any read error (ENOENT or malformed JSON) a fresh empty tracker is
 * returned — the dedup degrades gracefully to "first injection is always
 * full format". On write, atomicWriteText ensures no partial state leaks.
 *
 * Invariant: no LLM or network calls on this path.
 */

import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { atomicWriteText } from "../vault/atomic_write.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface InjectionTracker {
	/** Sanitized session identifier used as the filename stem. */
	sessionId: string;
	/** Content hashes that have been injected in this session. */
	seenHashes: Set<string>;
	/** Number of unique documents injected (new hashes). */
	hits: number;
	/** Number of repeated injection attempts skipped (existing hashes). */
	skips: number;
}

// ---------------------------------------------------------------------------
// Serialised shape stored on disk
// ---------------------------------------------------------------------------

interface TrackerJson {
	sessionId: string;
	seenHashes: string[];
	hits: number;
	skips: number;
}

// ---------------------------------------------------------------------------
// Session ID sanitization
// ---------------------------------------------------------------------------

/**
 * Keep only [A-Za-z0-9_-] characters.  Falls back to 'unknown' if the
 * result is empty.
 */
export function sanitizeSessionId(raw: string): string {
	const cleaned = raw.replace(/[^A-Za-z0-9_-]/g, "");
	return cleaned.length > 0 ? cleaned : "unknown";
}

// ---------------------------------------------------------------------------
// Fresh tracker factory
// ---------------------------------------------------------------------------

function freshTracker(sessionId: string): InjectionTracker {
	return {
		sessionId,
		seenHashes: new Set<string>(),
		hits: 0,
		skips: 0,
	};
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Load a tracker from disk.  Returns a fresh empty tracker on ENOENT or
 * any parse/validation error.
 */
export function loadTracker(
	stateDir: string,
	sessionId: string,
): InjectionTracker {
	const safeId = sanitizeSessionId(sessionId);
	const filePath = join(stateDir, "injection-tracker", `${safeId}.json`);

	let raw: string;
	try {
		raw = readFileSync(filePath, "utf8");
	} catch {
		// ENOENT or permission error — degrade gracefully.
		return freshTracker(safeId);
	}

	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return freshTracker(safeId);
	}

	// Manual validation — avoids a new dependency (zod already in package
	// but the spec says prefer manual here to keep this file dependency-free
	// from external schema libs beyond what already exists).
	if (
		typeof parsed !== "object" ||
		parsed === null ||
		typeof (parsed as TrackerJson).sessionId !== "string" ||
		!Array.isArray((parsed as TrackerJson).seenHashes) ||
		typeof (parsed as TrackerJson).hits !== "number" ||
		typeof (parsed as TrackerJson).skips !== "number"
	) {
		return freshTracker(safeId);
	}

	const data = parsed as TrackerJson;

	// Validate every element of seenHashes is a string.
	if (!data.seenHashes.every((h) => typeof h === "string")) {
		return freshTracker(safeId);
	}

	return {
		sessionId: safeId,
		seenHashes: new Set<string>(data.seenHashes),
		hits: data.hits,
		skips: data.skips,
	};
}

/**
 * Persist the tracker to disk atomically.  Creates the injection-tracker
 * subdirectory if it does not exist.
 */
export function saveTracker(stateDir: string, tracker: InjectionTracker): void {
	const dir = join(stateDir, "injection-tracker");
	mkdirSync(dir, { recursive: true });

	const filePath = join(dir, `${tracker.sessionId}.json`);

	const data: TrackerJson = {
		sessionId: tracker.sessionId,
		// Sort for deterministic output and easier diffing.
		seenHashes: Array.from(tracker.seenHashes).sort(),
		hits: tracker.hits,
		skips: tracker.skips,
	};

	atomicWriteText(filePath, `${JSON.stringify(data, null, 2)}\n`);
}

/**
 * Returns true if `hash` was already injected in this session.
 */
export function hasInjected(tracker: InjectionTracker, hash: string): boolean {
	return tracker.seenHashes.has(hash);
}

/**
 * Record that `hash` has been injected.
 *
 * Idempotent: if the hash is already in the set, increments `skips` only.
 * If the hash is new, adds it to the set and increments `hits`.
 */
export function markInjected(tracker: InjectionTracker, hash: string): void {
	if (tracker.seenHashes.has(hash)) {
		tracker.skips += 1;
	} else {
		tracker.seenHashes.add(hash);
		tracker.hits += 1;
	}
}
