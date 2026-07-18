import { writeFileSync } from "node:fs";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import {
	type TimelineGraphAdapter,
	TimelineInputSchema,
	timelineTool,
} from "../../src/tools/timeline.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

describe("TimelineInputSchema", () => {
	it("requires subject", () => {
		expect(() => TimelineInputSchema.parse({})).toThrow();
	});

	it("rejects malformed dates", () => {
		expect(() =>
			TimelineInputSchema.parse({ subject: "x", valid_from: "yesterday" }),
		).toThrow();
	});

	it("rejects impossible calendar dates", () => {
		expect(() =>
			TimelineInputSchema.parse({ subject: "x", valid_to: "2026-02-30" }),
		).toThrow();
	});

	it("rejects a reversed valid-time range", () => {
		expect(() =>
			TimelineInputSchema.parse({
				subject: "x",
				valid_from: "2026-03-01",
				valid_to: "2026-02-01",
			}),
		).toThrow();
	});

	it("defaults top_k to 25", () => {
		const parsed = TimelineInputSchema.parse({ subject: "x" });
		expect(parsed.top_k).toBe(25);
	});

	it("accepts a concrete scope and rejects whitespace overrides", () => {
		expect(
			TimelineInputSchema.parse({ subject: "x", scope: "clinical" }).scope,
		).toBe("clinical");
		expect(() =>
			TimelineInputSchema.parse({ subject: "x", scope: " clinical " }),
		).toThrow();
		expect(() =>
			TimelineInputSchema.parse({ subject: "x", scope: "   " }),
		).toThrow();
	});
});

describe("timelineTool runtime", () => {
	it("preserves stale-index error classification", async () => {
		const { vault } = makeTempVault("tl-stale", defaultDocs());
		const db = new Database(vault.fts5Db);
		try {
			db.prepare(
				"DELETE FROM index_meta WHERE key='ascii_normalization_profile'",
			).run();
		} finally {
			db.close();
		}

		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(false);
		if (!res.ok) {
			expect(res.error.code).toBe(ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH);
		}
	});

	it("INDEX_NOT_FOUND when fts5.sqlite missing", async () => {
		const { vault } = makeTempVault("tl-noindex", []);
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "anything" }),
			vault,
		);
		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
	});

	it("orders entries ASC by mtime", async () => {
		const { vault } = makeTempVault("tl-asc", defaultDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "memory retrieval privacy" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const mtimes = res.data.entries.map((e) => e.mtime);
			const sorted = [...mtimes].sort((a, b) => a - b);
			expect(mtimes).toEqual(sorted);
		}
	});

	it("source is fts5 and as_of_applied is false when kg inactive", async () => {
		const { vault } = makeTempVault("tl-source", defaultDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.source).toBe("fts5");
			expect(res.data.as_of_applied).toBe(false);
			expect(res.data.facts).toBeUndefined();
		}
	});

	it("respects valid_from and valid_to bounds", async () => {
		const { vault } = makeTempVault("tl-bounds", defaultDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "memory retrieval privacy",
				valid_from: "2026-05-25",
				valid_to: "2026-06-01",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const mtimes = res.data.entries.map((e) => e.mtime);
			const sorted = [...mtimes].sort((a, b) => a - b);
			expect(mtimes).toEqual(sorted);
		}
	});

	it("stays fts5-only when kg flag is set but credentials missing", async () => {
		const { vault } = makeTempVault("tl-flag-nocreds", defaultDocs());
		writeFileSync(vault.kgActiveFlag, "on\n", "utf8");
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "rank fusion" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.source).toBe("fts5");
			expect(res.data.facts).toBeUndefined();
		}
	});

	it("applies one as_of plan to FTS5 and a successful graph snapshot", async () => {
		const { vault } = makeTempVault("tl-as-of", defaultDocs());
		const captured: Array<Record<string, unknown>> = [];
		const driver = {
			session: () => ({
				run: async () => ({ records: [] }),
				close: async () => undefined,
			}),
			close: async () => undefined,
		};
		const graph: TimelineGraphAdapter = {
			isActive: () => true,
			createDriver: async () => driver,
			query: async (_driver, _subject, opts) => {
				captured.push(opts);
				return { facts: [], asOfApplied: true, querySucceeded: true };
			},
			close: async () => undefined,
		};
		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "rank fusion",
				valid_to: "2026-06-01",
				as_of: "2026-06-15",
			}),
			vault,
			graph,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.as_of_applied).toBe(true);
			expect(res.data.source).toBe("fts5");
		}
		expect(captured).toEqual([
			expect.objectContaining({
				validToExclusive: "2026-06-02T00:00:00.000Z",
				asOf: "2026-06-15T00:00:00.000Z",
				scope: "default",
			}),
		]);
	});

	it("keeps the FTS5 as_of guarantee when the graph query fails closed", async () => {
		const { vault } = makeTempVault("tl-as-of-failed", defaultDocs());
		const driver = {
			session: () => ({
				run: async () => ({ records: [] }),
				close: async () => undefined,
			}),
			close: async () => undefined,
		};
		const graph: TimelineGraphAdapter = {
			isActive: () => true,
			createDriver: async () => driver,
			query: async () => ({
				facts: [],
				asOfApplied: false,
				querySucceeded: false,
			}),
			close: async () => undefined,
		};
		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "rank fusion",
				as_of: "2026-06-15",
			}),
			vault,
			graph,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.as_of_applied).toBe(true);
			expect(res.data.facts).toBeUndefined();
		}
	});

	it("uses as_of as an FTS5 transaction-time upper bound without KG", async () => {
		const { vault } = makeTempVault("tl-local-as-of", defaultDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "memory retrieval privacy",
				as_of: "2024-05-18",
			}),
			vault,
		);

		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.as_of_applied).toBe(true);
			const cutoff = Date.parse("2024-05-18T00:00:00Z") / 1000;
			expect(res.data.entries.every((entry) => entry.mtime <= cutoff)).toBe(
				true,
			);
		}
	});

	it("drops graph facts when the graph cannot prove the requested snapshot", async () => {
		const { vault } = makeTempVault("tl-unfiltered-graph", defaultDocs());
		const driver = {
			session: () => ({
				run: async () => ({ records: [] }),
				close: async () => undefined,
			}),
			close: async () => undefined,
		};
		const graph: TimelineGraphAdapter = {
			isActive: () => true,
			createDriver: async () => driver,
			query: async () => ({
				facts: [{ fact: "unfiltered graph fact" }],
				asOfApplied: false,
				querySucceeded: true,
			}),
			close: async () => undefined,
		};

		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "rank fusion",
				as_of: "2026-06-15",
			}),
			vault,
			graph,
		);

		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.as_of_applied).toBe(true);
			expect(res.data.facts).toBeUndefined();
			expect(res.data.source).toBe("fts5");
		}
	});
});
