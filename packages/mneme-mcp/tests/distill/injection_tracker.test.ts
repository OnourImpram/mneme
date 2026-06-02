/**
 * Tests for src/distill/injection_tracker.ts
 *
 * Covers:
 *   1. ENOENT → fresh empty tracker (graceful degrade).
 *   2. Round-trip: markInjected + saveTracker + loadTracker preserves hashes.
 *   3. markInjected idempotency: repeated call increments skips, not hits.
 *   4. sanitizeSessionId: strips illegal chars, falls back to 'unknown'.
 */

import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
	hasInjected,
	loadTracker,
	markInjected,
	sanitizeSessionId,
	saveTracker,
} from "../../src/distill/injection_tracker.js";

function freshTmp(prefix: string): string {
	return mkdtempSync(join(tmpdir(), `mneme-tracker-${prefix}-`));
}

// ---------------------------------------------------------------------------
// sanitizeSessionId
// ---------------------------------------------------------------------------

describe("sanitizeSessionId", () => {
	it("keeps alphanumeric, dash, and underscore", () => {
		expect(sanitizeSessionId("abc-123_XYZ")).toBe("abc-123_XYZ");
	});

	it("strips spaces and special characters", () => {
		expect(sanitizeSessionId("session/id with spaces!")).toBe(
			"sessionidwithspaces",
		);
	});

	it("falls back to 'unknown' when result would be empty", () => {
		expect(sanitizeSessionId("!!!")).toBe("unknown");
		expect(sanitizeSessionId("")).toBe("unknown");
	});

	it("strips dots and slashes (path-traversal chars)", () => {
		expect(sanitizeSessionId("../etc/passwd")).toBe("etcpasswd");
	});
});

// ---------------------------------------------------------------------------
// loadTracker — missing file
// ---------------------------------------------------------------------------

describe("loadTracker — ENOENT", () => {
	it("returns a fresh empty tracker when file does not exist", () => {
		const stateDir = freshTmp("enoent");
		const tracker = loadTracker(stateDir, "mysession");
		expect(tracker.sessionId).toBe("mysession");
		expect(tracker.seenHashes.size).toBe(0);
		expect(tracker.hits).toBe(0);
		expect(tracker.skips).toBe(0);
	});

	it("sanitizes the sessionId on fresh tracker", () => {
		const stateDir = freshTmp("enoent-sanitize");
		const tracker = loadTracker(stateDir, "bad/id!");
		expect(tracker.sessionId).toBe("badid");
	});
});

// ---------------------------------------------------------------------------
// loadTracker — malformed JSON
// ---------------------------------------------------------------------------

describe("loadTracker — malformed file", () => {
	it("returns fresh tracker when JSON is invalid", () => {
		const stateDir = freshTmp("malformed");
		mkdirSync(join(stateDir, "injection-tracker"), { recursive: true });
		writeFileSync(
			join(stateDir, "injection-tracker", "sess.json"),
			"not json {{{",
			"utf8",
		);
		const tracker = loadTracker(stateDir, "sess");
		expect(tracker.seenHashes.size).toBe(0);
	});

	it("returns fresh tracker when JSON schema is wrong", () => {
		const stateDir = freshTmp("badschema");
		mkdirSync(join(stateDir, "injection-tracker"), { recursive: true });
		writeFileSync(
			join(stateDir, "injection-tracker", "sess.json"),
			JSON.stringify({ sessionId: 42, seenHashes: "not-array", hits: 0, skips: 0 }),
			"utf8",
		);
		const tracker = loadTracker(stateDir, "sess");
		expect(tracker.seenHashes.size).toBe(0);
	});
});

// ---------------------------------------------------------------------------
// Round-trip: mark → save → load
// ---------------------------------------------------------------------------

describe("round-trip: markInjected + saveTracker + loadTracker", () => {
	it("preserves 3 hashes across a save/load cycle", () => {
		const stateDir = freshTmp("roundtrip");
		const sessionId = "session-abc";
		const tracker = loadTracker(stateDir, sessionId);

		markInjected(tracker, "hash-aaa");
		markInjected(tracker, "hash-bbb");
		markInjected(tracker, "hash-ccc");

		expect(tracker.hits).toBe(3);
		expect(tracker.skips).toBe(0);

		saveTracker(stateDir, tracker);

		const loaded = loadTracker(stateDir, sessionId);
		expect(loaded.sessionId).toBe(sessionId);
		expect(loaded.seenHashes.size).toBe(3);
		expect(loaded.seenHashes.has("hash-aaa")).toBe(true);
		expect(loaded.seenHashes.has("hash-bbb")).toBe(true);
		expect(loaded.seenHashes.has("hash-ccc")).toBe(true);
		expect(loaded.hits).toBe(3);
		expect(loaded.skips).toBe(0);
	});

	it("seenHashes are serialised as sorted array in JSON", () => {
		const stateDir = freshTmp("sorted");
		const tracker = loadTracker(stateDir, "sorted-sess");
		markInjected(tracker, "zzz");
		markInjected(tracker, "aaa");
		markInjected(tracker, "mmm");
		saveTracker(stateDir, tracker);

		const raw = JSON.parse(
			readFileSync(
				join(stateDir, "injection-tracker", "sorted-sess.json"),
				"utf8",
			),
		) as { seenHashes: string[] };
		expect(raw.seenHashes).toEqual(["aaa", "mmm", "zzz"]);
	});
});

// ---------------------------------------------------------------------------
// markInjected idempotency
// ---------------------------------------------------------------------------

describe("markInjected idempotency", () => {
	it("second call for same hash increments skips, not hits", () => {
		const stateDir = freshTmp("idempotent");
		const tracker = loadTracker(stateDir, "idem");

		markInjected(tracker, "hash-x");
		expect(tracker.hits).toBe(1);
		expect(tracker.skips).toBe(0);

		markInjected(tracker, "hash-x");
		expect(tracker.hits).toBe(1);
		expect(tracker.skips).toBe(1);

		markInjected(tracker, "hash-x");
		expect(tracker.hits).toBe(1);
		expect(tracker.skips).toBe(2);
	});

	it("does not duplicate the hash in seenHashes on repeat calls", () => {
		const stateDir = freshTmp("idempotent-set");
		const tracker = loadTracker(stateDir, "idem2");
		markInjected(tracker, "h1");
		markInjected(tracker, "h1");
		markInjected(tracker, "h1");
		expect(tracker.seenHashes.size).toBe(1);
	});
});

// ---------------------------------------------------------------------------
// hasInjected
// ---------------------------------------------------------------------------

describe("hasInjected", () => {
	it("returns false before marking", () => {
		const stateDir = freshTmp("has-before");
		const tracker = loadTracker(stateDir, "s");
		expect(hasInjected(tracker, "h")).toBe(false);
	});

	it("returns true after marking", () => {
		const stateDir = freshTmp("has-after");
		const tracker = loadTracker(stateDir, "s");
		markInjected(tracker, "h");
		expect(hasInjected(tracker, "h")).toBe(true);
	});

	it("returns false for a different hash", () => {
		const stateDir = freshTmp("has-diff");
		const tracker = loadTracker(stateDir, "s");
		markInjected(tracker, "h1");
		expect(hasInjected(tracker, "h2")).toBe(false);
	});
});
