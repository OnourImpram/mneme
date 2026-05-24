import { describe, expect, it } from "vitest";
import { normalizeTr, normalizeTrForFts } from "../../src/locale/tr.js";

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
