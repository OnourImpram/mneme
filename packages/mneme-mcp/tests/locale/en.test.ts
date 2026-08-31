/**
 * English normalizer contract — the TypeScript half of a cross-language gate.
 *
 * This file and `packages/mneme-core/tests/unit/test_en_normalize.py` read
 * the SAME fixture, `mneme-core/tests/fixtures/en_locale_vectors.json`.
 *
 * That sharing is the point. The Python indexer writes tokens into SQLite and
 * this TypeScript client queries them, so if the two normalizers drift the
 * index becomes unsearchable in a way neither side's own tests would reveal —
 * each would still pass against its own expectations. Binding both to one
 * file turns silent drift into a failing build.
 *
 * Mirrors the existing tr.test.ts arrangement.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { normalizeEn, normalizeEnForFts } from "../../src/locale/en.js";
import { normalizeTr } from "../../src/locale/tr.js";

interface EnVector {
	id: string;
	input: string;
	normalize_en: string;
	normalize_en_for_fts: string;
	note: string;
}

const VECTORS_PATH = join(
	__dirname,
	"..",
	"..",
	"..",
	"mneme-core",
	"tests",
	"fixtures",
	"en_locale_vectors.json",
);

const vectors: EnVector[] = JSON.parse(readFileSync(VECTORS_PATH, "utf8"));

describe("normalizeEn — shared vectors", () => {
	it("loads the fixture the Python suite also reads", () => {
		// A silently empty fixture would make every case below vacuous.
		expect(vectors.length).toBeGreaterThan(5);
	});

	for (const v of vectors) {
		it(`${v.id}: normalizeEn`, () => {
			expect(normalizeEn(v.input), v.note).toBe(v.normalize_en);
		});
		it(`${v.id}: normalizeEnForFts`, () => {
			expect(normalizeEnForFts(v.input), v.note).toBe(v.normalize_en_for_fts);
		});
	}
});

describe("normalizeEn — length invariant", () => {
	for (const v of vectors) {
		it(`${v.id}: one char in, one char out`, () => {
			expect(normalizeEn(v.input).length).toBe(v.input.length);
		});
	}

	it("negative control: bare toLowerCase violates it", () => {
		// The obvious implementation fails on U+0130. If this stops failing,
		// the assertions above have become vacuous.
		expect("İ".toLowerCase().length).toBe(2);
	});
});

describe("profile separation", () => {
	it("disagrees with Turkish exactly where the languages disagree", () => {
		expect(normalizeEn("API")).toBe("api");
		expect(normalizeTr("API")).toBe("apı");
		expect(normalizeEn("Istanbul")).toBe("istanbul");
		expect(normalizeTr("Istanbul")).toBe("ıstanbul");
	});

	it("never introduces a dotless i", () => {
		for (const text of ["INDEX", "I/O", "CLI", "API"]) {
			expect(normalizeEn(text)).not.toContain("ı");
		}
	});
});
