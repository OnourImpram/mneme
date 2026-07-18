/**
 * Scope isolation tests for all read tools (recall, search, prime,
 * summarize, timeline) and write/propose scope stamping (M2–M5).
 *
 * Seeds a vault with three documents — one per scope ('default',
 * 'clinical', 'freelance') — and verifies that each tool:
 *   1. Returns only in-scope rows when a scope arg is supplied.
 *   2. Falls back to config.defaultScope() ('default') when scope is
 *      omitted (no MNEME_SCOPE env and no config.toml override in tests).
 *   3. Returns all rows when scope = "*" (cross-scope).
 *
 * Also covers backward compatibility (pre-M1 index without a scope column)
 * and the M3 write/propose scope-stamping contract.
 */

import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimeInputSchema, primeTool } from "../../src/tools/prime.js";
import { ProposeInputSchema, proposeTool } from "../../src/tools/propose.js";
import { RecallInputSchema, recallTool } from "../../src/tools/recall.js";
import { SearchInputSchema, searchTool } from "../../src/tools/search.js";
import {
	SummarizeInputSchema,
	summarizeTool,
} from "../../src/tools/summarize.js";
import { TimelineInputSchema, timelineTool } from "../../src/tools/timeline.js";
import { WriteInputSchema, writeTool } from "../../src/tools/write.js";
import type { TestDoc } from "../helpers/fts5_fixture.js";
import { makeTempVault } from "../helpers/vault_fixture.js";

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

/** Three docs — one per scope — all matchable by the tokens "alpha" and "content". */
function scopeDocs(): TestDoc[] {
	return [
		{
			path: "default/session.md",
			title: "Alpha Default",
			titleNormalized: "alpha default",
			contentRaw: "alpha content default scope doc",
			bodyText: "alpha content default scope doc",
			contentNormalized: "alpha content default scope doc",
			mtime: 1_716_000_000,
			frontmatterType: "session",
			sessionId: "s-default",
			scope: "default",
		},
		{
			path: "clinical/session.md",
			title: "Alpha Clinical",
			titleNormalized: "alpha clinical",
			contentRaw: "alpha content clinical scope doc",
			bodyText: "alpha content clinical scope doc",
			contentNormalized: "alpha content clinical scope doc",
			mtime: 1_716_100_000,
			frontmatterType: "session",
			sessionId: "s-clinical",
			scope: "clinical",
		},
		{
			path: "freelance/session.md",
			title: "Alpha Freelance",
			titleNormalized: "alpha freelance",
			contentRaw: "alpha content freelance scope doc",
			bodyText: "alpha content freelance scope doc",
			contentNormalized: "alpha content freelance scope doc",
			mtime: 1_716_200_000,
			frontmatterType: "session",
			sessionId: "s-freelance",
			scope: "freelance",
		},
	];
}

/**
 * Stamp an index_meta row so searchTool does not emit a console.warn about
 * a missing normalization profile. Mirrors the helper in search.test.ts.
 */
function setIndexProfile(dbPath: string, profile: string): void {
	const db = new Database(dbPath);
	try {
		db.prepare(
			"CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
		).run();
		db.prepare(
			"INSERT OR REPLACE INTO index_meta(key, value) VALUES ('normalization_profile', ?)",
		).run(profile);
	} finally {
		db.close();
	}
}

/**
 * Build a pre-M1 SQLite database at dbPath that intentionally omits the
 * `scope` column. Used by the backward-compatibility test to verify that
 * tools degrade gracefully (unfiltered results, one-time stderr warning)
 * rather than throwing.
 */
function buildLegacyDbNoScope(dbPath: string): void {
	mkdirSync(dirname(dbPath), { recursive: true });
	const db = new Database(dbPath);
	try {
		db.prepare(
			`CREATE TABLE documents(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         title TEXT, path TEXT UNIQUE NOT NULL,
         content_raw TEXT, body_text TEXT,
         mtime REAL, frontmatter_type TEXT,
         session_id TEXT, content_hash TEXT, trust TEXT
       )`,
		).run();
		db.prepare(
			`CREATE VIRTUAL TABLE documents_fts USING fts5(
         title, content, tags, linked_notes,
         tokenize='unicode61 remove_diacritics 2'
       )`,
		).run();
		const ins = db.prepare(
			`INSERT INTO documents(title, path, content_raw, body_text, mtime,
                             frontmatter_type, session_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
		);
		const insFts = db.prepare(
			`INSERT INTO documents_fts(rowid, title, content, tags, linked_notes)
       VALUES (?, ?, ?, ?, ?)`,
		);
		const tx = db.transaction(() => {
			const info = ins.run(
				"Legacy Doc",
				"legacy/doc.md",
				"legacy content",
				"legacy content",
				1_716_000_000,
				"session",
				"s-legacy",
			);
			insFts.run(info.lastInsertRowid, "legacy doc", "legacy content", "", "");
		});
		tx();
	} finally {
		db.close();
	}
}

// ---------------------------------------------------------------------------
// recallTool
// ---------------------------------------------------------------------------

describe("recallTool scope isolation", () => {
	it("scope arg filters documents table to matching scope only", () => {
		const { vault } = makeTempVault("scope-recall-explicit", scopeDocs());
		const res = recallTool(
			RecallInputSchema.parse({ scope: "clinical", include_body: false }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(1);
			expect(res.data.entries[0]?.path).toBe("clinical/session.md");
		}
	});

	it("scope omitted falls to defaultScope ('default'), returns only default-scope rows", () => {
		const { vault } = makeTempVault("scope-recall-default", scopeDocs());
		const res = recallTool(
			RecallInputSchema.parse({ include_body: false }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(1);
			expect(res.data.entries[0]?.path).toBe("default/session.md");
		}
	});

	it('scope "*" returns all rows cross-scope', () => {
		const { vault } = makeTempVault("scope-recall-star", scopeDocs());
		const res = recallTool(
			RecallInputSchema.parse({ scope: "*", include_body: false, top_n: 10 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(3);
		}
	});
});

// ---------------------------------------------------------------------------
// searchTool
// ---------------------------------------------------------------------------

describe("searchTool scope isolation", () => {
	it("scope arg filters FTS hits to matching scope only", () => {
		const { vault } = makeTempVault("scope-search-explicit", scopeDocs());
		setIndexProfile(vault.fts5Db, "tr-cldr");
		const res = searchTool(
			SearchInputSchema.parse({ query: "alpha content", scope: "clinical" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.hits.length).toBe(1);
			expect(res.data.hits[0]?.path).toBe("clinical/session.md");
		}
	});

	it("scope omitted falls to defaultScope, returns only default-scope hits", () => {
		const { vault } = makeTempVault("scope-search-default", scopeDocs());
		setIndexProfile(vault.fts5Db, "tr-cldr");
		const res = searchTool(
			SearchInputSchema.parse({ query: "alpha content" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.hits.length).toBe(1);
			expect(res.data.hits[0]?.path).toBe("default/session.md");
		}
	});

	it('scope "*" returns hits across all scopes', () => {
		const { vault } = makeTempVault("scope-search-star", scopeDocs());
		setIndexProfile(vault.fts5Db, "tr-cldr");
		const res = searchTool(
			SearchInputSchema.parse({
				query: "alpha content",
				scope: "*",
				top_k: 10,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.hits.length).toBe(3);
		}
	});
});

// ---------------------------------------------------------------------------
// primeTool
// ---------------------------------------------------------------------------

describe("primeTool scope isolation", () => {
	// topic_doc_count: 0 disables topicMatches so only listRecentSessions
	// contributes to sources, giving deterministic assertions per scope.

	it("scope arg restricts recent sessions to matching scope only", () => {
		const { vault } = makeTempVault("scope-prime-explicit", scopeDocs());
		const res = primeTool(
			PrimeInputSchema.parse({
				task_description: "alpha",
				scope: "clinical",
				recent_session_count: 5,
				topic_doc_count: 0,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const paths = res.data.sources.map((s) => s.path);
			expect(paths.length).toBeGreaterThan(0);
			expect(paths.every((p) => p.startsWith("clinical/"))).toBe(true);
		}
	});

	it("scope omitted falls to defaultScope, only default-scope sessions appear in sources", () => {
		const { vault } = makeTempVault("scope-prime-default", scopeDocs());
		const res = primeTool(
			PrimeInputSchema.parse({
				task_description: "alpha",
				recent_session_count: 5,
				topic_doc_count: 0,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const paths = res.data.sources.map((s) => s.path);
			expect(paths.every((p) => p.startsWith("default/"))).toBe(true);
		}
	});

	it('scope "*" includes sessions from all scopes', () => {
		const { vault } = makeTempVault("scope-prime-star", scopeDocs());
		const res = primeTool(
			PrimeInputSchema.parse({
				task_description: "alpha",
				scope: "*",
				recent_session_count: 5,
				topic_doc_count: 0,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.sources.length).toBe(3);
		}
	});
});

// ---------------------------------------------------------------------------
// summarizeTool
// ---------------------------------------------------------------------------

describe("summarizeTool scope isolation", () => {
	it("scope arg limits grouped FTS hits to matching scope", async () => {
		const { vault } = makeTempVault("scope-sum-explicit", scopeDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "alpha content", scope: "clinical" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.total_docs).toBe(1);
			const paths = Object.values(res.data.directories)
				.flat()
				.map((d) => d.path);
			expect(paths[0]).toBe("clinical/session.md");
		}
	});

	it("scope omitted falls to defaultScope, only default-scope docs grouped", async () => {
		const { vault } = makeTempVault("scope-sum-default", scopeDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "alpha content" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.total_docs).toBe(1);
			const paths = Object.values(res.data.directories)
				.flat()
				.map((d) => d.path);
			expect(paths[0]).toBe("default/session.md");
		}
	});

	it('scope "*" groups docs from all scopes', async () => {
		const { vault } = makeTempVault("scope-sum-star", scopeDocs());
		const res = await summarizeTool(
			SummarizeInputSchema.parse({ topic: "alpha content", scope: "*" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.total_docs).toBe(3);
		}
	});
});

// ---------------------------------------------------------------------------
// timelineTool
// ---------------------------------------------------------------------------

describe("timelineTool scope isolation", () => {
	it("scope arg restricts FTS timeline entries to matching scope", async () => {
		const { vault } = makeTempVault("scope-tl-explicit", scopeDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({
				subject: "alpha content",
				scope: "clinical",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(1);
			expect(res.data.entries[0]?.path).toBe("clinical/session.md");
		}
	});

	it("scope omitted falls to defaultScope, only default-scope entries returned", async () => {
		const { vault } = makeTempVault("scope-tl-default", scopeDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "alpha content" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(1);
			expect(res.data.entries[0]?.path).toBe("default/session.md");
		}
	});

	it('scope "*" returns entries across all scopes', async () => {
		const { vault } = makeTempVault("scope-tl-star", scopeDocs());
		const res = await timelineTool(
			TimelineInputSchema.parse({ subject: "alpha content", scope: "*" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.length).toBe(3);
		}
	});
});

// ---------------------------------------------------------------------------
// Pre-scope index migration safety
// ---------------------------------------------------------------------------

describe("pre-scope index (no scope column)", () => {
	it("fails closed for concrete scope and permits explicit cross-scope access", () => {
		const { vault } = makeTempVault("scope-compat", []);
		buildLegacyDbNoScope(vault.fts5Db);
		const spy = vi
			.spyOn(process.stderr, "write")
			.mockImplementation(() => true);
		try {
			const scoped = recallTool(
				RecallInputSchema.parse({ include_body: false }),
				vault,
			);
			expect(scoped.ok).toBe(false);
			if (!scoped.ok) {
				expect(scoped.error.code).toBe("INDEX_STALE_OR_LOCALE_MISMATCH");
				expect(scoped.error.message).toContain("index rebuild");
			}

			const crossScope = recallTool(
				RecallInputSchema.parse({ scope: "*", include_body: false }),
				vault,
			);
			expect(crossScope.ok).toBe(true);
			if (crossScope.ok) {
				expect(crossScope.data.entries).toHaveLength(1);
				expect(crossScope.data.entries[0]?.path).toBe("legacy/doc.md");
			}
			expect(spy).toHaveBeenCalledTimes(1);
		} finally {
			spy.mockRestore();
		}
	});
});

// ---------------------------------------------------------------------------
// writeTool — scope stamping on new files (M3)
// ---------------------------------------------------------------------------

describe("writeTool scope stamping (M3)", () => {
	it("stamps defaultScope into frontmatter when creating a file with no frontmatter arg", () => {
		const { vault, rootDir } = makeTempVault("scope-write-stamp", []);
		const res = writeTool(
			WriteInputSchema.parse({
				path: "notes/new.md",
				section: "Test Section",
				content: "body content",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) expect(res.data.created_new_file).toBe(true);
		const written = readFileSync(join(rootDir, "notes/new.md"), "utf8");
		expect(written).toContain('scope: "default"');
	});

	it("respects caller-supplied scope in frontmatter over defaultScope", () => {
		const { vault, rootDir } = makeTempVault("scope-write-caller", []);
		const res = writeTool(
			WriteInputSchema.parse({
				path: "notes/clinical.md",
				section: "Notes",
				content: "clinical body",
				frontmatter: { scope: "clinical" },
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(join(rootDir, "notes/clinical.md"), "utf8");
		expect(written).toContain('scope: "clinical"');
		expect(written).not.toContain('"default"');
	});
});

// ---------------------------------------------------------------------------
// proposeTool — scope field in JSONL record (M3)
// ---------------------------------------------------------------------------

describe("proposeTool scope stamping (M3)", () => {
	it("stamps defaultScope into the queued JSONL record when scope is not supplied", () => {
		const { vault } = makeTempVault("scope-propose-default", []);
		const res = proposeTool(
			ProposeInputSchema.parse({
				action: "create",
				path: "notes/new.md",
				content: "proposal body",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const queuePath = join(vault.stateDir, "proposals", "pending.jsonl");
		const record = JSON.parse(readFileSync(queuePath, "utf8").trim()) as {
			scope?: string;
		};
		expect(record.scope).toBe("default");
	});

	it("uses caller-supplied scope when provided", () => {
		const { vault } = makeTempVault("scope-propose-caller", []);
		const res = proposeTool(
			ProposeInputSchema.parse({
				action: "create",
				path: "notes/clin.md",
				content: "clinical proposal",
				scope: "clinical",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const queuePath = join(vault.stateDir, "proposals", "pending.jsonl");
		const record = JSON.parse(readFileSync(queuePath, "utf8").trim()) as {
			scope?: string;
		};
		expect(record.scope).toBe("clinical");
	});
});

// ---------------------------------------------------------------------------
// Test-gap A — scope field size bound (.max(256))
// ---------------------------------------------------------------------------

describe("Test-gap A: scope field size bound (.max(256))", () => {
	const oversizeScope = "x".repeat(257);

	it("RecallInputSchema rejects scope > 256 chars", () => {
		const result = RecallInputSchema.safeParse({
			include_body: false,
			scope: oversizeScope,
		});
		expect(result.success).toBe(false);
	});

	it("SearchInputSchema rejects scope > 256 chars", () => {
		const result = SearchInputSchema.safeParse({
			query: "hello",
			scope: oversizeScope,
		});
		expect(result.success).toBe(false);
	});

	it("SummarizeInputSchema rejects scope > 256 chars", () => {
		const result = SummarizeInputSchema.safeParse({
			topic: "hello",
			scope: oversizeScope,
		});
		expect(result.success).toBe(false);
	});

	it("TimelineInputSchema rejects scope > 256 chars", () => {
		const result = TimelineInputSchema.safeParse({
			subject: "hello",
			scope: oversizeScope,
		});
		expect(result.success).toBe(false);
	});

	it("PrimeInputSchema rejects scope > 256 chars", () => {
		const result = PrimeInputSchema.safeParse({
			task_description: "hello",
			scope: oversizeScope,
		});
		expect(result.success).toBe(false);
	});

	it("accepts scope exactly at the 256-char limit", () => {
		const exactScope = "x".repeat(256);
		const result = SearchInputSchema.safeParse({
			query: "hello",
			scope: exactScope,
		});
		expect(result.success).toBe(true);
	});
});

// ---------------------------------------------------------------------------
// Test-gap B — MNEME_SCOPE env end-to-end
// ---------------------------------------------------------------------------

describe("Test-gap B: MNEME_SCOPE env end-to-end", () => {
	afterEach(() => {
		vi.unstubAllEnvs();
	});

	it("search tool filters to MNEME_SCOPE scope when no explicit scope arg is given", () => {
		vi.stubEnv("MNEME_SCOPE", "clinical");
		const { vault } = makeTempVault("scope-env-e2e-search", scopeDocs());
		setIndexProfile(vault.fts5Db, "tr-cldr");
		// No explicit scope arg: vault.defaultScope() reads MNEME_SCOPE=clinical
		const res = searchTool(
			SearchInputSchema.parse({ query: "alpha content" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			// Only the clinical-scoped doc should be returned
			expect(res.data.hits.length).toBe(1);
			expect(res.data.hits[0]?.path).toBe("clinical/session.md");
		}
	});
});
