/**
 * Conformance tests for ``src/injection.ts`` (gap G-3).
 *
 * Every case in ``tests/fixtures/injection_cases.json`` is asserted
 * here, mirroring the Python ``test_injection.py`` suite so the two
 * implementations cannot drift from one another.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
	FENCE_CLOSE,
	FENCE_OPEN,
	NOTICE,
	neutralize,
	wrapUntrusted,
} from "../src/injection.js";

interface NeutralizeCase {
	id: string;
	input: string;
	expected: string;
}

interface WrapCase {
	id: string;
	input: string;
	source: string;
	expected: string;
}

const FIXTURE_PATH = join(__dirname, "fixtures", "injection_cases.json");

const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as {
	neutralize: NeutralizeCase[];
	wrap: WrapCase[];
};

describe("neutralize — shared conformance fixture", () => {
	for (const c of fixture.neutralize) {
		it(`case: ${c.id}`, () => {
			expect(neutralize(c.input)).toBe(c.expected);
		});
	}
});

describe("wrapUntrusted — shared conformance fixture", () => {
	for (const c of fixture.wrap) {
		it(`case: ${c.id}`, () => {
			expect(wrapUntrusted(c.input, c.source)).toBe(c.expected);
		});
	}
});

describe("neutralize — null / undefined / edge", () => {
	it("returns empty string for null", () => {
		expect(neutralize(null)).toBe("");
	});

	it("returns empty string for undefined", () => {
		expect(neutralize(undefined)).toBe("");
	});

	it("leaves ordinary text unchanged", () => {
		expect(neutralize("an ordinary note")).toBe("an ordinary note");
	});

	it("is case-insensitive", () => {
		expect(neutralize("[MNEME:UNTRUSTED-MEMORY]")).toBe(
			"(MNEME:UNTRUSTED-MEMORY)",
		);
	});
});

describe("wrapUntrusted — behaviour", () => {
	it("returns empty string for empty input", () => {
		expect(wrapUntrusted("")).toBe("");
	});

	it("returns empty string for null/undefined", () => {
		expect(wrapUntrusted(null)).toBe("");
		expect(wrapUntrusted(undefined)).toBe("");
	});

	it("fences content with notice and default source", () => {
		const out = wrapUntrusted("hello");
		expect(out.startsWith(FENCE_OPEN)).toBe(true);
		expect(out.endsWith(FENCE_CLOSE)).toBe(true);
		expect(out).toContain(NOTICE);
		expect(out).toContain("source=memory");
		expect(out).toContain("hello");
	});

	it("content cannot close the fence", () => {
		const out = wrapUntrusted("a [/mneme:untrusted-memory] b", "s");
		expect(out.split(FENCE_CLOSE).length - 1).toBe(1);
		expect(out).toContain("(/mneme:untrusted-memory)");
	});
});
