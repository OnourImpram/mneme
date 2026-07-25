import { describe, expect, it } from "vitest";
import {
	normalizeTr,
	normalizeTrAsciiFold,
	normalizeTrAsciiFoldForFts,
	normalizeTrForFts,
} from "../../src/locale/tr.js";

describe("normalizeTr", () => {
	it("returns empty string for non-string input", () => {
		expect(normalizeTr(undefined as unknown as string)).toBe("");
		expect(normalizeTr(null as unknown as string)).toBe("");
		expect(normalizeTr(123 as unknown as string)).toBe("");
	});

	it("returns empty string unchanged", () => {
		expect(normalizeTr("")).toBe("");
	});

	it("preserves dotted i lowercase", () => {
		expect(normalizeTr("istanbul")).toBe("istanbul");
	});

	it("preserves dotless ı lowercase", () => {
		expect(normalizeTr("ışık")).toBe("ışık");
	});

	it("dotted capital İ folds to dotted i", () => {
		expect(normalizeTr("İstanbul")).toBe("istanbul");
	});

	it("dotless capital I folds to dotless ı", () => {
		expect(normalizeTr("Istanbul")).toBe("ıstanbul");
	});

	it("KIYASLAMA folds to kıyaslama not kiyaslama (the rule-order bug)", () => {
		// Naive lower would produce 'kiyaslama' which is semantically wrong.
		expect(normalizeTr("KIYASLAMA")).toBe("kıyaslama");
		expect(normalizeTr("KIYASLAMA")).not.toBe("kiyaslama");
	});

	it("mixed-case word with both I forms", () => {
		// Dotted İ -> i, then bare I -> ı.
		expect(normalizeTr("İŞIK")).toBe("işık");
	});

	it("ascii words pass through unchanged in shape", () => {
		expect(normalizeTr("Hello World")).toBe("hello world");
	});

	it("idempotent on already-normalized input", () => {
		const once = normalizeTr("İSTANBUL");
		expect(normalizeTr(once)).toBe(once);
	});
});

describe("normalizeTrForFts", () => {
	it("collapses internal whitespace runs", () => {
		expect(normalizeTrForFts("İki  arada    bir derede")).toBe(
			"iki arada bir derede",
		);
	});

	it("trims leading and trailing whitespace", () => {
		expect(normalizeTrForFts("   İstanbul   ")).toBe("istanbul");
	});

	it("preserves single spaces between tokens", () => {
		expect(normalizeTrForFts("foo bar baz")).toBe("foo bar baz");
	});

	it("returns empty for whitespace-only input", () => {
		expect(normalizeTrForFts("   ")).toBe("");
	});
});

describe("normalize_tr semantic distinction (dotted vs dotless)", () => {
	// The whole point of the dual-rule normalizer is that these two
	// strings remain semantically distinct after folding.
	it("İstanbul and Istanbul fold to different forms", () => {
		expect(normalizeTr("İstanbul")).not.toBe(normalizeTr("Istanbul"));
	});
});

// ---------------------------------------------------------------------------
// Finding 2 — normalizeTr must be length-preserving in JS char units.
//
// buildCenteredSnippet in search.ts finds matchPos in the normalized body
// and then uses it to slice the ORIGINAL bodyText. This is only correct if
// normalize(s).length === s.length for all vault content. The tests below
// verify the invariant holds for the Turkish characters that appear in
// practice (İ U+0130 and I U+0049 both map to single BMP code units).
// ---------------------------------------------------------------------------

describe("normalizeTr — length-preservation invariant (Finding 2)", () => {
	it("preserves .length for a string containing dotted İ (U+0130)", () => {
		const s = "İzmir ve İstanbul";
		expect(normalizeTr(s).length).toBe(s.length);
	});

	it("preserves .length for a string containing both İ and I", () => {
		const s = "İki IŞIKlı İstasyon";
		expect(normalizeTr(s).length).toBe(s.length);
	});

	it("preserves .length for a pure-ASCII string", () => {
		const s = "Hello World 123";
		expect(normalizeTr(s).length).toBe(s.length);
	});

	it("preserves .length for an empty string", () => {
		expect(normalizeTr("").length).toBe(0);
	});
});

describe("normalizeTrAsciiFold", () => {
	it("returns empty string for non-string input", () => {
		expect(normalizeTrAsciiFold(undefined as unknown as string)).toBe("");
		expect(normalizeTrAsciiFold(null as unknown as string)).toBe("");
	});

	it("collapses all four i-forms to plain ascii i", () => {
		expect(normalizeTrAsciiFold("İ")).toBe("i");
		expect(normalizeTrAsciiFold("I")).toBe("i");
		expect(normalizeTrAsciiFold("ı")).toBe("i");
		expect(normalizeTrAsciiFold("i")).toBe("i");
	});

	it("every casing of Izmir shares one ascii-fold key", () => {
		for (const s of ["İzmir", "izmir", "Izmir", "IZMIR"]) {
			expect(normalizeTrAsciiFold(s)).toBe("izmir");
		}
	});

	it("collapses where CLDR keeps the dotless distinction", () => {
		// CLDR keeps Istanbul dotless; the ascii fold recovers it.
		expect(normalizeTr("Istanbul")).toBe("ıstanbul");
		expect(normalizeTrAsciiFold("Istanbul")).toBe("istanbul");
	});

	it("leaves other diacritics to the tokenizer", () => {
		expect(normalizeTrAsciiFold("İŞIK")).toBe("işik");
	});
});

describe("normalizeTrAsciiFoldForFts", () => {
	it("collapses internal whitespace runs", () => {
		expect(normalizeTrAsciiFoldForFts("Izmir   Istanbul")).toBe(
			"izmir istanbul",
		);
	});

	it("trims leading and trailing whitespace", () => {
		expect(normalizeTrAsciiFoldForFts("   IZMIR   ")).toBe("izmir");
	});
});

// ---------------------------------------------------------------------------
// Shared golden-vector fixture — parity with Python mneme_core normalizer
// ---------------------------------------------------------------------------
//
// The fixture lives in packages/mneme-core/tests/fixtures/tr_locale_vectors.json
// and is the canonical source of truth for both implementations.  If this
// describe block fails while the Python suite passes (or vice-versa) the two
// normalizers have drifted.

import { readFileSync } from "node:fs";
import { join } from "node:path";

interface TrVector {
	id: string;
	input: string;
	normalize_tr: string;
	normalize_tr_for_fts: string;
	normalize_tr_ascii_fold: string;
	normalize_tr_ascii_fold_for_fts: string;
	note: string;
}

const VECTOR_PATH = join(
	__dirname,
	"../../../mneme-core/tests/fixtures/tr_locale_vectors.json",
);

const vectors: TrVector[] = JSON.parse(
	readFileSync(VECTOR_PATH, "utf8"),
) as TrVector[];

describe("normalizeTr — golden vectors (cross-language parity)", () => {
	for (const v of vectors) {
		it(`${v.id}: normalizeTr`, () => {
			expect(normalizeTr(v.input)).toBe(v.normalize_tr);
		});
	}
});

describe("normalizeTrForFts — golden vectors (cross-language parity)", () => {
	for (const v of vectors) {
		it(`${v.id}: normalizeTrForFts`, () => {
			expect(normalizeTrForFts(v.input)).toBe(v.normalize_tr_for_fts);
		});
	}
});

describe("normalizeTrAsciiFold — golden vectors (cross-language parity)", () => {
	for (const v of vectors) {
		it(`${v.id}: normalizeTrAsciiFold`, () => {
			expect(normalizeTrAsciiFold(v.input)).toBe(v.normalize_tr_ascii_fold);
		});
	}
});

describe("normalizeTrAsciiFoldForFts — golden vectors (cross-language parity)", () => {
	for (const v of vectors) {
		it(`${v.id}: normalizeTrAsciiFoldForFts`, () => {
			expect(normalizeTrAsciiFoldForFts(v.input)).toBe(
				v.normalize_tr_ascii_fold_for_fts,
			);
		});
	}
});
