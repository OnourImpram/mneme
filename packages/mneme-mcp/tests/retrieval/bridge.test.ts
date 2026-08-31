/**
 * Cross-language bridge contract (4.1).
 *
 * The bridge exists because coverage scores a document by how many query terms
 * appear in its title or path, and an English query shares no token with a
 * Turkish filename: "agent creation protocol" against `Ajan-Yaratma-Protokolu`
 * scores zero. Measured on a real bilingual vault, five of six remaining
 * failures had exactly that shape.
 *
 * The dangerous failure mode is not a missing pair — it is a bridge that
 * INFLATES coverage, letting a bilingual document outrank a monolingual one on
 * the same evidence. Several tests below pin that specifically, and the suite
 * carries negative controls: an unknown token must bridge to nothing, and a
 * query builder given no expander must produce exactly what it produced before
 * the bridge existed.
 */

import { describe, expect, it } from "vitest";
import { bridgeSize, bridgeTerms, foldForCompare } from "../../src/retrieval/bridge.js";
import { buildFts5Query } from "../../src/retrieval/fts5.js";
import type { Fts5Hit } from "../../src/retrieval/fts5.js";
import { canonicityScore, coverageCount } from "../../src/retrieval/rerank.js";

function hit(path: string, title: string): Fts5Hit {
	return {
		path,
		title,
		rank: -10,
		contentRaw: "",
		bodyText: "",
		mtime: 1_700_000_000,
		frontmatterType: "topic",
		sessionId: "",
		contentHash: "",
		trust: "user",
	};
}

describe("bridgeTerms", () => {
	it("bridges English to Turkish", () => {
		expect(bridgeTerms("agent")).toContain("ajan");
		expect(bridgeTerms("audit")).toContain("denetim");
	});

	it("bridges Turkish back to English — the table is written once, both ways", () => {
		expect(bridgeTerms("ajan")).toContain("agent");
		expect(bridgeTerms("denetim")).toContain("audit");
	});

	it("folds diacritics so an ASCII filename still matches", () => {
		// The gloss is written "hafıza"; filenames spell it "hafiza".
		// Dense matching fails exactly here (memory<->hafiza measured 0.257),
		// which is why this is a table and not an embedding.
		expect(bridgeTerms("memory")).toContain(foldForCompare("hafıza"));
		expect(bridgeTerms("memory")).toContain("hafiza");
	});

	it("is case-insensitive on input", () => {
		expect(bridgeTerms("AUDIT")).toContain("denetim");
	});

	it("negative control: an unknown token bridges to nothing", () => {
		expect(bridgeTerms("xyzzy").size).toBe(0);
		expect(bridgeTerms("").size).toBe(0);
	});

	it("negative control: no token bridges to itself", () => {
		for (const token of ["agent", "ajan", "audit", "test"]) {
			expect(bridgeTerms(token).has(foldForCompare(token))).toBe(false);
		}
	});

	it("carries a non-trivial table", () => {
		expect(bridgeSize()).toBeGreaterThan(100);
	});
});

describe("coverageCount with the bridge", () => {
	it("counts a cross-language match", () => {
		// The measured failure: this scored 0 before the bridge.
		const h = hit("06-Altyapi/Ajan-Yaratma-Protokolu.md", "ajan yaratma protokolü");
		expect(coverageCount(["agent", "creation", "protocol"], h)).toBe(3);
	});

	it("counts a term ONCE when both it and its equivalent are present", () => {
		// A bilingual document must not score higher on the same evidence.
		const h = hit("notes/agent-ajan.md", "agent ajan");
		expect(coverageCount(["agent"], h)).toBe(1);
	});

	it("never exceeds the number of query terms", () => {
		const h = hit(
			"06-Altyapi/Ajan-Yaratma-Protokolu-Denetim-Kayit.md",
			"ajan yaratma protokolü denetim kayıt",
		);
		const tokens = ["agent", "creation", "protocol", "audit", "record"];
		expect(coverageCount(tokens, h)).toBeLessThanOrEqual(tokens.length);
	});

	it("negative control: an unbridged foreign term still scores zero", () => {
		// "stale" is deliberately absent from the table; the honest result is
		// a miss, not a silent fuzzy match.
		const h = hit("08-Referans/Feedback/2026-08-04-bayat-sunucu.md", "bayat sunucu");
		expect(coverageCount(["stale"], h)).toBe(0);
	});
});

describe("canonicityScore is query-aware", () => {
	const auditFile = "06-Altyapi/Hafiza-Sistemi/Onarim-Denetimi-2026-07-30.md";

	it("penalises a derived marker when the query does not ask for it", () => {
		expect(canonicityScore(auditFile, ["memory", "system"])).toBeLessThan(
			canonicityScore("06-Altyapi/Hafiza-Sistemi/Rapor.md", ["memory", "system"]),
		);
	});

	it("lifts the penalty when the query asks for that very thing", () => {
		// Measured defect: this file was demoted to 0.245 for the query
		// "memory system repair audit" — the penalty suppressed the answer.
		const asked = canonicityScore(auditFile, ["memory", "repair", "audit"]);
		const blind = canonicityScore(auditFile, []);
		expect(asked).toBeGreaterThan(blind);
	});

	it("lifts the penalty across the bridge too", () => {
		// Turkish query, Turkish marker, English-authored table entry.
		const asked = canonicityScore(auditFile, ["denetim"]);
		expect(asked).toBeGreaterThan(canonicityScore(auditFile, []));
	});

	it("negative control: an unrelated query leaves the penalty in place", () => {
		expect(canonicityScore(auditFile, ["kitap", "sunum"])).toBe(
			canonicityScore(auditFile, []),
		);
	});
});

describe("buildFts5Query expansion", () => {
	const opts = { minTokenLength: 2 };

	it("negative control: without an expander the query is unchanged", () => {
		// If this ever fails, the bridge has leaked into callers that did not
		// ask for it — including every existing test's expectations.
		expect(buildFts5Query("agent protocol", opts)).toBe('"agent" OR "protocol"');
	});

	it("adds equivalents as separate OR arms", () => {
		const q = buildFts5Query("agent protocol", { ...opts, expandTerm: bridgeTerms });
		expect(q).toContain('"agent"');
		expect(q).toContain('"ajan"');
		expect(q).toContain('"protokol"');
	});

	it("does not duplicate an arm", () => {
		const q = buildFts5Query("agent ajan", { ...opts, expandTerm: bridgeTerms });
		const arms = q.split(" OR ");
		expect(new Set(arms).size).toBe(arms.length);
	});

	it("keeps the FTS5 grammar intact when an equivalent carries punctuation", () => {
		const q = buildFts5Query("thing", {
			...opts,
			expandTerm: () => ['bad"quote', "dash-word"],
		});
		// Quotes must balance, or FTS5 raises a syntax error at MATCH time.
		expect((q.match(/"/g) ?? []).length % 2).toBe(0);
		expect(q).not.toContain('bad"quote');
	});

	it("an empty query stays empty regardless of the expander", () => {
		expect(buildFts5Query("", { ...opts, expandTerm: bridgeTerms })).toBe("");
	});
});
