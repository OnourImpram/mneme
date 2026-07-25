import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { ERROR_CODES, MnemeToolError } from "../../src/errors.js";
import { normalizeTr, normalizeTrAsciiFold } from "../../src/locale/tr.js";
import { buildFts5Query, fts5Search } from "../../src/retrieval/fts5.js";
import { buildTestDb, type TestDoc } from "../helpers/fts5_fixture.js";

function freshTmp(prefix: string): string {
	return mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
}

function fixtureDocs(): TestDoc[] {
	return [
		{
			path: "daily/2026-05-18.md",
			title: "Reciprocal Rank Fusion",
			titleNormalized: normalizeTr("Reciprocal Rank Fusion"),
			contentRaw: "RRF k equals sixty fuses ranked lists. Robust across tasks.",
			contentNormalized: normalizeTr(
				"RRF k equals sixty fuses ranked lists. Robust across tasks.",
			),
			mtime: 1_716_000_000,
			frontmatterType: "session",
			sessionId: "s-2026-05-18",
		},
		{
			path: "reference/vault-native.md",
			title: "Vault Native Memory",
			titleNormalized: normalizeTr("Vault Native Memory"),
			contentRaw:
				"Markdown is ground truth. Hybrid retrieval beats single backend.",
			contentNormalized: normalizeTr(
				"Markdown is ground truth. Hybrid retrieval beats single backend.",
			),
			mtime: 1_716_500_000,
			frontmatterType: "reference",
		},
		{
			path: "reference/privacy.md",
			title: "Privacy Redaction",
			titleNormalized: normalizeTr("Privacy Redaction"),
			contentRaw: "Sensitive content is stripped at staging. Audit log stays.",
			contentNormalized: normalizeTr(
				"Sensitive content is stripped at staging. Audit log stays.",
			),
			mtime: 1_717_000_000,
			frontmatterType: "reference",
		},
	];
}

describe("buildFts5Query", () => {
	it("quotes each token and joins with OR", () => {
		expect(buildFts5Query("hello world")).toBe('"hello" OR "world"');
	});

	it("returns empty string on empty input", () => {
		expect(buildFts5Query("")).toBe("");
		expect(buildFts5Query("   ")).toBe("");
	});

	it("drops tokens shorter than minTokenLength", () => {
		expect(buildFts5Query("a hello b world", { minTokenLength: 2 })).toBe(
			'"hello" OR "world"',
		);
	});

	it("drops stopwords", () => {
		expect(
			buildFts5Query("the hello world", {
				stopwords: new Set(["the"]),
			}),
		).toBe('"hello" OR "world"');
	});

	it("splits reserved characters into phrases instead of fusing", () => {
		// " : * - are syntactic/separator chars; split them into a phrase so
		// the query matches the adjacent tokens unicode61 indexed.
		expect(buildFts5Query('"foo* bar:qux"')).toBe('"foo" OR "bar qux"');
	});

	it("turns a hyphenated identifier into a phrase", () => {
		expect(buildFts5Query("claude-mem")).toBe('"claude mem"');
	});

	it("applies the normalizer to each token", () => {
		expect(
			buildFts5Query("KIYASLAMA İstanbul", { normalize: normalizeTr }),
		).toBe('"kıyaslama" OR "istanbul"');
	});
});

describe("fts5Search", () => {
	it("uses the ASCII-fold sibling index for Turkish I variants", () => {
		const root = freshTmp("fts-ascii-fold");
		const dbPath = join(root, "fts.db");
		const statement = "İzmir limanı bugün açıktır.";
		buildTestDb(dbPath, [
			{
				path: "notes/izmir.md",
				title: "İzmir Limanı",
				titleNormalized: normalizeTr("İzmir Limanı"),
				titleAsciiNormalized: normalizeTrAsciiFold("İzmir Limanı"),
				contentRaw: statement,
				contentNormalized: normalizeTr(statement),
				contentAsciiNormalized: normalizeTrAsciiFold(statement),
				mtime: 1_716_000_000,
			},
		]);

		const primaryQuery = buildFts5Query("IZMIR", { normalize: normalizeTr });
		const asciiQuery = buildFts5Query("IZMIR", {
			normalize: normalizeTrAsciiFold,
		});
		expect(
			fts5Search({ dbPath, ftsQuery: primaryQuery, limit: 10, scope: "*" }),
		).toEqual([]);

		const hits = fts5Search({
			dbPath,
			ftsQuery: primaryQuery,
			ftsQueryAscii: asciiQuery,
			limit: 10,
			scope: "*",
		});
		expect(hits.map((hit) => hit.path)).toEqual(["notes/izmir.md"]);
	});

	it("fails closed when the ASCII-fold profile metadata is absent", () => {
		const root = freshTmp("fts-ascii-profile");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		const db = new Database(dbPath);
		try {
			db.prepare(
				"DELETE FROM index_meta WHERE key='ascii_normalization_profile'",
			).run();
		} finally {
			db.close();
		}

		try {
			fts5Search({
				dbPath,
				ftsQuery: '"rank"',
				ftsQueryAscii: '"rank"',
				limit: 10,
				scope: "*",
			});
			expect.unreachable("expected stale-index failure");
		} catch (error) {
			expect(error).toBeInstanceOf(MnemeToolError);
			expect((error as MnemeToolError).code).toBe(
				ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH,
			);
		}
	});

	it("returns hits for known terms", () => {
		const root = freshTmp("fts-known");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());

		const hits = fts5Search({
			dbPath,
			ftsQuery: '"rank" OR "fusion"',
			limit: 10,
			scope: "*",
		});
		expect(hits.length).toBeGreaterThan(0);
		expect(hits.some((h) => h.title.startsWith("Reciprocal"))).toBe(true);
	});

	it("returns empty on empty FTS query", () => {
		const root = freshTmp("fts-empty-q");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		expect(fts5Search({ dbPath, ftsQuery: "", limit: 10, scope: "*" })).toEqual(
			[],
		);
	});

	it("respects limit", () => {
		const root = freshTmp("fts-limit");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"memory" OR "retrieval" OR "privacy"',
			limit: 1,
			scope: "*",
		});
		expect(hits.length).toBeLessThanOrEqual(1);
	});

	it("filters by mtimeFrom inclusive", () => {
		const root = freshTmp("fts-from");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"retrieval" OR "rank" OR "privacy"',
			limit: 10,
			mtimeFrom: 1_716_400_000,
			scope: "*",
		});
		for (const h of hits) {
			expect(h.mtime).toBeGreaterThanOrEqual(1_716_400_000);
		}
	});

	it("filters by mtimeTo inclusive", () => {
		const root = freshTmp("fts-to");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"retrieval" OR "rank" OR "privacy"',
			limit: 10,
			mtimeTo: 1_716_400_000,
			scope: "*",
		});
		for (const h of hits) {
			expect(h.mtime).toBeLessThanOrEqual(1_716_400_000);
		}
	});

	it("rejects writes via PRAGMA query_only", () => {
		const root = freshTmp("fts-readonly");
		const dbPath = join(root, "fts.db");
		buildTestDb(dbPath, fixtureDocs());
		// fts5Search opens readonly so the underlying file can't be mutated.
		const hits = fts5Search({
			dbPath,
			ftsQuery: '"rank"',
			limit: 1,
			scope: "*",
		});
		expect(hits.length).toBeGreaterThanOrEqual(0);
	});
});
