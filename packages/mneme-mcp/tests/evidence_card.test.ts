/**
 * Unit tests for evidence_card.ts (component 6 — EvidenceCard interface).
 *
 * Parity assertion: CANONICAL_MEMORY_TYPES in search.ts must equal the
 * canonical_memory_types.json fixture. This test is in the TS lane because
 * the constant being asserted is a TS export and the test runner is vitest.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { hitToEvidenceCard } from "../src/evidence_card.js";
import { CANONICAL_MEMORY_TYPES } from "../src/tools/search.js";
import type { Fts5Hit } from "../src/retrieval/fts5.js";

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

function mockHit(overrides: Partial<Fts5Hit> = {}): Fts5Hit {
	return {
		path: "daily/2026-06-01.md",
		title: "Test Session",
		rank: -0.87,
		contentRaw: "Raw content including <private>SECRET</private>.",
		bodyText: "Body text without frontmatter.",
		mtime: 1_748_736_000,
		frontmatterType: "session",
		sessionId: "s-2026-06-01",
		contentHash: "a".repeat(64),
		trust: "user",
		...overrides,
	};
}

// ---------------------------------------------------------------------------
// hitToEvidenceCard — field mapping
// ---------------------------------------------------------------------------

describe("hitToEvidenceCard — field mapping", () => {
	it("returns all required EvidenceCard fields", () => {
		const hit = mockHit();
		const card = hitToEvidenceCard(hit, "test query", "safe snippet");

		expect(card.path).toBe(hit.path);
		expect(card.title).toBe(hit.title);
		expect(card.score).toBe(hit.rank);
		expect(card.snippet).toBe("safe snippet");
		expect(card.type).toBe(hit.frontmatterType);
		expect(card.mtime).toBe(hit.mtime);
		expect(card.contentHash).toBe(hit.contentHash);
		expect(card.trust).toBe(hit.trust);
		expect(card.query).toBe("test query");
	});

	it("confidenceLabel is always EXTRACTED for FTS5 hits", () => {
		const card = hitToEvidenceCard(mockHit(), "q", "snip");
		expect(card.confidenceLabel).toBe("EXTRACTED");
	});

	it("score equals hit.rank (BM25 rank passes through unchanged)", () => {
		const hit = mockHit({ rank: -1.234 });
		const card = hitToEvidenceCard(hit, "q", "snip");
		expect(card.score).toBe(-1.234);
	});

	it("contentHash passes through from hit", () => {
		const hash = "b".repeat(64);
		const hit = mockHit({ contentHash: hash });
		const card = hitToEvidenceCard(hit, "q", "snip");
		expect(card.contentHash).toBe(hash);
	});

	it("trust passes through from hit", () => {
		const hit = mockHit({ trust: "user" });
		const card = hitToEvidenceCard(hit, "q", "snip");
		expect(card.trust).toBe("user");
	});

	it("snippet is exactly the string passed in (caller owns redaction)", () => {
		// Caller is responsible for redaction before calling hitToEvidenceCard.
		// The function must not transform the snippet string.
		const snippet = "already redacted [REDACTED] safe text";
		const card = hitToEvidenceCard(mockHit(), "q", snippet);
		expect(card.snippet).toBe(snippet);
	});

	it("query field matches the query argument", () => {
		const card = hitToEvidenceCard(mockHit(), "vault memory", "snip");
		expect(card.query).toBe("vault memory");
	});

	it("empty contentHash (legacy index fallback) passes through as empty string", () => {
		const hit = mockHit({ contentHash: "" });
		const card = hitToEvidenceCard(hit, "q", "snip");
		expect(card.contentHash).toBe("");
	});

	it("backend defaults to 'fts5' when not supplied", () => {
		const card = hitToEvidenceCard(mockHit(), "q", "snip");
		expect(card.backend).toBe("fts5");
	});

	it("backend is overridable for future retrieval legs", () => {
		const card = hitToEvidenceCard(mockHit(), "q", "snip", "dense");
		expect(card.backend).toBe("dense");
	});
});

// ---------------------------------------------------------------------------
// Parity: CANONICAL_MEMORY_TYPES vs canonical_memory_types.json fixture
// ---------------------------------------------------------------------------

interface CanonicalTypeEntry {
	type: string;
	description: string;
}

const FIXTURE_PATH = join(
	__dirname,
	"../../mneme-core/tests/fixtures/canonical_memory_types.json",
);

describe("CANONICAL_MEMORY_TYPES — parity with shared fixture (TS lane assertion)", () => {
	it("contains exactly the same types as canonical_memory_types.json (no extras, no missing)", () => {
		const fixture = JSON.parse(
			readFileSync(FIXTURE_PATH, "utf8"),
		) as CanonicalTypeEntry[];
		const fromFixture = fixture.map((e) => e.type).sort();
		const fromTs = [...CANONICAL_MEMORY_TYPES].sort();
		expect(fromTs).toEqual(fromFixture);
	});
});
