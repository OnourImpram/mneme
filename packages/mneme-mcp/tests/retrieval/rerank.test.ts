/**
 * Coverage reranking contract (4.1).
 *
 * The defect this fixes, measured on a real vault: a note literally titled
 * "Kapalı Operatör Kararları" ranked #6 for the query "kapalı operatör
 * kararları listesi", losing to short files that repeated one query term.
 * BM25 scores term DENSITY; nothing in it rewards covering more DISTINCT
 * query terms.
 *
 * Every test here pins a property that is load-bearing for that fix, and the
 * suite carries negative controls: reranking with an empty query, and a tier
 * that must NOT be reordered. Without those, the assertions would still pass
 * for an implementation that had silently become a no-op or a free-for-all.
 */

import { describe, expect, it } from "vitest";
import type { Fts5Hit } from "../../src/retrieval/fts5.js";
import {
	canonicityScore,
	coverageCount,
	rerank,
} from "../../src/retrieval/rerank.js";

function hit(path: string, title: string, rank = -10): Fts5Hit {
	return {
		path,
		title,
		rank,
		contentRaw: "",
		bodyText: "",
		mtime: 1_700_000_000,
		frontmatterType: "topic",
		sessionId: "",
		contentHash: "",
		trust: "user",
	};
}

describe("coverageCount", () => {
	it("counts distinct query terms present in title or path", () => {
		const h = hit("08-Referans/Kapali-Operator-Kararlari.md", "kapalı operatör kararları");
		expect(coverageCount(["kapalı", "operatör", "kararları"], h)).toBe(3);
	});

	it("counts a term once even if it appears in both title and path", () => {
		const h = hit("notes/roster.md", "roster");
		expect(coverageCount(["roster"], h)).toBe(1);
	});

	it("matches across Turkish dotted/dotless i", () => {
		// Query typed with ASCII i, document stored with dotted İ and dotless ı.
		const h = hit("08-Referans/Vault-Haritasi.md", "Vault Haritası");
		expect(coverageCount(["vault", "haritasi"], h)).toBe(2);
	});

	it("matches Turkish inflection by substring", () => {
		const h = hit("notes/protokol.md", "Ajan Yaratma Protokolü");
		// "protokolü" (inflected) against "protokol" (stem) in the path.
		expect(coverageCount(["protokolü"], h)).toBe(1);
	});

	it("ignores body text — coverage is aboutness, not mention", () => {
		const h = hit("notes/unrelated.md", "Something Else");
		h.bodyText = "kapalı operatör kararları ".repeat(50);
		expect(coverageCount(["kapalı", "operatör", "kararları"], h)).toBe(0);
	});

	it("negative control: an empty query covers nothing", () => {
		expect(coverageCount([], hit("a/b.md", "anything"))).toBe(0);
	});

	it("does not match short tokens by substring", () => {
		// "pl" must not match "planning" — that is why MIN_SUBSTRING_LENGTH exists.
		const h = hit("docs/planning.md", "Planning");
		expect(coverageCount(["pl"], h)).toBe(0);
	});
});

describe("canonicityScore", () => {
	it("ranks shallow paths above deep ones", () => {
		expect(canonicityScore("Ajan-Roster.md")).toBeGreaterThan(
			canonicityScore("a/b/c/d/Ajan-Roster.md"),
		);
	});

	it("penalises derived content markers", () => {
		const canonical = canonicityScore("06-Altyapi/Ajan-Yaratma-Protokolu.md");
		const draft = canonicityScore(
			"06-Altyapi/Sistem-Denetimi-2026-07-05/faz5/protokol-taslak.md",
		);
		expect(canonical).toBeGreaterThan(draft);
	});

	it("stays within 0..1", () => {
		for (const p of [
			"a.md",
			"a/b.md",
			"a/b/c/d/e/f/g.md",
			"arsiv/eski/yedek/cikti.md",
		]) {
			const s = canonicityScore(p);
			expect(s).toBeGreaterThan(0);
			expect(s).toBeLessThanOrEqual(1);
		}
	});
});

describe("rerank", () => {
	it("promotes the document covering more query terms", () => {
		// Mirrors the real failure: BM25 put the noise file first.
		const pool = [
			hit("06-Altyapi/Ajan-Kosum-2026-08-02/a11y.cikti.md", "a11y çıktı", -20),
			hit("08-Referans/Kapali-Operator-Kararlari.md", "kapalı operatör kararları", -15),
		];
		const out = rerank(pool, ["kapalı", "operatör", "kararları", "listesi"]);
		expect(out[0]?.hit.path).toBe("08-Referans/Kapali-Operator-Kararlari.md");
		expect(out[0]?.coverage).toBe(3);
	});

	it("breaks a coverage tie with canonicity", () => {
		// Both titled "ajan yaratma protokolü"; only the path differs.
		const pool = [
			hit(
				"06-Altyapi/Sistem-Denetimi-2026-07-05/faz5/protokol-taslak.md",
				"ajan yaratma protokolü (taslak)",
				-20,
			),
			hit("06-Altyapi/Ajan-Yaratma-Protokolu.md", "ajan yaratma protokolü (v1.5.0)", -19),
		];
		const out = rerank(pool, ["ajan", "yaratma", "protokolü"]);
		expect(out[0]?.hit.path).toBe("06-Altyapi/Ajan-Yaratma-Protokolu.md");
	});

	/**
	 * NEGATIVE CONTROL for the tiering guarantee. Within one coverage tier the
	 * incoming BM25 order must survive untouched. If this ever fails, tiering
	 * has degenerated into a general reshuffle and the promise that "a document
	 * is only overtaken by one covering strictly more terms" no longer holds.
	 */
	it("negative control: preserves BM25 order inside a tier", () => {
		const pool = [
			hit("a/one.md", "alpha", -30),
			hit("a/two.md", "alpha", -20),
			hit("a/three.md", "alpha", -10),
		];
		const out = rerank(pool, ["alpha"]);
		expect(out.map((r) => r.hit.path)).toEqual(["a/one.md", "a/two.md", "a/three.md"]);
		expect(new Set(out.map((r) => r.coverage))).toEqual(new Set([1]));
	});

	/**
	 * NEGATIVE CONTROL for the whole mechanism: with no query tokens every
	 * document has coverage 0, so the result must be the input order. A rerank
	 * that reordered here would be sorting on something it should not see.
	 */
	it("negative control: empty query leaves order untouched", () => {
		const pool = [
			hit("z/last.md", "z", -5),
			hit("a/first.md", "a", -4),
		];
		const out = rerank(pool, []);
		expect(out.map((r) => r.hit.path)).toEqual(["z/last.md", "a/first.md"]);
	});

	it("never drops or duplicates candidates", () => {
		const pool = [
			hit("a/x.md", "vault haritası"),
			hit("b/y.md", "başka"),
			hit("c/z.md", "vault"),
		];
		const out = rerank(pool, ["vault", "haritası"]);
		expect(out).toHaveLength(3);
		expect(new Set(out.map((r) => r.hit.path)).size).toBe(3);
	});
});
