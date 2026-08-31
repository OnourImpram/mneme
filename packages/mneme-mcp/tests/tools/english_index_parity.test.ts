/**
 * Every retrieval tool must serve an English index, not only a Turkish one.
 *
 * WHY THIS EXISTS
 * `mneme_search` read `index_meta.normalization_profile` and adopted whatever
 * the index declared. `mneme_prime`, `mneme_summarize` and `mneme_timeline`
 * imported the Turkish normalizers directly and therefore passed an ASCII arm
 * on every call. `fts5Search` refuses an ASCII arm unless the index declares
 * `ascii_normalization_profile = 'tr-ascii-fold'`, so on an English index all
 * three failed with INDEX_STALE_OR_LOCALE_MISMATCH — measured, for every
 * query, including queries containing no Turkish characters at all.
 *
 * WHY NO TEST CAUGHT IT
 * `buildTestDb` hardcoded the Turkish profile. Every fixture in the suite was
 * a Turkish index, so three tools that only worked on Turkish indexes passed
 * everything. The fixture gained a `locale` argument for exactly this reason;
 * a test that cannot construct the failing condition cannot detect it.
 *
 * The defect was unreachable in practice while `--locale en` produced an
 * index that refused every query anyway. Fixing the CLI is what exposed it,
 * which is why both land in the same release.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { primeTool, PrimeInputSchema } from "../../src/tools/prime.js";
import { searchTool, SearchInputSchema } from "../../src/tools/search.js";
import { summarizeTool, SummarizeInputSchema } from "../../src/tools/summarize.js";
import { timelineTool, TimelineInputSchema } from "../../src/tools/timeline.js";
import { VaultConfig } from "../../src/vault/config.js";
import { buildTestDb, type FixtureLocale } from "../helpers/fts5_fixture.js";

const DOC = {
	path: "notes/release-engineering.md",
	title: "Release Engineering",
	titleNormalized: "release engineering",
	contentRaw: "The API returned a locale mismatch during rebuild.",
	contentNormalized: "the api returned a locale mismatch during rebuild.",
	mtime: 1_789_727_531,
	frontmatterType: "reference",
};

let dir: string;

function vaultWith(locale: FixtureLocale): VaultConfig {
	const root = join(dir, locale);
	const vault = new VaultConfig(root);
	buildTestDb(vault.fts5Db, [DOC], locale);
	return vault;
}

beforeEach(() => {
	dir = mkdtempSync(join(tmpdir(), "mneme-en-parity-"));
});

afterEach(() => {
	rmSync(dir, { recursive: true, force: true });
});

describe("retrieval tools on an English index", () => {
	it("the fixture really declares the English profile", () => {
		const vault = vaultWith("en");
		const db = new Database(vault.fts5Db, { readonly: true });
		const meta = Object.fromEntries(
			(
				db.prepare("SELECT key, value FROM index_meta").all() as Array<{
					key: string;
					value: string;
				}>
			).map((r) => [r.key, r.value]),
		);
		db.close();
		// Without this the rest of the file could pass against a Turkish index
		// and prove nothing at all.
		expect(meta.normalization_profile).toBe("en-unicode");
		expect(meta.ascii_normalization_profile).toBe("disabled");
	});

	it("mneme_search answers", () => {
		const vault = vaultWith("en");
		const r = searchTool(
			SearchInputSchema.parse({ query: "locale mismatch", scope: "*" }),
			vault,
		);
		expect(r.ok).toBe(true);
	});

	it("mneme_summarize answers instead of refusing", () => {
		const vault = vaultWith("en");
		const r = summarizeTool(
			SummarizeInputSchema.parse({ topic: "locale mismatch", scope: "*" }),
			vault,
		);
		return r.then((res) => {
			expect(res.ok).toBe(true);
		});
	});

	it("mneme_timeline answers instead of refusing", () => {
		const vault = vaultWith("en");
		const r = timelineTool(
			TimelineInputSchema.parse({ subject: "locale mismatch", scope: "*" }),
			vault,
		);
		return r.then((res) => {
			expect(res.ok).toBe(true);
		});
	});

	it("mneme_prime answers instead of refusing", () => {
		const vault = vaultWith("en");
		const r = primeTool(
			PrimeInputSchema.parse({
				task_description: "locale mismatch",
				scope: "*",
			}),
			vault,
		);
		expect(r.ok).toBe(true);
	});

	it("an English query is folded the English way, not the Turkish way", () => {
		const vault = vaultWith("en");
		// "API" folds to "api" under en-unicode and to "apı" under tr-cldr.
		// The stored token is "api", so a Turkish fold finds nothing.
		const r = searchTool(
			SearchInputSchema.parse({ query: "API", scope: "*" }),
			vault,
		);
		expect(r.ok).toBe(true);
		if (r.ok) expect(r.data.cards.length).toBeGreaterThan(0);
	});
});

describe("negative control — the Turkish path is untouched", () => {
	it("all four tools still answer on a Turkish index", async () => {
		const vault = vaultWith("tr");
		const q = "release engineering";
		expect(
			searchTool(SearchInputSchema.parse({ query: q, scope: "*" }), vault).ok,
		).toBe(true);
		expect(
			(
				await summarizeTool(
					SummarizeInputSchema.parse({ topic: q, scope: "*" }),
					vault,
				)
			).ok,
		).toBe(true);
		expect(
			(
				await timelineTool(
					TimelineInputSchema.parse({ subject: q, scope: "*" }),
					vault,
				)
			).ok,
		).toBe(true);
		expect(
			primeTool(
				PrimeInputSchema.parse({ task_description: q, scope: "*" }),
				vault,
			).ok,
		).toBe(true);
	});

	it("an index with an unrecognised profile is still refused", async () => {
		const vault = vaultWith("en");
		const db = new Database(vault.fts5Db);
		db.prepare(
			"UPDATE index_meta SET value='klingon-fold' WHERE key='normalization_profile'",
		).run();
		db.close();

		// Adopting the index's profile must not become "accept anything".
		const s = searchTool(
			SearchInputSchema.parse({ query: "locale", scope: "*" }),
			vault,
		);
		expect(s.ok).toBe(false);
		const z = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "locale", scope: "*" }),
			vault,
		);
		expect(z.ok).toBe(false);
		const t = await timelineTool(
			TimelineInputSchema.parse({ subject: "locale", scope: "*" }),
			vault,
		);
		expect(t.ok).toBe(false);
	});
});
