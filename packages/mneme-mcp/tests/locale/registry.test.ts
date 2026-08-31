/**
 * Locale profile registry contract (4.0).
 *
 * Two things are load-bearing here and neither is obvious from reading the
 * profiles in isolation:
 *
 *  1. Profile ids are a WIRE CONTRACT. They are persisted into the SQLite
 *     index by the Python indexer and read back by this TS client, possibly
 *     from a different release. Renaming one silently breaks every existing
 *     index, so the ids are pinned here.
 *
 *  2. Every normalizer must be LENGTH-PRESERVING in JS char units.
 *     `buildCenteredSnippet` finds a match offset in the normalized body and
 *     slices the ORIGINAL body at that offset. A normalizer that changes
 *     length shifts every subsequent snippet. Bare `.toLowerCase()` violates
 *     this for U+0130 ("İ" -> "i̇", 1 unit becomes 2), which is why
 *     normalizeEn folds U+0130 explicitly first.
 *
 * The length test iterates the whole registry rather than naming profiles, so
 * a locale added later inherits the guarantee instead of quietly breaking it.
 */

import { describe, expect, it } from "vitest";
import {
	EN_PROFILE,
	LOCALE_PROFILES,
	profileById,
	TR_PROFILE,
} from "../../src/locale/index.js";

/** Inputs chosen to stress case folding, not to be representative prose. */
const STRESS_INPUTS = [
	"İstanbul",
	"ISTANBUL",
	"Istanbul",
	"İ",
	"I",
	"API",
	"I/O",
	"KIYASLAMA",
	"Straße",
	"ΣΊΣΥΦΟΣ",
	"ǅungla",
	"kapalı operatör kararları",
	"",
];

describe("locale profile registry", () => {
	it("pins the persisted profile ids", () => {
		expect(TR_PROFILE.id).toBe("tr-cldr");
		expect(TR_PROFILE.asciiProfileId).toBe("tr-ascii-fold");
		expect(EN_PROFILE.id).toBe("en-unicode");
		expect(profileById("tr-cldr")).toBe(TR_PROFILE);
		expect(profileById("en-unicode")).toBe(EN_PROFILE);
		// identity and tr-ascii-fold are intentionally unresolvable: the first
		// is the absence of normalization, the second a secondary recall key.
		expect(profileById("identity")).toBeUndefined();
		expect(profileById("tr-ascii-fold")).toBeUndefined();
	});

	it("returns undefined for an unknown id so callers can fail closed", () => {
		expect(profileById("de-din")).toBeUndefined();
		expect(profileById("")).toBeUndefined();
	});

	it("every registered normalizer preserves length", () => {
		for (const [id, profile] of LOCALE_PROFILES) {
			for (const input of STRESS_INPUTS) {
				expect(
					profile.normalize(input).length,
					`profile '${id}' changed length for ${JSON.stringify(input)}`,
				).toBe(input.length);
			}
		}
	});

	it("every registered ascii fold preserves length too", () => {
		for (const [id, profile] of LOCALE_PROFILES) {
			if (!profile.asciiFold) continue;
			for (const input of STRESS_INPUTS) {
				expect(
					profile.asciiFold(input).length,
					`profile '${id}' ascii fold changed length for ${JSON.stringify(input)}`,
				).toBe(input.length);
			}
		}
	});

	/**
	 * NEGATIVE CONTROL for the length rule: bare toLowerCase, the obvious
	 * implementation someone would reach for, actually fails it. If this ever
	 * stops failing, the length tests above have become vacuous and the guard
	 * in normalizeEn can no longer be justified by this suite.
	 */
	it("negative control: bare toLowerCase violates the length rule", () => {
		expect("İ".toLowerCase().length).toBe(2);
		expect("İ".toLowerCase()).not.toBe("i");
	});

	it("separates the two languages where the fold differs", () => {
		// Turkish: I and İ are distinct letters, folded apart.
		expect(TR_PROFILE.normalize("API")).toBe("apı");
		expect(TR_PROFILE.normalize("İstanbul")).toBe("istanbul");
		expect(TR_PROFILE.normalize("Istanbul")).toBe("ıstanbul");
		// English: standard Unicode lowercase, no dotless i.
		expect(EN_PROFILE.normalize("API")).toBe("api");
		expect(EN_PROFILE.normalize("Istanbul")).toBe("istanbul");
		// A Turkish proper noun inside English prose folds without expanding.
		expect(EN_PROFILE.normalize("İstanbul")).toBe("istanbul");
	});

	it("only Turkish declares an ascii-fold leg", () => {
		expect(TR_PROFILE.asciiFold).toBeDefined();
		expect(TR_PROFILE.asciiFold?.("Izmir")).toBe("izmir");
		expect(TR_PROFILE.asciiFold?.("İzmir")).toBe("izmir");
		// English has no dotted/dotless ambiguity, so it skips the extra scan.
		expect(EN_PROFILE.asciiFold).toBeUndefined();
	});

	it("preserves pre-4.0 Turkish behaviour exactly", () => {
		// These are the vectors the pre-4.0 hard-coded path produced. Any
		// change here is a retrieval regression for every existing index.
		expect(TR_PROFILE.normalize("KIYASLAMA")).toBe("kıyaslama");
		expect(TR_PROFILE.normalize("kapalı operatör kararları")).toBe(
			"kapalı operatör kararları",
		);
		expect(TR_PROFILE.normalizeForFts("  çok   boşluk  ")).toBe("çok boşluk");
	});
});
