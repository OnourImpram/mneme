/**
 * mneme_health contract.
 *
 * WHY THIS FILE EXISTS
 * `mneme_health` shipped in 4.0 as the tool that reports whether the system is
 * healthy — and it was the only module in the package with no tests at all
 * (6.57% statements, 0% branches). The 4.1 release gate is what surfaced it:
 * global branch coverage fell to 78.07% against an 80% threshold, and every
 * uncovered branch was in here. A health detector without its own measurement
 * is exactly the failure mode the tool was written to prevent.
 *
 * WHAT IS PINNED
 * Each warning path gets a positive case (the condition fires) AND a negative
 * control (the condition is absent and the warning does NOT fire). Without the
 * negative half, a detector hard-wired to always warn would pass every test
 * here — and an alarm that never goes quiet is noise, which is worse than no
 * alarm at all.
 *
 * The load-bearing contract, checked across every scenario: EVERY warning
 * carries a non-empty `remedy`. A detector ships with its remedy or it becomes
 * a signal nobody can act on.
 */

import { mkdirSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { HealthInputSchema, healthTool } from "../../src/tools/health.js";
import { buildTestDb } from "../helpers/fts5_fixture.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

const DAY = 86_400;

/** Docs whose mtime is N days old, so index staleness is controllable. */
function docsAged(days: number) {
	const mtime = Math.floor(Date.now() / 1000) - days * DAY;
	return defaultDocs().map((d) => ({ ...d, mtime }));
}

function args(overrides: Record<string, unknown> = {}) {
	return HealthInputSchema.parse(overrides);
}

/** Set an index_meta key, or delete it when value is null. */
function setMeta(dbPath: string, key: string, value: string | null): void {
	const db = new Database(dbPath);
	try {
		if (value === null) {
			db.prepare("DELETE FROM index_meta WHERE key = ?").run(key);
		} else {
			db.prepare(
				"INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
			).run(key, value);
		}
	} finally {
		db.close();
	}
}

/** Write a staging file whose mtime is `days` old. */
function stageFile(dir: string, name: string, days: number): void {
	mkdirSync(dir, { recursive: true });
	const full = join(dir, name);
	writeFileSync(full, "staged\n", "utf8");
	const when = Date.now() / 1000 - days * DAY;
	utimesSync(full, when, when);
}

function codes(warnings: Array<{ code: string }>): string[] {
	return warnings.map((w) => w.code);
}

describe("healthTool — a healthy index", () => {
	it("reports ok with no warnings", () => {
		const { vault } = makeTempVault("health-ok", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.warnings).toEqual([]);
		expect(res.data.ok).toBe(true);
		expect(res.data.schema.matches).toBe(true);
		expect(res.data.index.exists).toBe(true);
		expect(res.data.index.documentCount).toBe(3);
	});

	it("reports the locale profile the index was built with", () => {
		const { vault } = makeTempVault("health-locale", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.locale.profile).toBe("tr-cldr");
		expect(res.data.locale.asciiProfile).toBe("tr-ascii-fold");
		expect(res.data.locale.recognized).toBe(true);
	});
});

describe("healthTool — missing index", () => {
	it("reports INDEX_NOT_FOUND instead of throwing", () => {
		const { vault } = makeTempVault("health-noindex");
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("INDEX_NOT_FOUND");
		expect(res.data.ok).toBe(false);
		expect(res.data.index.exists).toBe(false);
		expect(res.data.index.documentCount).toBeNull();
		expect(res.data.schema.stored).toBeNull();
	});

	it("names the path it looked at, so the remedy is actionable", () => {
		const { vault } = makeTempVault("health-noindex-path");
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		const w = res.data.warnings.find((x) => x.code === "INDEX_NOT_FOUND");
		expect(w?.detail).toContain(vault.fts5Db);
		expect(w?.remedy).toContain("rebuild");
	});
});

describe("healthTool — schema version", () => {
	it("warns when the stored schema is older than this client speaks", () => {
		const { vault } = makeTempVault("health-schema-old", docsAged(0));
		setMeta(vault.fts5Db, "schema_version", "3");
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("SCHEMA_MISMATCH");
		expect(res.data.schema.stored).toBe("3");
		expect(res.data.schema.matches).toBe(false);
	});

	it("warns when the index carries no schema version at all", () => {
		const { vault } = makeTempVault("health-schema-absent", docsAged(0));
		setMeta(vault.fts5Db, "schema_version", null);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("SCHEMA_MISMATCH");
		expect(res.data.schema.stored).toBeNull();
	});

	/** NEGATIVE CONTROL: a matching schema must produce no schema warning. */
	it("negative control: a matching schema raises nothing", () => {
		const { vault } = makeTempVault("health-schema-ok", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).not.toContain("SCHEMA_MISMATCH");
	});
});

describe("healthTool — locale profile", () => {
	it("warns when the normalizer profile is unrecognized", () => {
		const { vault } = makeTempVault("health-locale-bad", docsAged(0));
		setMeta(vault.fts5Db, "normalization_profile", "klingon-fold");
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("LOCALE_PROFILE_UNRECOGNIZED");
		expect(res.data.locale.recognized).toBe(false);
	});

	it("warns when the profile is absent entirely", () => {
		const { vault } = makeTempVault("health-locale-absent", docsAged(0));
		setMeta(vault.fts5Db, "normalization_profile", null);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("LOCALE_PROFILE_UNRECOGNIZED");
		expect(res.data.locale.profile).toBeNull();
	});

	/** NEGATIVE CONTROL. */
	it("negative control: a recognized profile raises nothing", () => {
		const { vault } = makeTempVault("health-locale-good", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).not.toContain(
			"LOCALE_PROFILE_UNRECOGNIZED",
		);
	});
});

describe("healthTool — index staleness", () => {
	it("warns when the newest document is older than the threshold", () => {
		// This is the defect the tool was built for: an index that answers
		// confidently while silently missing everything written since.
		const { vault } = makeTempVault("health-stale", docsAged(40));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("INDEX_STALE");
		expect(res.data.index.newestDocumentAgeDays).toBeGreaterThan(7);
	});

	/** NEGATIVE CONTROL: a fresh index must not report staleness. */
	it("negative control: a fresh index raises nothing", () => {
		const { vault } = makeTempVault("health-fresh", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).not.toContain("INDEX_STALE");
		expect(res.data.index.newestDocumentAgeDays).toBeLessThan(1);
	});

	it("handles an empty documents table without dividing by nothing", () => {
		const { vault } = makeTempVault("health-empty");
		buildTestDb(vault.fts5Db, []);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.index.documentCount).toBe(0);
		expect(res.data.index.newestDocumentAgeDays).toBeNull();
		expect(codes(res.data.warnings)).not.toContain("INDEX_STALE");
	});
});

describe("healthTool — staging backlog", () => {
	it("warns when the oldest pending file is past the backlog threshold", () => {
		const { vault } = makeTempVault("health-staging", docsAged(0));
		stageFile(vault.stagingDir, "pending-1.md", 30);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("STAGING_NOT_DRAINING");
		expect(res.data.staging.pendingFiles).toBe(1);
		expect(res.data.staging.oldestAgeDays).toBeGreaterThan(3);
	});

	/**
	 * NEGATIVE CONTROL for the archive rule. Files under `staging/archive/`
	 * are already processed; counting them conflates a healthy archive with a
	 * stuck queue, which is precisely the misdiagnosis this exclusion prevents.
	 */
	it("negative control: archived files are neither counted nor warned about", () => {
		const { vault } = makeTempVault("health-staging-archive", docsAged(0));
		stageFile(join(vault.stagingDir, "archive"), "done-1.md", 90);
		stageFile(join(vault.stagingDir, "archive"), "done-2.md", 90);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.staging.pendingFiles).toBe(0);
		expect(codes(res.data.warnings)).not.toContain("STAGING_NOT_DRAINING");
	});

	it("negative control: a recent pending file does not warn", () => {
		const { vault } = makeTempVault("health-staging-fresh", docsAged(0));
		stageFile(vault.stagingDir, "pending-new.md", 0);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.staging.pendingFiles).toBe(1);
		expect(codes(res.data.warnings)).not.toContain("STAGING_NOT_DRAINING");
	});

	it("reports zero when the staging directory does not exist", () => {
		const { vault } = makeTempVault("health-staging-none", docsAged(0));
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.staging.pendingFiles).toBe(0);
		expect(res.data.staging.oldestAgeDays).toBeNull();
	});
});

describe("healthTool — language breakdown", () => {
	it("groups documents by language when asked", () => {
		const { vault } = makeTempVault("health-lang", docsAged(0));
		const res = healthTool(args({ include_language_breakdown: true }), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.languages).not.toBeNull();
		expect(Object.values(res.data.languages ?? {}).reduce((a, b) => a + b, 0)).toBe(
			3,
		);
	});

	it("skips the extra scan when not asked", () => {
		const { vault } = makeTempVault("health-lang-off", docsAged(0));
		const res = healthTool(args({ include_language_breakdown: false }), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.languages).toBeNull();
		expect(codes(res.data.warnings)).not.toContain("LANGUAGE_UNIFORM");
	});

	/**
	 * The pre-4.0 defect in miniature: the language column existed since
	 * schema 3 but was never written, so all 11,910 rows carried the default.
	 * A uniform label across a large corpus means detection did not run.
	 */
	it("warns when a large corpus carries exactly one language label", () => {
		const { vault } = makeTempVault("health-lang-uniform");
		const many = Array.from({ length: 150 }, (_, i) => ({
			...defaultDocs()[0],
			path: `bulk/doc-${i}.md`,
			mtime: Math.floor(Date.now() / 1000),
		}));
		buildTestDb(vault.fts5Db, many);
		const res = healthTool(args({ include_language_breakdown: true }), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toContain("LANGUAGE_UNIFORM");
	});

	/** NEGATIVE CONTROL: a small corpus is not evidence of a broken detector. */
	it("negative control: a small uniform corpus does not warn", () => {
		const { vault } = makeTempVault("health-lang-small", docsAged(0));
		const res = healthTool(args({ include_language_breakdown: true }), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).not.toContain("LANGUAGE_UNIFORM");
	});

	/** NEGATIVE CONTROL: genuinely mixed languages must stay quiet. */
	it("negative control: a mixed-language corpus does not warn", () => {
		const { vault } = makeTempVault("health-lang-mixed");
		const many = Array.from({ length: 150 }, (_, i) => ({
			...defaultDocs()[0],
			path: `bulk/doc-${i}.md`,
			mtime: Math.floor(Date.now() / 1000),
		}));
		buildTestDb(vault.fts5Db, many);
		const db = new Database(vault.fts5Db);
		try {
			db.prepare(
				"UPDATE documents SET language = 'tr' WHERE id % 2 = 0",
			).run();
		} finally {
			db.close();
		}
		const res = healthTool(args({ include_language_breakdown: true }), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(Object.keys(res.data.languages ?? {}).length).toBeGreaterThan(1);
		expect(codes(res.data.warnings)).not.toContain("LANGUAGE_UNIFORM");
	});
});

describe("healthTool — the remedy contract", () => {
	/**
	 * The load-bearing rule: a detector ships with its remedy. A warning the
	 * reader cannot act on becomes noise, and noise gets read as silence.
	 */
	it("every warning in every scenario carries a non-empty remedy", () => {
		const scenarios: Array<() => ReturnType<typeof healthTool>> = [
			() => healthTool(args(), makeTempVault("rem-missing").vault),
			() => {
				const { vault } = makeTempVault("rem-stale", docsAged(40));
				return healthTool(args(), vault);
			},
			() => {
				const { vault } = makeTempVault("rem-schema", docsAged(0));
				setMeta(vault.fts5Db, "schema_version", "3");
				return healthTool(args(), vault);
			},
			() => {
				const { vault } = makeTempVault("rem-locale", docsAged(0));
				setMeta(vault.fts5Db, "normalization_profile", "nonsense");
				return healthTool(args(), vault);
			},
			() => {
				const { vault } = makeTempVault("rem-staging", docsAged(0));
				stageFile(vault.stagingDir, "old.md", 30);
				return healthTool(args(), vault);
			},
		];
		let seen = 0;
		for (const run of scenarios) {
			const res = run();
			expect(res.ok).toBe(true);
			if (!res.ok) continue;
			for (const w of res.data.warnings) {
				seen += 1;
				expect(w.remedy.length).toBeGreaterThan(0);
				expect(w.detail.length).toBeGreaterThan(0);
				expect(w.code).toMatch(/^[A-Z_]+$/);
			}
		}
		// Guards against the scenarios silently producing nothing to check.
		expect(seen).toBeGreaterThanOrEqual(5);
	});

	it("reports several conditions in one call rather than stopping at the first", () => {
		const { vault } = makeTempVault("rem-multi", docsAged(40));
		setMeta(vault.fts5Db, "schema_version", "3");
		stageFile(vault.stagingDir, "old.md", 30);
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(codes(res.data.warnings)).toEqual(
			expect.arrayContaining([
				"STAGING_NOT_DRAINING",
				"SCHEMA_MISMATCH",
				"INDEX_STALE",
			]),
		);
		expect(res.data.ok).toBe(false);
	});
});

describe("healthTool — failure handling", () => {
	it("returns a structured error rather than throwing on an unreadable index", () => {
		const { vault } = makeTempVault("health-corrupt");
		mkdirSync(join(vault.stateDir), { recursive: true });
		writeFileSync(vault.fts5Db, "this is not a sqlite database", "utf8");
		const res = healthTool(args(), vault);
		expect(res.ok).toBe(false);
		if (res.ok) return;
		expect(res.error.code.length).toBeGreaterThan(0);
	});
});
