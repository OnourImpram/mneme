import { createHmac } from "node:crypto";
import {
	mkdirSync,
	mkdtempSync,
	readFileSync,
	statSync,
	utimesSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { WriteInputSchema, writeTool } from "../../src/tools/write.js";
import { VaultConfig } from "../../src/vault/config.js";

function makeBareVault(prefix: string): {
	vault: VaultConfig;
	rootDir: string;
} {
	const rootDir = mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
	return { vault: VaultConfig.fromPath(rootDir), rootDir };
}

describe("WriteInputSchema", () => {
	it("requires path, section, content", () => {
		expect(() => WriteInputSchema.parse({})).toThrow();
	});

	it("defaults replace to false", () => {
		const parsed = WriteInputSchema.parse({
			path: "a.md",
			section: "Notes",
			content: "x",
		});
		expect(parsed.replace).toBe(false);
	});

	it("accepts frontmatter record", () => {
		const parsed = WriteInputSchema.parse({
			path: "a.md",
			section: "S",
			content: "",
			frontmatter: { id: "abc", schema_version: 1 },
		});
		expect(parsed.frontmatter?.id).toBe("abc");
	});
});

describe("writeTool runtime", () => {
	it("appends a new section to a fresh file", () => {
		const { vault, rootDir } = makeBareVault("write-fresh");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "daily/notes.md",
				section: "Today",
				content: "Logged X.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.operation).toBe("added");
			expect(res.data.created_new_file).toBe(true);
			const written = readFileSync(join(rootDir, "daily/notes.md"), "utf8");
			expect(written).toContain("## Today");
			expect(written).toContain("Logged X.");
		}
	});

	it("appends to an existing file without overwriting", () => {
		const { vault, rootDir } = makeBareVault("write-append");
		const target = join(rootDir, "doc.md");
		writeFileSync(target, "Existing line.\n", "utf8");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "doc.md",
				section: "Second",
				content: "Second body.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const written = readFileSync(target, "utf8");
			expect(written).toContain("Existing line.");
			expect(written).toContain("## Second");
		}
	});

	it("replaces an existing section in replace mode", () => {
		const { vault, rootDir } = makeBareVault("write-replace");
		const target = join(rootDir, "doc.md");
		writeFileSync(target, "## Notes\n\nOld body.\n", "utf8");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "doc.md",
				section: "Notes",
				content: "New body.",
				replace: true,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.operation).toBe("replaced");
			const written = readFileSync(target, "utf8");
			expect(written).toContain("New body.");
			expect(written).not.toContain("Old body.");
		}
	});

	it("rejects paths that escape the vault root", () => {
		const { vault } = makeBareVault("write-escape");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "../outside.md",
				section: "S",
				content: "x",
			}),
			vault,
		);
		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.PATH_OUTSIDE_VAULT);
	});

	it("creates parent directories as needed", () => {
		const { vault, rootDir } = makeBareVault("write-mkdir");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "a/b/c/deep.md",
				section: "S",
				content: "deep",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const created = readFileSync(join(rootDir, "a/b/c/deep.md"), "utf8");
		expect(created).toContain("## S");
	});

	it("prepends frontmatter only when creating a new file", () => {
		const { vault, rootDir } = makeBareVault("write-fm");
		const r1 = writeTool(
			WriteInputSchema.parse({
				path: "fm.md",
				section: "Body",
				content: "x",
				frontmatter: { id: "abc", type: "topic" },
			}),
			vault,
		);
		expect(r1.ok).toBe(true);
		const after1 = readFileSync(join(rootDir, "fm.md"), "utf8");
		expect(after1.startsWith("---\n")).toBe(true);
		// Codex Pass 2 YAML-escape fix emits double-quoted string scalars.
		expect(after1).toContain('id: "abc"');

		const r2 = writeTool(
			WriteInputSchema.parse({
				path: "fm.md",
				section: "Second",
				content: "y",
				frontmatter: { id: "different", type: "session" },
			}),
			vault,
		);
		expect(r2.ok).toBe(true);
		const after2 = readFileSync(join(rootDir, "fm.md"), "utf8");
		// Existing frontmatter is preserved unchanged on second write.
		expect(after2.split("---").length).toBeLessThanOrEqual(3);
		expect(after2).toContain('id: "abc"');
		expect(after2).not.toContain('id: "different"');
	});
});

// T9: write response path must be vault-relative, not absolute
describe("writeTool path in response (T9)", () => {
	it("returned path is the relative input path, not an absolute path", () => {
		const { vault } = makeBareVault("write-t9-relpath");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "sessions/x.md",
				section: "Notes",
				content: "body",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.path).toBe("sessions/x.md");
			// Must not be absolute: no drive letter (Windows) and no leading slash (Unix)
			expect(res.data.path).not.toMatch(/^[A-Za-z]:[/\\]/);
			expect(res.data.path).not.toMatch(/^\//);
			// Must not contain the vault root prefix
			expect(res.data.path).not.toContain(tmpdir());
		}
	});

	it("returned path uses forward slashes regardless of OS path separator", () => {
		const { vault } = makeBareVault("write-t9-fwdslash");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "a/b/c.md",
				section: "S",
				content: "x",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.path).toBe("a/b/c.md");
		}
	});
});

// F1: canonical redact() — case-insensitive and attribute-tolerant
describe("writeTool redaction (F1)", () => {
	it("redacts uppercase <PRIVATE>secret</PRIVATE> before writing", () => {
		const { vault, rootDir } = makeBareVault("write-redact-upper");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "redact-upper.md",
				section: "Notes",
				content: "Prefix <PRIVATE>secret</PRIVATE> suffix.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(join(rootDir, "redact-upper.md"), "utf8");
		expect(written).not.toContain("secret");
		expect(written).toContain("[REDACTED]");
		if (res.ok) expect(res.data.redactions_applied).toBeGreaterThanOrEqual(1);
	});

	it('redacts attribute-bearing <private reason="x">secret</private> before writing', () => {
		const { vault, rootDir } = makeBareVault("write-redact-attr");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "redact-attr.md",
				section: "Notes",
				content: 'Before <private reason="x">secret</private> after.',
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(join(rootDir, "redact-attr.md"), "utf8");
		expect(written).not.toContain("secret");
		expect(written).toContain("[REDACTED]");
		if (res.ok) expect(res.data.redactions_applied).toBeGreaterThanOrEqual(1);
	});
});

// F2: H2 heading guard
describe("writeTool H2 body guard (F2)", () => {
	it("throws when content contains a '## ' H2 line", () => {
		const { vault } = makeBareVault("write-h2-guard");
		expect(() =>
			writeTool(
				WriteInputSchema.parse({
					path: "h2guard.md",
					section: "Top",
					content: "Some text.\n## Sub\nMore text.",
				}),
				vault,
			),
		).toThrow(/H2/i);
	});

	it("succeeds when content contains only H3+ headings", () => {
		const { vault, rootDir } = makeBareVault("write-h3-ok");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "h3ok.md",
				section: "Top",
				content: "Some text.\n### Sub-heading\nMore text.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(join(rootDir, "h3ok.md"), "utf8");
		expect(written).toContain("### Sub-heading");
	});
});

// F3: append separator
describe("writeTool append separator (F3)", () => {
	it("appending to a file ending in a single newline yields exactly one blank line before the new heading", () => {
		const { vault, rootDir } = makeBareVault("write-sep-single-nl");
		const target = join(rootDir, "sep.md");
		writeFileSync(target, "Existing content.\n", "utf8");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "sep.md",
				section: "New Section",
				content: "New body.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(target, "utf8");
		// Exactly one blank line between old content and the new heading.
		expect(written).toContain("Existing content.\n\n## New Section");
		// Must NOT have two blank lines (three consecutive newlines).
		expect(written).not.toContain("Existing content.\n\n\n");
	});

	it("appending to a file already ending in double newline adds no extra blank line", () => {
		const { vault, rootDir } = makeBareVault("write-sep-double-nl");
		const target = join(rootDir, "sep2.md");
		writeFileSync(target, "Existing content.\n\n", "utf8");
		const res = writeTool(
			WriteInputSchema.parse({
				path: "sep2.md",
				section: "New Section",
				content: "New body.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		const written = readFileSync(target, "utf8");
		expect(written).toContain("Existing content.\n\n## New Section");
		expect(written).not.toContain("Existing content.\n\n\n");
	});
});

// ---------------------------------------------------------------------------
// T4: audit log persistence
// ---------------------------------------------------------------------------

/**
 * Parse all non-empty lines from a JSONL file into typed audit records.
 */
function readAuditRecords(jsonlPath: string): Array<{
	timestamp_iso: string;
	sequence: number;
	relative_path: string;
	redactions_applied: number;
	prev_hash: string;
	hmac: string;
}> {
	const raw = readFileSync(jsonlPath, "utf8");
	return raw
		.split("\n")
		.filter((l) => l.trim().length > 0)
		.map((l) => JSON.parse(l));
}

/**
 * Recompute the expected HMAC for a record, matching audit.ts logic:
 *   HMAC-SHA256(key, prevHash + JSON({timestamp_iso, sequence,
 *                                     relative_path, redactions_applied,
 *                                     prev_hash}))
 */
function verifyHmac(
	key: Buffer,
	record: ReturnType<typeof readAuditRecords>[number],
): boolean {
	const { hmac: _hmac, ...withoutHmac } = record;
	const serialized = JSON.stringify(withoutHmac);
	const expected = createHmac("sha256", key)
		.update(record.prev_hash + serialized)
		.digest("hex");
	return expected === record.hmac;
}

describe("writeTool audit log (T4)", () => {
	it("a write with a private block appends exactly one audit record with correct fields", () => {
		const { vault, rootDir } = makeBareVault("write-t4-single");

		const res = writeTool(
			WriteInputSchema.parse({
				path: "private-note.md",
				section: "Secrets",
				content: "Before <private>topsecret</private> after.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.redactions_applied).toBe(1);

		// Locate the daily JSONL file.
		const today = new Date().toISOString().slice(0, 10);
		const auditDir = join(rootDir, ".mneme", "audit");
		const jsonlPath = join(auditDir, `${today}.jsonl`);
		expect(() => readFileSync(jsonlPath)).not.toThrow();

		const records = readAuditRecords(jsonlPath);
		expect(records).toHaveLength(1);

		const rec = records[0];
		expect(rec).toBeDefined();
		if (rec === undefined) return;

		// All required fields are present.
		expect(typeof rec.timestamp_iso).toBe("string");
		expect(rec.timestamp_iso).toMatch(/^\d{4}-\d{2}-\d{2}T/);
		expect(rec.sequence).toBe(1);
		expect(rec.relative_path).toBe("private-note.md");
		expect(rec.redactions_applied).toBe(1);
		// First record chains off all-zeros.
		expect(rec.prev_hash).toBe("0".repeat(64));
		expect(typeof rec.hmac).toBe("string");
		expect(rec.hmac).toHaveLength(64);
	});

	it("second write chains its prev_hash off the first record's hmac, and both HMACs verify", () => {
		const { vault, rootDir } = makeBareVault("write-t4-chain");

		// First write.
		writeTool(
			WriteInputSchema.parse({
				path: "chain.md",
				section: "First",
				content: "Hello <private>secret-one</private> world.",
			}),
			vault,
		);

		// Second write (replace mode to keep single file, different content).
		writeTool(
			WriteInputSchema.parse({
				path: "chain.md",
				section: "Second",
				content: "Another <private>secret-two</private> entry.",
			}),
			vault,
		);

		const today = new Date().toISOString().slice(0, 10);
		const jsonlPath = join(rootDir, ".mneme", "audit", `${today}.jsonl`);
		const records = readAuditRecords(jsonlPath);
		expect(records).toHaveLength(2);

		const [first, second] = records as [
			(typeof records)[number],
			(typeof records)[number],
		];

		// Chain: second.prev_hash === first.hmac.
		expect(second.prev_hash).toBe(first.hmac);
		expect([first.sequence, second.sequence]).toEqual([1, 2]);

		// Read key and verify both HMACs.
		const keyPath = join(rootDir, ".mneme", "audit-hmac.key");
		const key = readFileSync(keyPath);
		expect(verifyHmac(key, first)).toBe(true);
		expect(verifyHmac(key, second)).toBe(true);
	});

	it("key file is created at the correct path", () => {
		const { vault, rootDir } = makeBareVault("write-t4-keypath");

		writeTool(
			WriteInputSchema.parse({
				path: "kp.md",
				section: "S",
				content: "<private>x</private>",
			}),
			vault,
		);

		const keyPath = join(rootDir, ".mneme", "audit-hmac.key");
		const key = readFileSync(keyPath);
		// Key must be exactly 32 bytes.
		expect(key.length).toBe(32);
	});

	it("key file has restricted permissions on POSIX (mode 0o600)", () => {
		// Skip on Windows where mode bits don't set NTFS ACLs.
		if (process.platform === "win32") return;

		const { vault, rootDir } = makeBareVault("write-t4-perms");

		writeTool(
			WriteInputSchema.parse({
				path: "perm.md",
				section: "S",
				content: "<private>secret</private>",
			}),
			vault,
		);

		const keyPath = join(rootDir, ".mneme", "audit-hmac.key");
		const mode = statSync(keyPath).mode & 0o777;
		expect(mode).toBe(0o600);
	});

	it("a write with no private block does NOT create an audit record", () => {
		const { vault, rootDir } = makeBareVault("write-t4-noredact");

		const res = writeTool(
			WriteInputSchema.parse({
				path: "plain.md",
				section: "Notes",
				content: "No private content here.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.redactions_applied).toBe(0);

		// Audit dir must either not exist or contain no records for today.
		const today = new Date().toISOString().slice(0, 10);
		const jsonlPath = join(rootDir, ".mneme", "audit", `${today}.jsonl`);
		// File should not exist (no redaction → no audit call).
		expect(() => readFileSync(jsonlPath)).toThrow();
	});

	it("stale lock file (old mtime) is stolen and append still succeeds (Finding 3)", () => {
		// A lock file whose mtime exceeds LOCK_STALE_MS (10s) simulates a lock
		// left behind by a crashed process. acquireLock must steal it so the
		// write does not time out and the audit record is written correctly.
		// Backdated by 15s — safely past the 10s LOCK_STALE_MS threshold.
		const { vault, rootDir } = makeBareVault("write-t4-stale-lock");

		// Ensure the audit directory exists before planting the stale lock.
		const auditDir = join(rootDir, ".mneme", "audit");
		mkdirSync(auditDir, { recursive: true });

		const today = new Date().toISOString().slice(0, 10);
		const lockPath = join(auditDir, `${today}.lock`);
		const jsonlPath = join(auditDir, `${today}.jsonl`);

		// Plant a stale lock file: create it, then back-date its mtime by 15 seconds
		// (well beyond the 10s LOCK_STALE_MS threshold).
		writeFileSync(lockPath, "stale", "utf8");
		const staleTime = new Date(Date.now() - 15_000);
		utimesSync(lockPath, staleTime, staleTime);

		// The write must succeed despite the stale lock.
		const res = writeTool(
			WriteInputSchema.parse({
				path: "stale-lock-test.md",
				section: "Secrets",
				content: "Sensitive <private>stale-lock-secret</private> data.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.redactions_applied).toBe(1);

		// The JSONL file must contain exactly one well-formed record.
		const records = readAuditRecords(jsonlPath);
		expect(records).toHaveLength(1);
		expect(records[0]?.relative_path).toBe("stale-lock-test.md");
		expect(records[0]?.redactions_applied).toBe(1);

		// The stale lock must have been removed (stolen).
		expect(() => statSync(lockPath)).toThrow();
	});

	it("fresh lock file (recent mtime) is NOT stolen within acquisition window", () => {
		// A lock whose mtime is well within LOCK_STALE_MS (10s) belongs to a
		// live writer. acquireLock must NOT steal it; the audit append times out
		// non-fatally, the write response still returns ok=true (audit is
		// best-effort), and the JSONL file is never created.
		const { vault, rootDir } = makeBareVault("write-t4-fresh-lock");

		const auditDir = join(rootDir, ".mneme", "audit");
		mkdirSync(auditDir, { recursive: true });

		const today = new Date().toISOString().slice(0, 10);
		const lockPath = join(auditDir, `${today}.lock`);
		const jsonlPath = join(auditDir, `${today}.jsonl`);

		// Plant a fresh lock: mtime is "now", well inside LOCK_STALE_MS.
		writeFileSync(lockPath, "held-by-live-writer", "utf8");
		const freshTime = new Date(Date.now());
		utimesSync(lockPath, freshTime, freshTime);

		// The write itself must succeed (redaction happens); only the audit
		// append fails non-fatally because the fresh lock is never stolen.
		const res = writeTool(
			WriteInputSchema.parse({
				path: "fresh-lock-test.md",
				section: "Secrets",
				content: "Sensitive <private>fresh-lock-secret</private> data.",
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (!res.ok) return;
		expect(res.data.redactions_applied).toBe(1);

		// JSONL must NOT exist — the audit append timed out without writing.
		expect(() => readFileSync(jsonlPath)).toThrow();

		// The lock file must still exist (it was NOT stolen).
		expect(() => statSync(lockPath)).not.toThrow();
	});
});
