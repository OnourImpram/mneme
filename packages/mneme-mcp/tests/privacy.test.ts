/**
 * Conformance tests for ``src/privacy.ts``.
 *
 * Every case in ``tests/fixtures/redaction_cases.json`` is asserted
 * here, mirroring the Python ``test_privacy_redaction.py`` suite so
 * the two implementations cannot drift from one another.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { redact } from "../src/privacy.js";

interface RedactionCase {
	id: string;
	input: string;
	expected: string;
}

const FIXTURE_PATH = join(
	__dirname,
	"fixtures",
	"redaction_cases.json",
);

const cases: RedactionCase[] = JSON.parse(
	readFileSync(FIXTURE_PATH, "utf8"),
) as RedactionCase[];

// ---------------------------------------------------------------------------
// Shared conformance fixture
// ---------------------------------------------------------------------------

describe("redact — shared conformance fixture", () => {
	for (const c of cases) {
		it(`case: ${c.id}`, () => {
			const result = redact(c.input);
			expect(result.text).toBe(c.expected);
		});
	}
});

// ---------------------------------------------------------------------------
// Additional TypeScript-specific edge cases
// ---------------------------------------------------------------------------

describe("redact — null / undefined / empty", () => {
	it("returns empty string with count 0 for null", () => {
		const r = redact(null);
		expect(r.text).toBe("");
		expect(r.count).toBe(0);
	});

	it("returns empty string with count 0 for undefined", () => {
		const r = redact(undefined);
		expect(r.text).toBe("");
		expect(r.count).toBe(0);
	});

	it("returns empty string with count 0 for empty string", () => {
		const r = redact("");
		expect(r.text).toBe("");
		expect(r.count).toBe(0);
	});

	it("returns input unchanged with count 0 when no tag present", () => {
		const r = redact("hello world");
		expect(r.text).toBe("hello world");
		expect(r.count).toBe(0);
	});
});

describe("redact — count tracking", () => {
	it("counts a single substitution", () => {
		const r = redact("before <private>secret</private> after");
		expect(r.count).toBe(1);
	});

	it("counts multiple substitutions", () => {
		const r = redact("<private>a</private> mid <private>b</private>");
		expect(r.count).toBe(2);
	});

	it("counts unbalanced open as one substitution", () => {
		const r = redact("prefix <private>no close");
		expect(r.count).toBe(1);
	});
});

describe("redact — replacement token", () => {
	it("uses [REDACTED] as the replacement token", () => {
		const r = redact("<private>x</private>");
		expect(r.text).toBe("[REDACTED]");
	});
});
