import { writeFileSync } from "node:fs";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import {
	SummarizeInputSchema,
	summarizeTool,
} from "../../src/tools/summarize.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

describe("SummarizeInputSchema", () => {
	it("requires topic", () => {
		expect(() => SummarizeInputSchema.parse({})).toThrow();
	});

	it("rejects malformed date_range entries", () => {
		expect(() =>
			SummarizeInputSchema.parse({
				topic: "x",
				date_range: ["2026-05-01", "junk"],
			}),
		).toThrow();
	});

	it("defaults top_k to 20", () => {
		const parsed = SummarizeInputSchema.parse({ topic: "x" });
		expect(parsed.top_k).toBe(20);
	});
});

describe("summarizeTool runtime", () => {
	it("preserves stale-index error classification", async () => {
		const { vault } = makeTempVault("sum-stale", defaultDocs());
		const db = new Database(vault.fts5Db);
		try {
			db.prepare(
				"DELETE FROM index_meta WHERE key='ascii_normalization_profile'",
			).run();
		} finally {
			db.close();
		}

		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(false);
		if (!res.ok) {
			expect(res.error.code).toBe(ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH);
		}
	});

	it("INDEX_NOT_FOUND when fts5.sqlite missing", async () => {
		const { vault } = makeTempVault("sum-noindex", []);
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "anything" }),
			vault,
		);
		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
	});

	it("groups hits by directory", async () => {
		const { vault } = makeTempVault("sum-group", defaultDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "memory retrieval privacy" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const dirs = Object.keys(res.data.directories);
			expect(dirs).toContain("reference");
		}
	});

	it("returns empty when topic has only stopwords", async () => {
		const { vault } = makeTempVault("sum-empty", defaultDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "the and or" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) expect(res.data.total_docs).toBe(0);
	});

	it("graph_expansion is false when kg active flag is absent", async () => {
		const { vault } = makeTempVault("sum-noflag", defaultDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.graph_expansion).toBe(false);
			expect(res.data.related_entities).toBeUndefined();
		}
	});

	it("graph_expansion stays false when flag is set but credentials missing", async () => {
		const { vault } = makeTempVault("sum-flag-nocreds", defaultDocs());
		// Activate but provide no credentials. createDriverFromVault must return null.
		writeFileSync(vault.kgActiveFlag, "on\n", "utf8");
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) expect(res.data.graph_expansion).toBe(false);
	});
});
