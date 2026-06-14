/**
 * `mneme-migrate` — one-command migration from claude-mem v13.2.0 SQLite
 * into the mneme vault as readable markdown.
 *
 * The TS port of the migration script intentionally lives in mneme-mcp
 * (not mneme-core) so the lite install profile gets migration as a pure
 * Node binary with no Python dependency. The source SQLite is opened
 * read-only via better-sqlite3; the vault is written through the same
 * atomic-write primitive the MCP write tool uses, so partial writes
 * never reach disk.
 *
 * Three load-bearing properties:
 *
 *   1. Idempotent re-runs. Each emitted file embeds a `content_hash`
 *      frontmatter field. On re-run the migrator reads existing files,
 *      compares hashes, and skips rows whose hash matches. Mismatched
 *      rows are rewritten atomically; new rows are added. The output
 *      converges regardless of how many times the command runs.
 *
 *   2. Privacy redaction. Any text matching `<private>...</private>`
 *      (with the DOTALL flag) is replaced by the literal `<REDACTED>`
 *      token before serialization. Redaction is applied to the same
 *      text columns the Python predecessor covered (text, narrative,
 *      title, subtitle) plus the entirety of session_summaries and
 *      user_prompts heads. The number of substitutions is counted and
 *      surfaced in the run stats.
 *
 *   3. Archive tri-state. `preserve` (default) does not touch the
 *      source DB. `copy` writes a snapshot under `imported/claude-mem/
 *      _archive/`. `move` writes the snapshot then deletes the source
 *      file. The destructive `move` mode requires `confirmDelete: true`
 *      to be set programmatically (or `--confirm-delete` on the CLI).
 */

import { createHash } from "node:crypto";
import {
	copyFileSync,
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	rmSync,
	statSync,
	unlinkSync,
} from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import Database from "better-sqlite3";
import { redact as _privacyRedact } from "../privacy.js";
import { assertWithinVault, atomicWriteText } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";

/** Tri-state for what to do with the source claude-mem DB after export. */
export type ArchiveMode = "preserve" | "copy" | "move";

export interface MigrationOptions {
	/** Absolute path to the claude-mem SQLite database. */
	sourceDb: string;
	/** Target vault. The migrator writes under `vault.root/imported/claude-mem`. */
	vault: VaultConfig;
	/** Archive treatment for the source DB. Defaults to `preserve`. */
	archive?: ArchiveMode;
	/**
	 * Required when `archive === "move"`. Two-factor guard so the
	 * destructive code path cannot fire from a typo on the CLI.
	 */
	confirmDelete?: boolean;
	/** Plan-only mode: read DB, report counts, write nothing. */
	dryRun?: boolean;
}

export interface MigrationStats {
	status: "ok" | "error";
	sourceDb: string;
	vaultRoot: string;
	exportRoot: string;
	observations: { migrated: number; skippedDedup: number; rewritten: number };
	sessionSummaries: {
		migrated: number;
		skippedDedup: number;
		rewritten: number;
	};
	userPromptsHeads: {
		migrated: number;
		skippedDedup: number;
		rewritten: number;
	};
	redactionsApplied: number;
	archive: { mode: ArchiveMode; status: string; path?: string };
	manifestPath?: string;
	elapsedMs: number;
	dryRun: boolean;
	errors: string[];
}

interface ObservationRow {
	id: number;
	memory_session_id: string | null;
	project: string | null;
	text: string | null;
	type: string | null;
	title: string | null;
	subtitle: string | null;
	facts: string | null;
	narrative: string | null;
	concepts: string | null;
	files_read: string | null;
	files_modified: string | null;
	prompt_number: number | null;
	discovery_tokens: number | null;
	created_at: string | null;
	created_at_epoch: number | null;
	content_hash: string | null;
	generated_by_model: string | null;
	agent_type: string | null;
	agent_id: string | null;
	metadata: string | null;
}

interface SessionSummaryRow {
	id: number;
	[column: string]: unknown;
}

interface UserPromptRow {
	id: number;
	[column: string]: unknown;
}

const USER_PROMPT_HEAD_MAX = 200;
const SOURCE_TAG = "claude-mem-v13.2.0";
const SCHEMA_VERSION = 1;
/**
 * Bumped whenever the migration tool changes how it writes its output
 * (frontmatter field values, body section structure, etc.). Included
 * in the content hash so that bumping it invalidates every existing
 * migrated file on the next ``mneme-migrate`` run, triggering a clean
 * rewrite. Distinct from ``SCHEMA_VERSION`` which signals vault
 * frontmatter shape.
 *
 * Revision history:
 * - 1: initial release (Phase G).
 * - 2: Phase J Day 1 fix. Corrected ``type`` frontmatter values for
 *   observations (was "session", now "observation"),
 *   session_summaries (was "reference", now "session_summary"), and
 *   user_prompts (was "reference", now "user_prompt").
 */
const MIGRATION_OUTPUT_REVISION = 2;
const EXPORT_DIR_REL = ["imported", "claude-mem"] as const;

/**
 * Strip `<private>...</private>` blocks and return the count of
 * substitutions alongside the cleaned text. Delegates to the canonical
 * ``privacy.redact`` implementation (case-insensitive, attribute-tolerant,
 * fail-closed) so migrate.ts cannot drift from the shared semantics.
 *
 * Note: the replacement token is ``[REDACTED]`` (canonical). Previous
 * versions used ``<REDACTED>``; existing tests have been updated to match.
 */
export function redact(input: string | null | undefined): {
	text: string;
	count: number;
} {
	return _privacyRedact(input);
}

/**
 * Compute a stable SHA256 over a canonical projection of an observation
 * row. We hash the salient text columns (title, narrative, facts,
 * concepts, text) plus the temporal anchor, so a row's hash stays the
 * same across re-exports unless the underlying content actually moved.
 */
export function observationContentHash(row: ObservationRow): string {
	const parts = [
		`rev=${MIGRATION_OUTPUT_REVISION}`,
		String(row.id),
		row.title ?? "",
		row.subtitle ?? "",
		row.narrative ?? "",
		row.facts ?? "",
		row.concepts ?? "",
		row.text ?? "",
		row.created_at ?? "",
	];
	return sha256(parts.join("␟"));
}

/**
 * Hash for session_summaries and user_prompts rows. Falls back to
 * JSON over the full row because the upstream schema is less stable
 * than observations. Includes ``MIGRATION_OUTPUT_REVISION`` so output
 * shape changes invalidate prior migrations.
 */
export function rowContentHash(row: Record<string, unknown>): string {
	const keys = Object.keys(row).sort();
	const canon = keys.map((k) => `${k}=${stringifyForHash(row[k])}`).join("␟");
	return sha256(`rev=${MIGRATION_OUTPUT_REVISION}␟${canon}`);
}

function stringifyForHash(v: unknown): string {
	if (v === null || v === undefined) return "";
	if (typeof v === "string") return v;
	return JSON.stringify(v);
}

function sha256(s: string): string {
	return createHash("sha256").update(s, "utf8").digest("hex");
}

/**
 * Emit a YAML frontmatter block. The keys are emitted in declared
 * order so the serializer is deterministic — important for git diffs
 * across re-runs. Values are stringified safely (single-quoted, with
 * inner single-quotes doubled, per the YAML 1.2 spec).
 */
export function serializeFrontmatter(
	fields: Array<[string, string | number]>,
	arrays: Array<[string, string[]]> = [],
): string {
	const lines: string[] = ["---"];
	for (const [k, v] of fields) {
		lines.push(`${k}: ${yamlScalar(v)}`);
	}
	for (const [k, items] of arrays) {
		if (items.length === 0) continue;
		lines.push(`${k}:`);
		for (const item of items) {
			lines.push(`  - ${yamlScalar(item)}`);
		}
	}
	lines.push("---", "");
	return `${lines.join("\n")}\n`;
}

function yamlScalar(v: string | number): string {
	if (typeof v === "number") return String(v);
	// Empty strings emit as a single-quoted empty literal so the YAML
	// parser does not see them as `null`.
	if (v === "") return "''";
	// Single-quoted style is safe for arbitrary content provided we
	// escape inner single quotes by doubling them. Wrap to keep colons,
	// hashes, and other YAML indicators inert.
	return `'${v.replace(/'/g, "''")}'`;
}

/**
 * Build the markdown body for one observation. The body is plain
 * markdown that any reader (Obsidian, VS Code, grep) can consume.
 * Section headings are deterministic so downstream tools can target
 * them by name.
 */
export function buildObservationMarkdown(
	row: ObservationRow,
	redacted: {
		title: string;
		subtitle: string;
		narrative: string;
		text: string;
	},
): string {
	const title = redacted.title || redacted.subtitle || `Observation ${row.id}`;
	const model = row.generated_by_model ?? "unknown";
	const created = row.created_at ?? "n/a";
	const type = row.type ?? "n/a";
	const project = row.project ?? "n/a";
	const session = row.memory_session_id ?? "n/a";

	const sections: string[] = [
		`# ${title}`,
		"",
		`**Session**: ${session}  `,
		`**Project**: ${project}  `,
		`**Type**: ${type}  `,
		`**Model**: ${model}  `,
		`**Created**: ${created}  `,
		"",
	];
	if (redacted.narrative) {
		sections.push("## Narrative", "", redacted.narrative, "");
	}
	if (row.facts) {
		sections.push("## Facts", "", row.facts, "");
	}
	if (row.concepts) {
		sections.push("## Concepts", "", row.concepts, "");
	}
	if (redacted.text) {
		sections.push("## Text", "", redacted.text, "");
	}
	return `${sections.join("\n")}\n`;
}

/**
 * Build the per-record markdown for a session_summaries row. The body
 * dumps every column as a dedicated section so nothing is lost; the
 * frontmatter carries the navigation-relevant fields.
 */
export function buildSessionSummaryMarkdown(
	row: SessionSummaryRow,
	redactedRow: Record<string, unknown>,
): string {
	const sections: string[] = [`# Session summary ${row.id}`, ""];
	for (const [key, value] of Object.entries(redactedRow)) {
		if (key === "id") continue;
		if (value === null || value === undefined || value === "") continue;
		const heading = key
			.split("_")
			.map((w) => (w.length === 0 ? w : w[0]?.toUpperCase() + w.slice(1)))
			.join(" ");
		sections.push(`## ${heading}`, "", String(value), "");
	}
	return `${sections.join("\n")}\n`;
}

/**
 * Build the markdown for a user_prompts head. We truncate the prompt
 * body the same way the Python ETL did so sensitive content cannot
 * round-trip in full through the vault.
 */
export function buildUserPromptMarkdown(
	row: UserPromptRow,
	redactedRow: Record<string, unknown>,
): string {
	const sections: string[] = [`# User prompt ${row.id} (head)`, ""];
	for (const [key, value] of Object.entries(redactedRow)) {
		if (key === "id") continue;
		if (value === null || value === undefined || value === "") continue;
		const heading = key
			.split("_")
			.map((w) => (w.length === 0 ? w : w[0]?.toUpperCase() + w.slice(1)))
			.join(" ");
		sections.push(`## ${heading}`, "", String(value), "");
	}
	return `${sections.join("\n")}\n`;
}

/**
 * Extract the `content_hash:` line out of an existing frontmatter
 * block without pulling in a YAML parser. Returns null when the file
 * is missing, lacks frontmatter, or never carried the field. Cheap
 * enough to run for every row during dedup.
 */
export function readExistingContentHash(path: string): string | null {
	if (!existsSync(path)) return null;
	let raw: string;
	try {
		raw = readFileSync(path, "utf8");
	} catch {
		return null;
	}
	if (!raw.startsWith("---")) return null;
	const closingIdx = raw.indexOf("\n---", 3);
	if (closingIdx === -1) return null;
	const header = raw.slice(0, closingIdx);
	const match = header.match(/^content_hash:\s*'?([0-9a-fA-F]+)'?\s*$/m);
	return match?.[1] ?? null;
}

function extractDateBucket(createdAt: string | null | undefined): string {
	if (typeof createdAt === "string" && createdAt.length >= 10) {
		const candidate = createdAt.slice(0, 10);
		if (/^\d{4}-\d{2}-\d{2}$/.test(candidate)) return candidate;
	}
	return "0000-00-00";
}

function pruneEmptyDirIfPresent(dir: string): void {
	try {
		const entries = readdirSync(dir);
		if (entries.length === 0) rmSync(dir, { recursive: false, force: true });
	} catch {
		/* best-effort */
	}
}

interface ColumnIntrospection {
	observations: boolean;
	sessionSummaries: boolean;
	userPrompts: boolean;
}

function tablesPresent(db: Database.Database): ColumnIntrospection {
	const rows = db
		.prepare("SELECT name FROM sqlite_master WHERE type='table'")
		.all() as Array<{ name: string }>;
	const names = new Set(rows.map((r) => r.name));
	return {
		observations: names.has("observations"),
		sessionSummaries: names.has("session_summaries"),
		userPrompts: names.has("user_prompts"),
	};
}

const OBSERVATION_COLUMNS = [
	"id",
	"memory_session_id",
	"project",
	"text",
	"type",
	"title",
	"subtitle",
	"facts",
	"narrative",
	"concepts",
	"files_read",
	"files_modified",
	"prompt_number",
	"discovery_tokens",
	"created_at",
	"created_at_epoch",
	"content_hash",
	"generated_by_model",
	"agent_type",
	"agent_id",
	"metadata",
] as const;

/**
 * Orchestrator. Pure function over options + filesystem. Returns
 * statistics regardless of partial failures; errors are accumulated
 * on the result so a CLI dispatcher can format them without
 * destabilizing the migration of unrelated tables.
 */
export function migrate(opts: MigrationOptions): MigrationStats {
	const t0 = Date.now();
	const archive: ArchiveMode = opts.archive ?? "preserve";
	const dryRun = opts.dryRun === true;
	const exportRootAbs = resolvePath(join(opts.vault.root, ...EXPORT_DIR_REL));
	assertWithinVault(opts.vault.root, exportRootAbs);

	const stats: MigrationStats = {
		status: "ok",
		sourceDb: opts.sourceDb,
		vaultRoot: opts.vault.root,
		exportRoot: exportRootAbs,
		observations: { migrated: 0, skippedDedup: 0, rewritten: 0 },
		sessionSummaries: { migrated: 0, skippedDedup: 0, rewritten: 0 },
		userPromptsHeads: { migrated: 0, skippedDedup: 0, rewritten: 0 },
		redactionsApplied: 0,
		archive: { mode: archive, status: "pending" },
		elapsedMs: 0,
		dryRun,
		errors: [],
	};

	if (!existsSync(opts.sourceDb)) {
		stats.status = "error";
		stats.errors.push(`Source DB not found: ${opts.sourceDb}`);
		stats.elapsedMs = Date.now() - t0;
		stats.archive.status = "skipped (source missing)";
		return stats;
	}

	if (archive === "move" && opts.confirmDelete !== true) {
		stats.status = "error";
		stats.errors.push(
			"archive=move requires confirmDelete=true (or --confirm-delete on CLI).",
		);
		stats.elapsedMs = Date.now() - t0;
		stats.archive.status = "refused (confirmDelete missing)";
		return stats;
	}

	if (!dryRun) {
		mkdirSync(exportRootAbs, { recursive: true });
	}

	const db = new Database(opts.sourceDb, {
		readonly: true,
		fileMustExist: true,
	});
	try {
		db.pragma("query_only = ON");
		const tables = tablesPresent(db);

		if (tables.observations) {
			try {
				const result = migrateObservations(db, exportRootAbs, dryRun);
				stats.observations.migrated = result.migrated;
				stats.observations.skippedDedup = result.skippedDedup;
				stats.observations.rewritten = result.rewritten;
				stats.redactionsApplied += result.redactions;
			} catch (err) {
				stats.errors.push(`observations: ${errorMessage(err)}`);
			}
		} else {
			stats.errors.push("observations table missing in source DB");
		}

		if (tables.sessionSummaries) {
			try {
				const result = migrateSessionSummaries(db, exportRootAbs, dryRun);
				stats.sessionSummaries.migrated = result.migrated;
				stats.sessionSummaries.skippedDedup = result.skippedDedup;
				stats.sessionSummaries.rewritten = result.rewritten;
				stats.redactionsApplied += result.redactions;
			} catch (err) {
				stats.errors.push(`session_summaries: ${errorMessage(err)}`);
			}
		}

		if (tables.userPrompts) {
			try {
				const result = migrateUserPromptsHeads(db, exportRootAbs, dryRun);
				stats.userPromptsHeads.migrated = result.migrated;
				stats.userPromptsHeads.skippedDedup = result.skippedDedup;
				stats.userPromptsHeads.rewritten = result.rewritten;
				stats.redactionsApplied += result.redactions;
			} catch (err) {
				stats.errors.push(`user_prompts: ${errorMessage(err)}`);
			}
		}
	} finally {
		db.close();
	}

	if (!dryRun) {
		// Phase J post-Codex-review fix: archive=move is destructive (unlinks
		// the source DB after copy). If any table migration recorded an error,
		// refuse to delete the source so the operator can re-run after the
		// underlying failure is understood. Pass-through to applyArchive only
		// when stats.errors is empty AND --confirm-delete was set for the
		// move mode. preserve and copy modes always run.
		const safeToMove = archive !== "move" || stats.errors.length === 0;
		if (safeToMove) {
			stats.archive = applyArchive(opts.sourceDb, exportRootAbs, archive);
		} else {
			stats.archive.status = "refused (errors present, archive=move blocked)";
			stats.errors.push(
				"archive=move refused because table migration errors are present; " +
					"source DB preserved for re-run after the underlying failure is resolved",
			);
		}
		stats.manifestPath = writeManifest(exportRootAbs, stats, opts);
	} else {
		stats.archive.status = "skipped (dry-run)";
	}

	if (stats.errors.length > 0 && stats.status === "ok") {
		// Errors during a subset of tables degrade status to error so the
		// shell exit code reflects partial failure.
		stats.status = "error";
	}

	stats.elapsedMs = Date.now() - t0;
	return stats;
}

interface TableResult {
	migrated: number;
	skippedDedup: number;
	rewritten: number;
	redactions: number;
}

function migrateObservations(
	db: Database.Database,
	exportRoot: string,
	dryRun: boolean,
): TableResult {
	const sql = `SELECT ${OBSERVATION_COLUMNS.join(", ")} FROM observations ORDER BY created_at_epoch ASC`;
	const rows = db.prepare(sql).all() as ObservationRow[];

	const result: TableResult = {
		migrated: 0,
		skippedDedup: 0,
		rewritten: 0,
		redactions: 0,
	};

	for (const row of rows) {
		const titleR = redact(row.title);
		const subtitleR = redact(row.subtitle);
		const narrativeR = redact(row.narrative);
		const textR = redact(row.text);
		const factsR = redact(row.facts);
		const conceptsR = redact(row.concepts);
		result.redactions +=
			titleR.count +
			subtitleR.count +
			narrativeR.count +
			textR.count +
			factsR.count +
			conceptsR.count;

		const bucket = extractDateBucket(row.created_at);
		const fileName = `cm-obs-${row.id}.md`;
		const targetAbs = join(exportRoot, bucket, fileName);

		const hash = observationContentHash(row);
		const existingHash = readExistingContentHash(targetAbs);
		if (existingHash === hash) {
			result.skippedDedup += 1;
			continue;
		}

		if (dryRun) {
			if (existingHash === null) {
				result.migrated += 1;
			} else {
				result.rewritten += 1;
			}
			continue;
		}

		const tagList = [
			row.type ?? "",
			row.project ? `project:${row.project}` : "",
		].filter((s) => s.length > 0);

		const fm = serializeFrontmatter(
			[
				["id", `cm-obs-${row.id}`],
				["type", "observation"],
				["created", row.created_at ?? ""],
				["schema_version", SCHEMA_VERSION],
				["session_id", row.memory_session_id ?? ""],
				["source", SOURCE_TAG],
				["content_hash", hash],
				["original_type", row.type ?? ""],
				["project", row.project ?? ""],
				["original_model", row.generated_by_model ?? ""],
				["agent_type", row.agent_type ?? ""],
			],
			[["tags", tagList]],
		);

		const body = buildObservationMarkdown(
			{ ...row, facts: factsR.text, concepts: conceptsR.text },
			{
				title: titleR.text,
				subtitle: subtitleR.text,
				narrative: narrativeR.text,
				text: textR.text,
			},
		);

		mkdirSync(join(exportRoot, bucket), { recursive: true });
		atomicWriteText(targetAbs, `${fm}${body}`);

		if (existingHash === null) {
			result.migrated += 1;
		} else {
			result.rewritten += 1;
		}
	}
	return result;
}

function migrateSessionSummaries(
	db: Database.Database,
	exportRoot: string,
	dryRun: boolean,
): TableResult {
	const rows = db
		.prepare("SELECT * FROM session_summaries ORDER BY id ASC")
		.all() as SessionSummaryRow[];

	const result: TableResult = {
		migrated: 0,
		skippedDedup: 0,
		rewritten: 0,
		redactions: 0,
	};

	const targetDir = join(exportRoot, "_sessions");

	for (const row of rows) {
		const redactedRow: Record<string, unknown> = {};
		let redactionCount = 0;
		for (const [k, v] of Object.entries(row)) {
			if (typeof v === "string") {
				const r = redact(v);
				redactedRow[k] = r.text;
				redactionCount += r.count;
			} else {
				redactedRow[k] = v;
			}
		}
		result.redactions += redactionCount;

		const hash = rowContentHash(redactedRow);
		const targetAbs = join(targetDir, `cm-sess-${row.id}.md`);
		const existingHash = readExistingContentHash(targetAbs);
		if (existingHash === hash) {
			result.skippedDedup += 1;
			continue;
		}

		if (dryRun) {
			if (existingHash === null) {
				result.migrated += 1;
			} else {
				result.rewritten += 1;
			}
			continue;
		}

		const fm = serializeFrontmatter([
			["id", `cm-sess-${row.id}`],
			["type", "session_summary"],
			[
				"created",
				typeof row.created_at === "string" && row.created_at.length > 0
					? row.created_at
					: new Date().toISOString(),
			],
			["schema_version", SCHEMA_VERSION],
			["source", SOURCE_TAG],
			["content_hash", hash],
			["original_table", "session_summaries"],
		]);

		const body = buildSessionSummaryMarkdown(row, redactedRow);
		mkdirSync(targetDir, { recursive: true });
		atomicWriteText(targetAbs, `${fm}${body}`);

		if (existingHash === null) {
			result.migrated += 1;
		} else {
			result.rewritten += 1;
		}
	}

	if (rows.length === 0) {
		pruneEmptyDirIfPresent(targetDir);
	}
	return result;
}

function migrateUserPromptsHeads(
	db: Database.Database,
	exportRoot: string,
	dryRun: boolean,
): TableResult {
	const rows = db
		.prepare("SELECT * FROM user_prompts ORDER BY id ASC")
		.all() as UserPromptRow[];

	const result: TableResult = {
		migrated: 0,
		skippedDedup: 0,
		rewritten: 0,
		redactions: 0,
	};

	const targetDir = join(exportRoot, "_prompts");

	for (const row of rows) {
		const redactedRow: Record<string, unknown> = {};
		let redactionCount = 0;
		for (const [k, v] of Object.entries(row)) {
			if (typeof v === "string") {
				// Redact BEFORE truncating: a <private>...</private> block that
				// straddles the truncation boundary would otherwise be cut into
				// an unclosed tag that the redactor cannot match, leaking the
				// visible head of the secret into the vault.
				const r = redact(v);
				const truncated =
					r.text.length > USER_PROMPT_HEAD_MAX
						? `${r.text.slice(0, USER_PROMPT_HEAD_MAX)}... (truncated)`
						: r.text;
				redactedRow[k] = truncated;
				redactionCount += r.count;
			} else {
				redactedRow[k] = v;
			}
		}
		result.redactions += redactionCount;

		const hash = rowContentHash(redactedRow);
		const targetAbs = join(targetDir, `cm-prompt-${row.id}.md`);
		const existingHash = readExistingContentHash(targetAbs);
		if (existingHash === hash) {
			result.skippedDedup += 1;
			continue;
		}

		if (dryRun) {
			if (existingHash === null) {
				result.migrated += 1;
			} else {
				result.rewritten += 1;
			}
			continue;
		}

		const fm = serializeFrontmatter([
			["id", `cm-prompt-${row.id}`],
			["type", "user_prompt"],
			[
				"created",
				typeof row.created_at === "string" && row.created_at.length > 0
					? row.created_at
					: new Date().toISOString(),
			],
			["schema_version", SCHEMA_VERSION],
			["source", SOURCE_TAG],
			["content_hash", hash],
			["original_table", "user_prompts"],
			["truncated_at_chars", USER_PROMPT_HEAD_MAX],
		]);

		const body = buildUserPromptMarkdown(row, redactedRow);
		mkdirSync(targetDir, { recursive: true });
		atomicWriteText(targetAbs, `${fm}${body}`);

		if (existingHash === null) {
			result.migrated += 1;
		} else {
			result.rewritten += 1;
		}
	}

	if (rows.length === 0) {
		pruneEmptyDirIfPresent(targetDir);
	}
	return result;
}

function applyArchive(
	sourceDb: string,
	exportRoot: string,
	mode: ArchiveMode,
): { mode: ArchiveMode; status: string; path?: string } {
	if (mode === "preserve") {
		return { mode, status: "preserved" };
	}
	const archiveDir = join(exportRoot, "_archive");
	const destDb = join(archiveDir, "claude-mem.db");
	try {
		mkdirSync(archiveDir, { recursive: true });
		copyFileSync(sourceDb, destDb);
	} catch (err) {
		return { mode, status: `copy_failed: ${errorMessage(err)}` };
	}
	if (mode === "move") {
		try {
			unlinkSync(sourceDb);
			return { mode, status: "moved", path: destDb };
		} catch (err) {
			return {
				mode,
				status: `delete_failed: ${errorMessage(err)}`,
				path: destDb,
			};
		}
	}
	return { mode, status: "copied", path: destDb };
}

function writeManifest(
	exportRoot: string,
	stats: MigrationStats,
	opts: MigrationOptions,
): string {
	const manifest = {
		schema: "mneme-migration-manifest/1",
		run_at: new Date().toISOString(),
		source_db: opts.sourceDb,
		source_db_size_bytes: safeStatSize(opts.sourceDb),
		vault_root: opts.vault.root,
		export_root: exportRoot,
		observations: stats.observations,
		session_summaries: stats.sessionSummaries,
		user_prompts_heads: stats.userPromptsHeads,
		redactions_applied: stats.redactionsApplied,
		archive: stats.archive,
		errors: stats.errors,
	};
	const manifestPath = join(exportRoot, "_manifest.json");
	atomicWriteText(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
	return manifestPath;
}

function safeStatSize(path: string): number | null {
	try {
		return statSync(path).size;
	} catch {
		return null;
	}
}

function errorMessage(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}
