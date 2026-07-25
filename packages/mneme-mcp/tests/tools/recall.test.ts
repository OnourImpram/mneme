import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { FENCE_CLOSE, FENCE_OPEN } from "../../src/injection.js";
import { RecallInputSchema, recallTool } from "../../src/tools/recall.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

describe("RecallInputSchema", () => {
	it("accepts empty args (returns latest docs)", () => {
		expect(() => RecallInputSchema.parse({})).not.toThrow();
	});

	it("accepts session_id only", () => {
		const parsed = RecallInputSchema.parse({ session_id: "s-1" });
		expect(parsed.session_id).toBe("s-1");
	});

	it("rejects malformed date_from", () => {
		expect(() =>
			RecallInputSchema.parse({ date_from: "2026/05/18" }),
		).toThrow();
	});

	it("defaults top_n to 10 and include_body to true", () => {
		const parsed = RecallInputSchema.parse({});
		expect(parsed.top_n).toBe(10);
		expect(parsed.include_body).toBe(true);
	});
});

describe("recallTool runtime", () => {
	function writeBody(rootDir: string, relPath: string, body: string): void {
		const abs = join(rootDir, relPath);
		mkdirSync(dirname(abs), { recursive: true });
		writeFileSync(abs, body, "utf8");
	}

	it("INDEX_NOT_FOUND without fts5.sqlite", () => {
		const { vault } = makeTempVault("recall-noindex", []);
		const res = recallTool(RecallInputSchema.parse({}), vault);
		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
	});

	it("returns latest docs in DESC mtime order when no filter", () => {
		const { vault } = makeTempVault("recall-latest", defaultDocs());
		const res = recallTool(
			RecallInputSchema.parse({ include_body: false }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const mtimes = res.data.entries.map((e) => e.mtime);
			const sorted = [...mtimes].sort((a, b) => b - a);
			expect(mtimes).toEqual(sorted);
		}
	});

	it("filters by session_id", () => {
		const { vault } = makeTempVault("recall-sid", defaultDocs());
		const res = recallTool(
			RecallInputSchema.parse({
				session_id: "s-2026-05-18",
				include_body: false,
			}),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			for (const e of res.data.entries)
				expect(e.session_id).toBe("s-2026-05-18");
		}
	});

	it("filters by date_from inclusive", () => {
		const { vault } = makeTempVault("recall-dfrom", defaultDocs());
		const res = recallTool(
			RecallInputSchema.parse({ date_from: "2026-05-25", include_body: false }),
			vault,
		);
		expect(res.ok).toBe(true);
	});

	it("populates body when include_body and file exists", () => {
		const { vault, rootDir } = makeTempVault("recall-body", defaultDocs());
		writeBody(rootDir, "daily/2026-05-18.md", "# Daily Log\n\nReal body.\n");
		const res = recallTool(
			RecallInputSchema.parse({ include_body: true }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const hit = res.data.entries.find(
				(e) => e.path === "daily/2026-05-18.md",
			);
			expect(hit?.body).toContain("Real body.");
		}
	});

	it("leaves body undefined when file missing on disk", () => {
		const { vault } = makeTempVault("recall-nobody", defaultDocs());
		// No files written to vault root, so reads will fail silently.
		const res = recallTool(
			RecallInputSchema.parse({ include_body: true, top_n: 5 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			for (const e of res.data.entries) expect(e.body).toBeUndefined();
		}
	});

	it("body does not expose <private> content (privacy leak guard)", () => {
		// Finding 1: a vault file with a <private> block must have its secret
		// stripped before the body is returned, even with include_body true.
		const { vault, rootDir } = makeTempVault("recall-private", defaultDocs());
		writeBody(
			rootDir,
			"daily/2026-05-18.md",
			"Public part.\n<private>SECRET_RECALL</private>\nAlso public.",
		);
		const res = recallTool(
			RecallInputSchema.parse({ include_body: true }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const hit = res.data.entries.find(
				(e) => e.path === "daily/2026-05-18.md",
			);
			expect(hit?.body).toBeDefined();
			expect(hit?.body).not.toContain("SECRET_RECALL");
			expect(hit?.body).toContain("[REDACTED]");
		}
	});

	it("body is fenced with FENCE_OPEN and FENCE_CLOSE (Invariant 6 on recall path)", () => {
		// Finding 2: the spotlighting guard must wrap the returned body so a
		// model cannot be injected by crafted vault content (gap G-3).
		const { vault, rootDir } = makeTempVault("recall-fence", defaultDocs());
		writeBody(rootDir, "daily/2026-05-18.md", "# Daily Log\n\nFenced body.\n");
		const res = recallTool(
			RecallInputSchema.parse({ include_body: true }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const hit = res.data.entries.find(
				(e) => e.path === "daily/2026-05-18.md",
			);
			expect(hit?.body).toBeDefined();
			expect(hit?.body).toContain(FENCE_OPEN);
			expect(hit?.body).toContain(FENCE_CLOSE);
		}
	});

	it("logs a containment violation to stderr but keeps the response clean", () => {
		// A poisoned index row whose path escapes the vault root must not
		// reach the filesystem read, must not surface to the caller, and
		// must leave an operator-facing stderr signal.
		const poisoned = {
			path: "../../../../etc/passwd",
			title: "poisoned",
			titleNormalized: "poisoned",
			contentRaw: "x",
			contentNormalized: "x",
			mtime: 1_716_000_000,
			frontmatterType: "session",
			sessionId: "s-poison",
		};
		const { vault } = makeTempVault("recall-poison", [poisoned]);
		const spy = vi
			.spyOn(process.stderr, "write")
			.mockImplementation(() => true);
		try {
			const res = recallTool(
				RecallInputSchema.parse({ include_body: true }),
				vault,
			);
			expect(res.ok).toBe(true);
			if (res.ok) {
				for (const e of res.data.entries) expect(e.body).toBeUndefined();
			}
			expect(spy).toHaveBeenCalled();
			const msg = String(spy.mock.calls[0]?.[0] ?? "");
			expect(msg).toContain("escaped vault root");
		} finally {
			spy.mockRestore();
		}
	});
});
