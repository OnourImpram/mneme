/**
 * Security regression tests: S1–S4, S6
 *
 * S1: recall type predicate — non-session notes must not be returned
 * S2: working_set_load path traversal — poisoned index path must be refused
 * S3: sanitize returned fields — checkpoint bullets and KG fields
 * S4: input size limits — oversize inputs are rejected at parse
 * S6: telemetry HMAC — different vault keys produce different query hashes
 *
 * S5 lives in tests/vault/atomic_write_cleanup.test.ts (requires vi.mock).
 */

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FENCE_CLOSE, FENCE_OPEN } from "../src/injection.js";
import {
	expandTopicNeighborhood,
	type Neo4jDriverLike,
	timelineForSubject,
} from "../src/retrieval/graphiti.js";
import { computeQueryHash } from "../src/retrieval/telemetry.js";
import { PrimeInputSchema } from "../src/tools/prime.js";
import { ProposeInputSchema } from "../src/tools/propose.js";
import { RecallInputSchema, recallTool } from "../src/tools/recall.js";
import { SearchInputSchema } from "../src/tools/search.js";
import {
	WorkingSetLoadInputSchema,
	workingSetLoadTool,
} from "../src/tools/working_set_load.js";
import { WriteInputSchema } from "../src/tools/write.js";
import { VaultConfig } from "../src/vault/config.js";
import { defaultDocs, makeTempVault } from "./helpers/vault_fixture.js";

// ---------------------------------------------------------------------------
// S1: recall type predicate
// ---------------------------------------------------------------------------

describe("S1: recall returns only session-typed documents", () => {
	it("excludes non-session (reference) docs from recall results", () => {
		// defaultDocs() has 2 reference docs and 1 session doc.
		// After the fix, recall must only return the session doc.
		const { vault } = makeTempVault("s1-type-pred", defaultDocs());
		const res = recallTool(
			RecallInputSchema.parse({ include_body: false, top_n: 50 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			for (const e of res.data.entries) {
				expect(e.frontmatter_type).toBe("session");
			}
		}
	});

	it("session doc is present while reference docs are absent", () => {
		const { vault } = makeTempVault("s1-presence", defaultDocs());
		const res = recallTool(
			RecallInputSchema.parse({ include_body: false, top_n: 50 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const paths = res.data.entries.map((e) => e.path);
			expect(paths).toContain("daily/2026-05-18.md"); // session doc
			expect(paths).not.toContain("reference/vault-native.md"); // reference doc
			expect(paths).not.toContain("reference/privacy.md"); // reference doc
		}
	});
});

// ---------------------------------------------------------------------------
// S2: working_set_load path traversal guard
// ---------------------------------------------------------------------------

describe("S2: working_set_load path traversal", () => {
	it("checkpoint index with ../traversal path yields not-found and reads nothing outside vault", () => {
		// Create a base dir so vault has a parent containing a reachable file.
		const base = mkdtempSync(join(tmpdir(), "mneme-s2-base-"));
		const vaultRoot = join(base, "vault");
		mkdirSync(join(vaultRoot, ".mneme"), { recursive: true });
		const vault = VaultConfig.fromPath(vaultRoot);

		// Place a real file outside the vault — accessible via ../escape.md.
		const escapeFile = join(base, "escape.md");
		writeFileSync(escapeFile, "escaped content\n", "utf8");

		// Index entry whose path traverses out of the vault.
		const indexDir = join(vaultRoot, ".mneme", "checkpoints");
		mkdirSync(indexDir, { recursive: true });
		writeFileSync(
			join(indexDir, "index.jsonl"),
			`${JSON.stringify({ anchor: "evil", path: "../escape.md" })}\n`,
			"utf8",
		);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: "evil" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			// Without fix: existsSync(base/escape.md) = true → returns a data object,
			// not WorkingSetNotFound → data.found is undefined, not false → test fails.
			// With fix: assertWithinVault throws → continue → not_found.
			const data = res.data as { found?: boolean };
			expect(data.found).toBe(false);
		}
	});
});

// ---------------------------------------------------------------------------
// S3: sanitize returned fields
// ---------------------------------------------------------------------------

describe("S3a: working_set_load checkpoint bullet sanitization", () => {
	function makeCheckpointVault(prefix: string, anchor: string, md: string) {
		const { vault, rootDir } = makeTempVault(prefix);
		const indexDir = join(rootDir, ".mneme", "checkpoints");
		const cpDir = join(rootDir, "checkpoints");
		mkdirSync(indexDir, { recursive: true });
		mkdirSync(cpDir, { recursive: true });
		writeFileSync(
			join(indexDir, "index.jsonl"),
			`${JSON.stringify({ anchor, path: `checkpoints/cp-${anchor}.md` })}\n`,
			"utf8",
		);
		writeFileSync(join(cpDir, `cp-${anchor}.md`), md, "utf8");
		return { vault };
	}

	it("bullet with <private> block is redacted and fenced", () => {
		const anchor = "s3priv";
		const md = `---
type: checkpoint
anchor: ${anchor}
---

## Decisions
- [salience 0.90] Public <private>secret-s3a</private> end
`;
		const { vault } = makeCheckpointVault("s3a-priv", anchor, md);
		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { items: Array<{ text: string }> };
			const text = data.items[0]?.text ?? "";
			expect(text).not.toContain("secret-s3a");
			expect(text).toContain("[REDACTED]");
			expect(text).toContain(FENCE_OPEN);
			expect(text).toContain(FENCE_CLOSE);
		}
	});

	it("bullet with fence sentinel is neutralized inside the outer fence", () => {
		const anchor = "s3fence";
		const md = `---
type: checkpoint
anchor: ${anchor}
---

## Injections
- [salience 0.80] Inject [mneme:untrusted-memory] sentinel
`;
		const { vault } = makeCheckpointVault("s3a-fence", anchor, md);
		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { items: Array<{ text: string }> };
			const text = data.items[0]?.text ?? "";
			// Outer fence is present
			expect(text).toContain(FENCE_OPEN);
			expect(text).toContain(FENCE_CLOSE);
			// The raw sentinel inside the content must be neutralized (brackets → parens)
			const inner = text.slice(
				text.indexOf("\n") + 1,
				text.lastIndexOf(FENCE_CLOSE),
			);
			expect(inner).not.toContain("[mneme:untrusted-memory]");
		}
	});
});

describe("S3b: Graphiti entity/summary/fact sanitization", () => {
	function makeDriver(
		records: Array<Record<string, unknown>>,
	): Neo4jDriverLike {
		return {
			session() {
				return {
					run: async () => ({
						records: records.map((row) => ({
							keys: Object.keys(row),
							get: (k: string) => row[k] ?? null,
						})),
					}),
					close: async () => undefined,
				};
			},
			close: async () => undefined,
		};
	}

	it("expandTopicNeighborhood: entity/summary with <private> are redacted and fenced", async () => {
		const driver = makeDriver([
			{
				entity: "Entity with <private>secret-entity</private>",
				summary: "Summary <private>secret-summary</private> end",
				source_doc: null,
			},
		]);
		const hits = await expandTopicNeighborhood(driver, "test");
		const h = hits[0];
		expect(h?.entity).not.toContain("secret-entity");
		expect(h?.entity).toContain("[REDACTED]");
		expect(h?.entity).toContain(FENCE_OPEN);
		expect(h?.summary).not.toContain("secret-summary");
		expect(h?.summary).toContain(FENCE_OPEN);
	});

	it("timelineForSubject: fact with <private> is redacted and fenced", async () => {
		const driver = makeDriver([
			{
				subject: "Subject",
				fact: "Fact <private>secret-fact</private> done",
				object: null,
				valid_at: null,
				invalid_at: null,
				reference_time: null,
			},
		]);
		const facts = await timelineForSubject(driver, "Subject");
		const f = facts[0];
		expect(f?.fact).not.toContain("secret-fact");
		expect(f?.fact).toContain("[REDACTED]");
		expect(f?.fact).toContain(FENCE_OPEN);
	});

	it("expandTopicNeighborhood: source_doc is NOT fenced (it is a path)", async () => {
		const driver = makeDriver([
			{ entity: "E", summary: "S", source_doc: "ref/doc.md" },
		]);
		const hits = await expandTopicNeighborhood(driver, "E");
		// source_doc should be a plain string (path), not wrapped
		expect(hits[0]?.source_doc).toBe("ref/doc.md");
	});
});

// ---------------------------------------------------------------------------
// S4: input size limits
// ---------------------------------------------------------------------------

describe("S4: input size limits", () => {
	// WriteInputSchema
	it("WriteInputSchema rejects content > 100000 chars", () => {
		expect(() =>
			WriteInputSchema.parse({
				path: "x.md",
				section: "S",
				content: "x".repeat(100001),
			}),
		).toThrow();
	});

	it("WriteInputSchema accepts content exactly at 100000 chars", () => {
		expect(() =>
			WriteInputSchema.parse({
				path: "x.md",
				section: "S",
				content: "x".repeat(100000),
			}),
		).not.toThrow();
	});

	it("WriteInputSchema rejects path > 1024 chars", () => {
		expect(() =>
			WriteInputSchema.parse({
				path: "x".repeat(1025),
				section: "S",
				content: "body",
			}),
		).toThrow();
	});

	it("WriteInputSchema accepts path exactly at 1024 chars", () => {
		expect(() =>
			WriteInputSchema.parse({
				path: "x".repeat(1024),
				section: "S",
				content: "body",
			}),
		).not.toThrow();
	});

	// ProposeInputSchema
	it("ProposeInputSchema rejects content > 100000 chars", () => {
		expect(() =>
			ProposeInputSchema.parse({
				action: "create",
				path: "x.md",
				content: "x".repeat(100001),
			}),
		).toThrow();
	});

	it("ProposeInputSchema rejects path > 1024 chars", () => {
		expect(() =>
			ProposeInputSchema.parse({
				action: "create",
				path: "x".repeat(1025),
			}),
		).toThrow();
	});

	it("ProposeInputSchema accepts path exactly at 1024 chars", () => {
		expect(() =>
			ProposeInputSchema.parse({
				action: "create",
				path: "x".repeat(1024),
			}),
		).not.toThrow();
	});

	// SearchInputSchema
	it("SearchInputSchema rejects query > 2048 chars", () => {
		expect(() =>
			SearchInputSchema.parse({ query: "x".repeat(2049) }),
		).toThrow();
	});

	it("SearchInputSchema accepts query exactly at 2048 chars", () => {
		expect(() =>
			SearchInputSchema.parse({ query: "x".repeat(2048) }),
		).not.toThrow();
	});

	// PrimeInputSchema
	it("PrimeInputSchema rejects task_description > 2048 chars", () => {
		expect(() =>
			PrimeInputSchema.parse({ task_description: "x".repeat(2049) }),
		).toThrow();
	});

	it("PrimeInputSchema accepts task_description exactly at 2048 chars", () => {
		expect(() =>
			PrimeInputSchema.parse({ task_description: "x".repeat(2048) }),
		).not.toThrow();
	});

	it("PrimeInputSchema rejects session_id > 256 chars", () => {
		expect(() =>
			PrimeInputSchema.parse({
				task_description: "task",
				session_id: "x".repeat(257),
			}),
		).toThrow();
	});

	it("PrimeInputSchema accepts session_id exactly at 256 chars", () => {
		expect(() =>
			PrimeInputSchema.parse({
				task_description: "task",
				session_id: "x".repeat(256),
			}),
		).not.toThrow();
	});
});

// ---------------------------------------------------------------------------
// S6: telemetry HMAC keying
// ---------------------------------------------------------------------------

describe("S6: computeQueryHash — per-vault HMAC keying", () => {
	function freshStateDir(prefix: string): string {
		return mkdtempSync(join(tmpdir(), `mneme-s6-${prefix}-`));
	}

	it("same query under two different vault keys produces different hashes", () => {
		const stateDir1 = freshStateDir("vault1");
		const stateDir2 = freshStateDir("vault2");
		const query = "istanbul kayit normalized";
		const hash1 = computeQueryHash(stateDir1, query);
		const hash2 = computeQueryHash(stateDir2, query);
		// Different vault keys → different hashes for identical query
		expect(hash1).not.toBe(hash2);
	});

	it("same vault key produces the same hash for the same query (key persistence)", () => {
		const stateDir = freshStateDir("persist");
		const query = "persistent query";
		const hash1 = computeQueryHash(stateDir, query);
		const hash2 = computeQueryHash(stateDir, query);
		expect(hash1).toBe(hash2);
	});

	it("different queries under the same vault key produce different hashes", () => {
		const stateDir = freshStateDir("diff-query");
		const h1 = computeQueryHash(stateDir, "query one");
		const h2 = computeQueryHash(stateDir, "query two");
		expect(h1).not.toBe(h2);
	});

	it("returns a 64-char hex string (SHA-256 output length)", () => {
		const stateDir = freshStateDir("hex-len");
		const hash = computeQueryHash(stateDir, "test query");
		expect(hash).toMatch(/^[0-9a-f]{64}$/);
	});
});
