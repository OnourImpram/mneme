/**
 * Integration tests for the claude-mem migration.
 *
 * A synthetic SQLite database is materialized that mirrors the
 * relevant claude-mem v13.2.0 schema. The migrator is then run
 * against that fixture and against the actual filesystem. No
 * Python process is spawned; the TS layer is self-contained.
 */

import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	realpathSync,
	renameSync,
	rmSync,
	symlinkSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
	buildObservationMarkdown,
	type MigrationOptions,
	migrate,
	observationContentHash,
	readExistingContentHash,
	redact,
	serializeFrontmatter,
} from "../../src/cli/migrate.js";
import {
	abortMigrationRollback,
	finalizeMigrationRollback,
	prepareMigrationRollback,
	rollbackMigration,
} from "../../src/cli/migration_rollback.js";
import { VaultConfig } from "../../src/vault/config.js";

interface FixtureRow {
	id: number;
	memory_session_id: string;
	project: string;
	text: string;
	type: string;
	title: string;
	subtitle: string;
	facts: string;
	narrative: string;
	concepts: string;
	files_read: string;
	files_modified: string;
	prompt_number: number;
	discovery_tokens: number;
	created_at: string;
	created_at_epoch: number;
	content_hash: string;
	generated_by_model: string;
	agent_type: string;
	agent_id: string;
	metadata: string;
}

const SCHEMA_STATEMENTS: string[] = [
	`CREATE TABLE observations (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     project TEXT,
     text TEXT,
     type TEXT,
     title TEXT,
     subtitle TEXT,
     facts TEXT,
     narrative TEXT,
     concepts TEXT,
     files_read TEXT,
     files_modified TEXT,
     prompt_number INTEGER,
     discovery_tokens INTEGER,
     created_at TEXT,
     created_at_epoch INTEGER,
     content_hash TEXT,
     generated_by_model TEXT,
     agent_type TEXT,
     agent_id TEXT,
     metadata TEXT
   )`,
	`CREATE TABLE session_summaries (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     summary TEXT,
     created_at TEXT
   )`,
	`CREATE TABLE user_prompts (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     prompt_text TEXT,
     created_at TEXT
   )`,
];

function makeFixtureDb(path: string, rows: FixtureRow[]): void {
	const db = new Database(path);
	try {
		for (const stmt of SCHEMA_STATEMENTS) {
			db.prepare(stmt).run();
		}
		const insert = db.prepare(`
      INSERT INTO observations
      (id, memory_session_id, project, text, type, title, subtitle, facts,
       narrative, concepts, files_read, files_modified, prompt_number,
       discovery_tokens, created_at, created_at_epoch, content_hash,
       generated_by_model, agent_type, agent_id, metadata)
      VALUES (@id, @memory_session_id, @project, @text, @type, @title,
              @subtitle, @facts, @narrative, @concepts, @files_read,
              @files_modified, @prompt_number, @discovery_tokens,
              @created_at, @created_at_epoch, @content_hash,
              @generated_by_model, @agent_type, @agent_id, @metadata)
    `);
		const tx = db.transaction((items: FixtureRow[]) => {
			for (const r of items) insert.run(r);
		});
		tx(rows);
		db.prepare(
			"INSERT INTO session_summaries (id, memory_session_id, summary, created_at) VALUES (?, ?, ?, ?)",
		).run(1, "session-A", "First wrap-up", "2026-04-01T08:00:00Z");
		db.prepare(
			"INSERT INTO user_prompts (id, memory_session_id, prompt_text, created_at) VALUES (?, ?, ?, ?)",
		).run(
			1,
			"session-A",
			"How do we wire the indexer?",
			"2026-04-01T08:01:00Z",
		);
	} finally {
		db.close();
	}
}

function sampleRow(overrides: Partial<FixtureRow> = {}): FixtureRow {
	return {
		id: 1,
		memory_session_id: "session-A",
		project: "demo-project",
		text: "Sample observation text body.",
		type: "discovery",
		title: "Sample title",
		subtitle: "Sample subtitle",
		facts: "Fact one. Fact two.",
		narrative: "Narrative content.",
		concepts: "concept-a, concept-b",
		files_read: "[]",
		files_modified: "[]",
		prompt_number: 1,
		discovery_tokens: 42,
		created_at: "2026-04-01T08:00:00Z",
		created_at_epoch: 1_775_376_000,
		content_hash: "synthetic-hash-1",
		generated_by_model: "synthetic-model",
		agent_type: "main",
		agent_id: "agent-1",
		metadata: "{}",
		...overrides,
	};
}

describe("redact", () => {
	it("returns input unchanged when there is no private tag", () => {
		const r = redact("Hello world");
		expect(r.text).toBe("Hello world");
		expect(r.count).toBe(0);
	});

	it("replaces a single private block with REDACTED", () => {
		const r = redact("before <private>secret</private> after");
		expect(r.text).toBe("before [REDACTED] after");
		expect(r.count).toBe(1);
	});

	it("counts multiple substitutions including multi-line blocks", () => {
		const text = "<private>a\nb</private> mid <private>c</private>";
		const r = redact(text);
		expect(r.count).toBe(2);
		expect(r.text).toBe("[REDACTED] mid [REDACTED]");
	});

	it("treats null and empty string as zero-substitution passthrough", () => {
		expect(redact(null).count).toBe(0);
		expect(redact("").text).toBe("");
	});
});

describe("serializeFrontmatter", () => {
	it("emits a deterministic YAML block with proper delimiters", () => {
		const block = serializeFrontmatter(
			[
				["id", "cm-obs-1"],
				["type", "session"],
				["schema_version", 1],
			],
			[["tags", ["alpha", "beta"]]],
		);
		expect(block.startsWith("---\n")).toBe(true);
		expect(block).toContain("id: 'cm-obs-1'");
		expect(block).toContain("type: 'session'");
		expect(block).toContain("schema_version: 1");
		expect(block).toContain("tags:");
		expect(block).toContain("  - 'alpha'");
		expect(block.trimEnd().endsWith("---")).toBe(true);
	});

	it("escapes inner single quotes by doubling", () => {
		const block = serializeFrontmatter([["title", "it's fine"]]);
		expect(block).toContain("title: 'it''s fine'");
	});

	it("omits empty arrays entirely", () => {
		const block = serializeFrontmatter([["id", "cm-obs-2"]], [["tags", []]]);
		expect(block).not.toContain("tags:");
	});
});

describe("observationContentHash", () => {
	it("is stable for identical row payloads", () => {
		const a = observationContentHash(sampleRow() as never);
		const b = observationContentHash(sampleRow() as never);
		expect(a).toBe(b);
		expect(a.length).toBe(64);
	});

	it("changes when narrative changes", () => {
		const a = observationContentHash(sampleRow() as never);
		const b = observationContentHash(
			sampleRow({ narrative: "Different narrative" }) as never,
		);
		expect(a).not.toBe(b);
	});
});

describe("readExistingContentHash", () => {
	it("returns null on missing file", () => {
		expect(readExistingContentHash("/nonexistent/path/file.md")).toBe(null);
	});

	it("extracts the content_hash line from a written file", () => {
		const dir = mkdtempSync(join(tmpdir(), "mneme-frontmatter-"));
		try {
			const path = join(dir, "doc.md");
			writeFileSync(
				path,
				"---\nid: 'cm-obs-1'\ncontent_hash: 'abc123'\n---\n\nbody\n",
				"utf8",
			);
			expect(readExistingContentHash(path)).toBe("abc123");
		} finally {
			rmSync(dir, { recursive: true, force: true });
		}
	});

	it("returns null when the file has no frontmatter", () => {
		const dir = mkdtempSync(join(tmpdir(), "mneme-frontmatter-"));
		try {
			const path = join(dir, "doc.md");
			writeFileSync(path, "no frontmatter here", "utf8");
			expect(readExistingContentHash(path)).toBe(null);
		} finally {
			rmSync(dir, { recursive: true, force: true });
		}
	});
});

describe("buildObservationMarkdown", () => {
	it("uses title when present, falls back to subtitle, then to default", () => {
		const row = sampleRow({ title: "", subtitle: "fallback-sub" });
		const md = buildObservationMarkdown(row as never, {
			title: "",
			subtitle: "fallback-sub",
			narrative: "",
			text: "",
		});
		expect(md.startsWith("# fallback-sub")).toBe(true);
	});

	it("emits all four section headings when content is present", () => {
		const md = buildObservationMarkdown(sampleRow() as never, {
			title: "T",
			subtitle: "S",
			narrative: "N",
			text: "X",
		});
		expect(md).toContain("## Narrative");
		expect(md).toContain("## Facts");
		expect(md).toContain("## Concepts");
		expect(md).toContain("## Text");
	});

	it("skips a section when its content is empty", () => {
		const md = buildObservationMarkdown(sampleRow() as never, {
			title: "T",
			subtitle: "",
			narrative: "",
			text: "X",
		});
		expect(md).not.toContain("## Narrative");
		expect(md).toContain("## Text");
	});
});

describe("migrate (full integration)", () => {
	let workDir: string;
	let vaultDir: string;
	let dbPath: string;
	let vault: VaultConfig;

	beforeEach(() => {
		workDir = mkdtempSync(join(tmpdir(), "mneme-migrate-"));
		vaultDir = join(workDir, "vault");
		dbPath = join(workDir, "claude-mem.db");
		mkdirSync(join(vaultDir, ".mneme"), { recursive: true });
		vault = VaultConfig.fromPath(vaultDir);
		makeFixtureDb(dbPath, [
			sampleRow(),
			sampleRow({
				id: 2,
				title: "Second observation",
				narrative: "Has <private>sensitive</private> content.",
				created_at: "2026-04-02T09:30:00Z",
				created_at_epoch: 1_775_466_600,
			}),
			sampleRow({
				id: 3,
				title: "Third observation",
				subtitle: "",
				narrative: "",
				text: "Body-only content.",
				created_at: "2026-04-02T10:15:00Z",
				created_at_epoch: 1_775_469_300,
			}),
		]);
	});

	afterEach(() => {
		rmSync(workDir, { recursive: true, force: true });
	});

	function baseOpts(over: Partial<MigrationOptions> = {}): MigrationOptions {
		return {
			sourceDb: dbPath,
			vault,
			archive: "preserve",
			...over,
		};
	}

	it("migrates three observations into per-date buckets", () => {
		const stats = migrate(baseOpts());
		expect(stats.status).toBe("ok");
		expect(stats.observations.migrated).toBe(3);
		expect(stats.observations.skippedDedup).toBe(0);
		expect(stats.errors).toEqual([]);
		const bucket1 = join(stats.exportRoot, "2026-04-01");
		const bucket2 = join(stats.exportRoot, "2026-04-02");
		expect(existsSync(join(bucket1, "cm-obs-1.md"))).toBe(true);
		expect(existsSync(join(bucket2, "cm-obs-2.md"))).toBe(true);
		expect(existsSync(join(bucket2, "cm-obs-3.md"))).toBe(true);
	});

	it("strips <private> content and counts the substitutions", () => {
		const stats = migrate(baseOpts());
		expect(stats.redactionsApplied).toBeGreaterThanOrEqual(1);
		const second = readFileSync(
			join(stats.exportRoot, "2026-04-02", "cm-obs-2.md"),
			"utf8",
		);
		expect(second).toContain("[REDACTED]");
		expect(second).not.toContain("<private>");
		expect(second).not.toContain("sensitive");
	});

	it("redacts exported migration metadata and remains rollback-compatible", () => {
		const db = new Database(dbPath);
		try {
			db.prepare(
				`UPDATE observations
				 SET memory_session_id = ?, project = ?, type = ?, generated_by_model = ?,
				     agent_type = ?, metadata = ?, created_at = ?
				 WHERE id = 1`,
			).run(
				"session-<private>OBS_SESSION_SECRET</private>",
				"project-<private>OBS_PROJECT_SECRET</private>",
				"discovery-<private>OBS_TYPE_SECRET</private>",
				"model-<private>OBS_MODEL_SECRET</private>",
				"agent-<private>OBS_AGENT_SECRET</private>",
				'{"private":"<private>OBS_METADATA_SECRET</private>"}',
				"2026-04-01T08:00:00Z <private>OBS_CREATED_SECRET</private>",
			);
			db.prepare(
				`UPDATE session_summaries
				 SET memory_session_id = ?, summary = ?, created_at = ?
				 WHERE id = 1`,
			).run(
				"session-<private>SUMMARY_SESSION_SECRET</private>",
				"Summary <private>SUMMARY_BODY_SECRET</private>",
				"2026-04-01T08:00:00Z <private>SUMMARY_CREATED_SECRET</private>",
			);
			db.prepare(
				`UPDATE user_prompts
				 SET memory_session_id = ?, prompt_text = ?, created_at = ?
				 WHERE id = 1`,
			).run(
				"session-<private>PROMPT_SESSION_SECRET</private>",
				"Prompt <private>PROMPT_BODY_SECRET</private>",
				"2026-04-01T08:01:00Z <private>PROMPT_CREATED_SECRET</private>",
			);
		} finally {
			db.close();
		}

		const stats = migrate(baseOpts());
		expect(stats.status).toBe("ok");
		expect(stats.redactionsApplied).toBeGreaterThanOrEqual(13);

		const observation = readFileSync(
			join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
			"utf8",
		);
		const summary = readFileSync(
			join(stats.exportRoot, "_sessions", "cm-sess-1.md"),
			"utf8",
		);
		const prompt = readFileSync(
			join(stats.exportRoot, "_prompts", "cm-prompt-1.md"),
			"utf8",
		);
		const exported = `${observation}\n${summary}\n${prompt}`;
		for (const secret of [
			"OBS_SESSION_SECRET",
			"OBS_PROJECT_SECRET",
			"OBS_TYPE_SECRET",
			"OBS_MODEL_SECRET",
			"OBS_AGENT_SECRET",
			"OBS_METADATA_SECRET",
			"OBS_CREATED_SECRET",
			"SUMMARY_SESSION_SECRET",
			"SUMMARY_BODY_SECRET",
			"SUMMARY_CREATED_SECRET",
			"PROMPT_SESSION_SECRET",
			"PROMPT_BODY_SECRET",
			"PROMPT_CREATED_SECRET",
		]) {
			expect(exported).not.toContain(secret);
		}
		expect(observation).toContain("session_id: 'session-[REDACTED]'");
		expect(observation).toContain("project: 'project-[REDACTED]'");
		expect(observation).toContain("original_type: 'discovery-[REDACTED]'");
		expect(observation).toContain("original_model: 'model-[REDACTED]'");
		expect(observation).toContain("agent_type: 'agent-[REDACTED]'");
		expect(observation).toContain("**Session**: session-[REDACTED]");
		expect(observation).toContain("**Project**: project-[REDACTED]");
		expect(summary).toContain("created: '2026-04-01T08:00:00Z [REDACTED]'");
		expect(prompt).toContain("created: '2026-04-01T08:01:00Z [REDACTED]'");

		const rollback = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});
		expect(rollback.status).toBe("ok");
		expect(existsSync(stats.exportRoot)).toBe(false);
	});

	it("rerunning the same migration is fully idempotent", () => {
		const first = migrate(baseOpts());
		expect(first.observations.migrated).toBe(3);

		const second = migrate(baseOpts());
		expect(second.observations.migrated).toBe(0);
		expect(second.observations.rewritten).toBe(0);
		expect(second.observations.skippedDedup).toBe(3);
	});

	it("rewrites a record when its content_hash changes", () => {
		const first = migrate(baseOpts());
		expect(first.observations.migrated).toBe(3);

		// Mutate the source row to force a hash drift.
		const db = new Database(dbPath);
		try {
			db.prepare("UPDATE observations SET narrative = ? WHERE id = 1").run(
				"Newly edited narrative.",
			);
		} finally {
			db.close();
		}

		const second = migrate(baseOpts());
		expect(second.observations.skippedDedup).toBe(2);
		expect(second.observations.rewritten).toBe(1);
		expect(second.observations.migrated).toBe(0);
	});

	it("dry-run writes nothing to disk but reports counts", () => {
		const stats = migrate(baseOpts({ dryRun: true }));
		expect(stats.dryRun).toBe(true);
		expect(stats.observations.migrated).toBe(3);
		// The export root must not exist after a dry-run (no file, no folder).
		expect(existsSync(stats.exportRoot)).toBe(false);
		expect(existsSync(join(stats.exportRoot, "_manifest.json"))).toBe(false);
		expect(stats.archive.status).toBe("skipped (dry-run)");
	});

	it("migrates session_summaries and user_prompts heads into their dirs", () => {
		const stats = migrate(baseOpts());
		expect(stats.sessionSummaries.migrated).toBe(1);
		expect(stats.userPromptsHeads.migrated).toBe(1);
		expect(
			existsSync(join(stats.exportRoot, "_sessions", "cm-sess-1.md")),
		).toBe(true);
		expect(
			existsSync(join(stats.exportRoot, "_prompts", "cm-prompt-1.md")),
		).toBe(true);
	});

	it("writes a manifest that records the run", () => {
		const stats = migrate(baseOpts());
		const manifestRaw = readFileSync(
			join(stats.exportRoot, "_manifest.json"),
			"utf8",
		);
		const manifest = JSON.parse(manifestRaw) as {
			schema: string;
			observations: { migrated: number };
			archive: { mode: string; status: string };
		};
		expect(manifest.schema).toBe("mneme-migration-manifest/1");
		expect(manifest.observations.migrated).toBe(3);
		expect(manifest.archive.mode).toBe("preserve");
		expect(manifest.archive.status).toBe("preserved");
		expect(manifestRaw).not.toContain(vault.root);
		expect(manifestRaw).not.toContain(dbPath);
		expect(stats.rollbackManifestPath).toBeDefined();
		expect(manifestRaw).not.toContain(
			stats.rollbackManifestPath ?? "__missing_rollback_manifest__",
		);
	});

	it("rolls back a migration to an absent export root and is idempotent", () => {
		const stats = migrate(baseOpts());
		expect(stats.rollbackManifestPath).toBeDefined();

		const rollback = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});

		expect(rollback.status).toBe("ok");
		expect(rollback.restoredFiles).toBe(0);
		expect(existsSync(stats.exportRoot)).toBe(false);

		const second = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});
		expect(second.status).toBe("ok");
		expect(second.alreadyRolledBack).toBe(true);
	});

	it("restores the exact pre-migration export tree", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(
			join(exportRoot, "operator-note.txt"),
			"preserve me\n",
			"utf8",
		);
		const stats = migrate(baseOpts());

		const rollback = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});

		expect(rollback.status).toBe("ok");
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"preserve me\n",
		);
		expect(existsSync(join(exportRoot, "_manifest.json"))).toBe(false);
		expect(existsSync(join(exportRoot, "2026-04-01", "cm-obs-1.md"))).toBe(
			false,
		);
	});

	it("refuses rollback after a migrated file changes", () => {
		const stats = migrate(baseOpts());
		const target = join(stats.exportRoot, "2026-04-01", "cm-obs-1.md");
		writeFileSync(
			target,
			`${readFileSync(target, "utf8")}operator edit\n`,
			"utf8",
		);

		const rollback = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});

		expect(rollback.status).toBe("error");
		expect(rollback.errors.join(" ")).toContain("changed after the run");
		expect(readFileSync(target, "utf8")).toContain("operator edit");
	});

	it("rejects a structurally valid manifest whose HMAC was tampered", () => {
		const stats = migrate(baseOpts());
		const manifestPath = stats.rollbackManifestPath ?? "";
		const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Record<
			string,
			unknown
		>;
		manifest.prepared_at = "2099-01-01T00:00:00.000Z";
		writeFileSync(
			manifestPath,
			`${JSON.stringify(manifest, null, 2)}\n`,
			"utf8",
		);

		const rollback = rollbackMigration({ vault, manifestPath });

		expect(rollback.status).toBe("error");
		expect(rollback.errors.join(" ")).toContain("integrity HMAC mismatch");
		expect(existsSync(stats.exportRoot)).toBe(true);
	});

	it("recovers a crash while a migration is still staged", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		writeFileSync(join(handle.stagingRoot, "partial.md"), "partial\n", "utf8");

		const rollback = rollbackMigration({
			vault,
			manifestPath: handle.manifestPath,
		});

		expect(rollback.status).toBe("ok");
		expect(rollback.recoveredInterruptedRun).toBe(true);
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"original\n",
		);
		expect(existsSync(join(exportRoot, "partial.md"))).toBe(false);
	});

	it("keeps abort idempotent and refuses finalize after abort", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);

		abortMigrationRollback(
			vault,
			handle,
			`fixture <private>ABORT_SECRET</private> ${dbPath} ${join(workDir, "unlisted-secret.txt")} https://user:pass@example.test/repo`,
		);
		const manifestRaw = readFileSync(handle.manifestPath, "utf8");
		const manifest = JSON.parse(manifestRaw) as { abort_reason: string };
		expect(manifest.abort_reason).toContain("fixture [REDACTED]");
		expect(manifest.abort_reason).toContain("[PATH]");
		expect(manifest.abort_reason).toContain(
			"https://[REDACTED]@example.test/repo",
		);
		expect(manifestRaw).not.toContain("ABORT_SECRET");
		expect(manifest.abort_reason).not.toContain(dbPath);
		expect(manifest.abort_reason).not.toContain("unlisted-secret.txt");
		expect(manifestRaw).not.toContain("user:pass");
		expect(() =>
			abortMigrationRollback(vault, handle, "repeated abort"),
		).not.toThrow();
		expect(() =>
			finalizeMigrationRollback(vault, handle, { sourceMoved: false }),
		).toThrow("not in prepared state");
	});

	it("refuses abort when the live tree changed after staging", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		writeFileSync(join(exportRoot, "operator-note.txt"), "changed\n", "utf8");

		expect(() => abortMigrationRollback(vault, handle, "must fail")).toThrow(
			"original tree changed",
		);
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"changed\n",
		);
	});

	it("rejects archive intent that disagrees with the prepared manifest", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		const preserve = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		expect(() =>
			finalizeMigrationRollback(vault, preserve, { sourceMoved: true }),
		).toThrow("move intent");
		expect(() =>
			finalizeMigrationRollback(vault, preserve, {
				sourceMoved: false,
				archivePath: join(exportRoot, "_archive", "claude-mem.db"),
			}),
		).toThrow("archive path");

		const copy = prepareMigrationRollback(vault, exportRoot, dbPath, "copy");
		expect(() =>
			finalizeMigrationRollback(vault, copy, { sourceMoved: false }),
		).toThrow("archive evidence");
	});

	it("recovers a crash after quarantining the live tree", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		writeFileSync(join(handle.stagingRoot, "new.md"), "new\n", "utf8");
		expect(() =>
			finalizeMigrationRollback(vault, handle, {
				sourceMoved: false,
				faultAt: "after-live-quarantine",
			}),
		).toThrow("simulated process crash");

		const rollback = rollbackMigration({
			vault,
			manifestPath: handle.manifestPath,
		});

		expect(rollback.status).toBe("ok");
		expect(rollback.recoveredInterruptedRun).toBe(true);
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"original\n",
		);
		expect(existsSync(join(exportRoot, "new.md"))).toBe(false);
	});

	it("recovers a crash after publishing the staged tree", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		writeFileSync(join(handle.stagingRoot, "new.md"), "new\n", "utf8");
		expect(() =>
			finalizeMigrationRollback(vault, handle, {
				sourceMoved: false,
				faultAt: "after-stage-publish",
			}),
		).toThrow("simulated process crash");

		const rollback = rollbackMigration({
			vault,
			manifestPath: handle.manifestPath,
		});

		expect(rollback.status).toBe("ok");
		expect(rollback.recoveredInterruptedRun).toBe(true);
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"original\n",
		);
		expect(existsSync(join(exportRoot, "new.md"))).toBe(false);
	});

	it("restores a moved source after a crash before the live-tree swap", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		const handle = prepareMigrationRollback(vault, exportRoot, dbPath, "move");
		const stagedArchive = join(handle.stagingRoot, "_archive", "claude-mem.db");
		mkdirSync(join(handle.stagingRoot, "_archive"), { recursive: true });
		writeFileSync(stagedArchive, readFileSync(dbPath));
		expect(() =>
			finalizeMigrationRollback(vault, handle, {
				sourceMoved: true,
				archivePath: join(exportRoot, "_archive", "claude-mem.db"),
				faultAt: "after-source-quarantine",
			}),
		).toThrow("simulated process crash");
		expect(existsSync(dbPath)).toBe(false);

		const rollback = rollbackMigration({
			vault,
			manifestPath: handle.manifestPath,
		});

		expect(rollback.status).toBe("ok");
		expect(rollback.recoveredInterruptedRun).toBe(true);
		expect(existsSync(dbPath)).toBe(true);
		expect(existsSync(exportRoot)).toBe(false);
	});

	it("canonicalizes a stable parent alias before preparing a source move", () => {
		const sourceRoot = join(workDir, "canonical-source");
		const aliasRoot = join(workDir, "source-alias");
		mkdirSync(sourceRoot, { recursive: true });
		const source = join(sourceRoot, "claude-mem.db");
		makeFixtureDb(source, [sampleRow()]);
		symlinkSync(
			sourceRoot,
			aliasRoot,
			process.platform === "win32" ? "junction" : "dir",
		);

		const handle = prepareMigrationRollback(
			vault,
			join(vault.root, "imported", "claude-mem"),
			join(aliasRoot, "claude-mem.db"),
			"move",
		);
		const manifest = JSON.parse(readFileSync(handle.manifestPath, "utf8")) as {
			source_db: string;
		};

		expect(manifest.source_db).toBe(realpathSync(source));
	});

	it.skipIf(process.platform === "win32")(
		"rejects a source DB that is itself a symlink",
		() => {
			const sourceLink = join(workDir, "source-link.db");
			symlinkSync(dbPath, sourceLink, "file");

			expect(() =>
				prepareMigrationRollback(
					vault,
					join(vault.root, "imported", "claude-mem"),
					sourceLink,
					"move",
				),
			).toThrow("non-symlink regular file");
		},
	);

	it("refuses move when the staged archive does not match the source", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		const handle = prepareMigrationRollback(vault, exportRoot, dbPath, "move");
		mkdirSync(join(handle.stagingRoot, "_archive"), { recursive: true });
		writeFileSync(
			join(handle.stagingRoot, "_archive", "claude-mem.db"),
			"corrupt archive",
			"utf8",
		);

		expect(() =>
			finalizeMigrationRollback(vault, handle, {
				sourceMoved: true,
				archivePath: join(exportRoot, "_archive", "claude-mem.db"),
			}),
		).toThrow("staged archive hash");
		expect(existsSync(dbPath)).toBe(true);
		expect(existsSync(exportRoot)).toBe(false);
	});

	it("restores a live tree changed during the commit swap", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const handle = prepareMigrationRollback(
			vault,
			exportRoot,
			dbPath,
			"preserve",
		);
		writeFileSync(join(handle.stagingRoot, "new.md"), "new\n", "utf8");
		const manifest = JSON.parse(readFileSync(handle.manifestPath, "utf8")) as {
			quarantine_root: string;
		};

		expect(() =>
			finalizeMigrationRollback(vault, handle, {
				sourceMoved: false,
				onAfterLiveQuarantineForTest: () => {
					writeFileSync(
						join(manifest.quarantine_root, "late-write.txt"),
						"late\n",
						"utf8",
					);
				},
			}),
		).toThrow("quarantined-tree race");
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"original\n",
		);
		expect(readFileSync(join(exportRoot, "late-write.txt"), "utf8")).toBe(
			"late\n",
		);
		expect(existsSync(join(exportRoot, "new.md"))).toBe(false);
	});

	it("resumes rollback after a crash between the two tree renames", () => {
		const exportRoot = join(vault.root, "imported", "claude-mem");
		mkdirSync(exportRoot, { recursive: true });
		writeFileSync(join(exportRoot, "operator-note.txt"), "original\n", "utf8");
		const stats = migrate(baseOpts());

		const interrupted = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
			faultAt: "after-rollback-live-quarantine",
		});
		expect(interrupted.status).toBe("error");
		expect(interrupted.errors.join(" ")).toContain("simulated process crash");

		const resumed = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});
		expect(resumed.status).toBe("ok");
		expect(readFileSync(join(exportRoot, "operator-note.txt"), "utf8")).toBe(
			"original\n",
		);
	});

	it("rejects an export root replaced by an external symlink or junction", () => {
		const stats = migrate(baseOpts());
		const saved = join(workDir, "saved-export");
		const outside = join(workDir, "outside-export");
		mkdirSync(outside, { recursive: true });
		renameSync(stats.exportRoot, saved);
		symlinkSync(
			outside,
			stats.exportRoot,
			process.platform === "win32" ? "junction" : "dir",
		);

		const rollback = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});

		expect(rollback.status).toBe("error");
		expect(existsSync(saved)).toBe(true);
		expect(existsSync(outside)).toBe(true);
	});

	it("recovers an operation lock left by a dead process", () => {
		const lockPath = join(
			vault.root,
			".mneme",
			"migrations",
			".operation.lock",
		);
		mkdirSync(join(vault.root, ".mneme", "migrations"), { recursive: true });
		writeFileSync(
			lockPath,
			JSON.stringify({ pid: 2_147_483_647, nonce: "dead" }),
			"utf8",
		);

		const stats = migrate(baseOpts());

		expect(stats.status).toBe("ok");
		expect(existsSync(lockPath)).toBe(false);
	});

	it("archive=copy snapshots the DB and leaves the source intact", () => {
		const stats = migrate(baseOpts({ archive: "copy" }));
		expect(stats.archive.mode).toBe("copy");
		expect(stats.archive.status).toBe("copied");
		expect(
			existsSync(join(stats.exportRoot, "_archive", "claude-mem.db")),
		).toBe(true);
		expect(existsSync(dbPath)).toBe(true);
	});

	it("archive=copy captures committed WAL state in a valid standalone DB", () => {
		rmSync(dbPath, { force: true });
		const writer = new Database(dbPath);
		try {
			writer.pragma("journal_mode = WAL");
			writer.pragma("wal_autocheckpoint = 0");
			for (const stmt of SCHEMA_STATEMENTS) writer.prepare(stmt).run();
			writer
				.prepare(
					`INSERT INTO observations
				 (id, text, type, title, created_at, created_at_epoch)
				 VALUES (1, 'wal body', 'observation', 'WAL row',
				 '2026-04-01T00:00:00Z', 1775001600)`,
				)
				.run();

			const stats = migrate(baseOpts({ archive: "copy" }));
			expect(stats.status).toBe("ok");
			const archivePath = join(stats.exportRoot, "_archive", "claude-mem.db");
			const archived = new Database(archivePath, {
				readonly: true,
				fileMustExist: true,
			});
			try {
				expect(archived.pragma("integrity_check", { simple: true })).toBe("ok");
				expect(
					archived.prepare("SELECT count(*) AS n FROM observations").get(),
				).toEqual({ n: 1 });
			} finally {
				archived.close();
			}
		} finally {
			writer.close();
		}
	});

	it("archive=move without confirmDelete refuses to run", () => {
		const stats = migrate(baseOpts({ archive: "move" }));
		expect(stats.status).toBe("error");
		expect(stats.errors.some((e) => e.includes("confirmDelete"))).toBe(true);
		expect(existsSync(dbPath)).toBe(true);
	});

	it("archive=move with confirmDelete copies then deletes the source", () => {
		const stats = migrate(baseOpts({ archive: "move", confirmDelete: true }));
		expect(stats.archive.status).toBe("moved");
		expect(
			existsSync(join(stats.exportRoot, "_archive", "claude-mem.db")),
		).toBe(true);
		expect(existsSync(dbPath)).toBe(false);
	});

	it("archive=move refuses an active WAL database and preserves the source", () => {
		const writer = new Database(dbPath);
		try {
			writer.pragma("journal_mode = WAL");
			writer.pragma("wal_autocheckpoint = 0");
			writer
				.prepare("UPDATE observations SET title = ? WHERE id = 1")
				.run("WAL");

			const stats = migrate(baseOpts({ archive: "move", confirmDelete: true }));
			expect(stats.status).toBe("error");
			expect(stats.archive.status).toContain("copy_failed");
			expect(stats.errors.join(" ")).toContain("offline SQLite database");
			expect(existsSync(dbPath)).toBe(true);
		} finally {
			writer.close();
		}
	});

	it("requires explicit consent before restoring a moved source", () => {
		const stats = migrate(baseOpts({ archive: "move", confirmDelete: true }));
		expect(existsSync(dbPath)).toBe(false);

		const refused = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
		});
		expect(refused.status).toBe("error");
		expect(refused.errors.join(" ")).toContain("confirm-source-restore");
		expect(existsSync(dbPath)).toBe(false);

		const wrongPath = join(workDir, "wrong-source.db");
		const mismatched = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
			confirmSourceRestore: true,
			sourceRestorePath: wrongPath,
		});
		expect(mismatched.status).toBe("error");
		expect(mismatched.errors.join(" ")).toContain("does not match");
		expect(existsSync(wrongPath)).toBe(false);

		writeFileSync(dbPath, "operator replacement", "utf8");
		const noClobber = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
			confirmSourceRestore: true,
			sourceRestorePath: dbPath,
		});
		expect(noClobber.status).toBe("error");
		expect(readFileSync(dbPath, "utf8")).toBe("operator replacement");
		unlinkSync(dbPath);

		const restored = rollbackMigration({
			vault,
			manifestPath: stats.rollbackManifestPath ?? "",
			confirmSourceRestore: true,
			sourceRestorePath: dbPath,
		});
		expect(restored.status).toBe("ok");
		expect(restored.sourceRestored).toBe(true);
		expect(existsSync(dbPath)).toBe(true);
		expect(existsSync(stats.exportRoot)).toBe(false);
	});

	it("reports an error when the source DB is missing", () => {
		rmSync(dbPath, { force: true });
		const stats = migrate(baseOpts());
		expect(stats.status).toBe("error");
		expect(stats.errors[0]).toContain("Source DB not found");
	});

	it("frontmatter on emitted observation files carries the canonical source tag", () => {
		const stats = migrate(baseOpts());
		const file = readFileSync(
			join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
			"utf8",
		);
		expect(file).toContain("source: 'claude-mem-v13.2.0'");
		expect(file).toContain("type: 'observation'");
		expect(file).toMatch(/content_hash: '[0-9a-f]{64}'/);
	});

	it("writes the canonical type frontmatter for all three record kinds (Phase J Day 1 regression)", () => {
		const stats = migrate(baseOpts());
		expect(stats.status).toBe("ok");

		const obsFile = readFileSync(
			join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
			"utf8",
		);
		expect(obsFile).toContain("type: 'observation'");

		const sessFile = readFileSync(
			join(stats.exportRoot, "_sessions", "cm-sess-1.md"),
			"utf8",
		);
		expect(sessFile).toContain("type: 'session_summary'");

		const promptFile = readFileSync(
			join(stats.exportRoot, "_prompts", "cm-prompt-1.md"),
			"utf8",
		);
		expect(promptFile).toContain("type: 'user_prompt'");
	});
});

describe("regression: code-review fixes", () => {
	function tmpWork(): { workDir: string; dbPath: string; vault: VaultConfig } {
		const workDir = mkdtempSync(join(tmpdir(), "mneme-rev-"));
		const vaultDir = join(workDir, "vault");
		mkdirSync(join(vaultDir, ".mneme"), { recursive: true });
		return {
			workDir,
			dbPath: join(workDir, "claude-mem.db"),
			vault: VaultConfig.fromPath(vaultDir),
		};
	}

	const OBS_DDL =
		"CREATE TABLE observations (id INTEGER PRIMARY KEY, memory_session_id TEXT," +
		" project TEXT, text TEXT, type TEXT, title TEXT, subtitle TEXT, facts TEXT," +
		" narrative TEXT, concepts TEXT, files_read TEXT, files_modified TEXT," +
		" prompt_number INTEGER, discovery_tokens INTEGER, created_at TEXT," +
		" created_at_epoch INTEGER, content_hash TEXT, generated_by_model TEXT," +
		" agent_type TEXT, agent_id TEXT, metadata TEXT)";

	it("archive=move with confirmDelete but table errors preserves the source DB", () => {
		// A source DB missing the observations table forces a migration error.
		// The destructive move must refuse to delete the source so the operator
		// can re-run after the underlying failure is understood.
		const { workDir, dbPath, vault } = tmpWork();
		try {
			const db = new Database(dbPath);
			db.prepare(
				"CREATE TABLE session_summaries (id INTEGER PRIMARY KEY, summary TEXT, created_at TEXT)",
			).run();
			db.prepare(
				"INSERT INTO session_summaries (id, summary, created_at) VALUES (1, 'x', '2026-04-01T00:00:00Z')",
			).run();
			db.close();

			const stats = migrate({
				sourceDb: dbPath,
				vault,
				archive: "move",
				confirmDelete: true,
			});
			expect(stats.status).toBe("error");
			expect(existsSync(dbPath)).toBe(true);
			expect(stats.archive.status).toContain("refused");
		} finally {
			rmSync(workDir, { recursive: true, force: true });
		}
	});

	it("redacts <private> blocks in observation facts and concepts", () => {
		const { workDir, dbPath, vault } = tmpWork();
		try {
			const db = new Database(dbPath);
			db.prepare(OBS_DDL).run();
			db.prepare(
				"INSERT INTO observations (id, text, type, title, facts, concepts," +
					" created_at, created_at_epoch) VALUES (1, 'body', 'discovery', 'T'," +
					" 'fact <private>SECRETFACT</private> end'," +
					" '<private>SECRETCONCEPT</private>', '2026-04-01T00:00:00Z', 1775376000)",
			).run();
			db.close();

			const stats = migrate({ sourceDb: dbPath, vault, archive: "preserve" });
			const file = readFileSync(
				join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
				"utf8",
			);
			expect(file).not.toContain("SECRETFACT");
			expect(file).not.toContain("SECRETCONCEPT");
			expect(file).not.toContain("<private>");
			expect(file).toContain("[REDACTED]");
		} finally {
			rmSync(workDir, { recursive: true, force: true });
		}
	});

	it("redacts a <private> block straddling the user-prompt truncation boundary", () => {
		const { workDir, dbPath, vault } = tmpWork();
		try {
			const db = new Database(dbPath);
			db.prepare(
				"CREATE TABLE user_prompts (id INTEGER PRIMARY KEY, prompt_text TEXT, created_at TEXT)",
			).run();
			// Opening tag before char 200, closing tag after: a truncate-then-redact
			// order would cut the closing tag off and leak the visible head.
			const prompt =
				"x".repeat(180) +
				"<private>" +
				"S".repeat(60) +
				"</private>" +
				"y".repeat(40);
			db.prepare(
				"INSERT INTO user_prompts (id, prompt_text, created_at) VALUES (1, ?, '2026-04-01T00:00:00Z')",
			).run(prompt);
			db.close();

			const stats = migrate({ sourceDb: dbPath, vault, archive: "preserve" });
			const file = readFileSync(
				join(stats.exportRoot, "_prompts", "cm-prompt-1.md"),
				"utf8",
			);
			expect(file).not.toContain("<private>");
			expect(file).not.toContain("SSSSSS");
		} finally {
			rmSync(workDir, { recursive: true, force: true });
		}
	});
});
