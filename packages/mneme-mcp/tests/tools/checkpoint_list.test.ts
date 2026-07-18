import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
	CheckpointListInputSchema,
	checkpointListTool,
} from "../../src/tools/checkpoint_list.js";
import {
	WorkingSetLoadInputSchema,
	workingSetLoadTool,
} from "../../src/tools/working_set_load.js";
import { makeTempVault } from "../helpers/vault_fixture.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Write a minimal index.jsonl under <root>/.mneme/checkpoints/. */
function writeIndex(rootDir: string, lines: object[]): void {
	const dir = join(rootDir, ".mneme", "checkpoints");
	mkdirSync(dir, { recursive: true });
	writeFileSync(
		join(dir, "index.jsonl"),
		`${lines.map((line) => JSON.stringify(line)).join("\n")}\n`,
		"utf8",
	);
}

/** Write a checkpoint markdown file under <root>/checkpoints/. */
function writeCheckpointMd(
	rootDir: string,
	filename: string,
	content: string,
): void {
	const dir = join(rootDir, "checkpoints");
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, filename), content, "utf8");
}

const SAMPLE_ANCHOR = "abc123";
const SAMPLE_ANCHOR_2 = "def456";

const SAMPLE_INDEX_LINE = {
	anchor: SAMPLE_ANCHOR,
	id: "cp-001",
	created: "2026-06-14T10:00:00Z",
	session_id: "s-2026-06-14",
	prev_anchor: null,
	path: `checkpoints/2026-06-14-${SAMPLE_ANCHOR}.md`,
	item_count: 3,
	top_salience: 0.92,
	scope: "default",
};

const SAMPLE_MD = `---
type: checkpoint
anchor: ${SAMPLE_ANCHOR}
session_id: s-2026-06-14
created: 2026-06-14T10:00:00Z
scope: default
---

## Core Decisions
- [salience 0.92] Use FTS5 BM25 for retrieval baseline
- [salience 0.75] Turkish casefold normalization required

## Open Questions
- [salience 0.60] Whether to add dense retrieval in Phase 2
`;

// ---------------------------------------------------------------------------
// CheckpointListInputSchema
// ---------------------------------------------------------------------------

describe("CheckpointListInputSchema", () => {
	it("defaults limit to 20", () => {
		const parsed = CheckpointListInputSchema.parse({});
		expect(parsed.limit).toBe(20);
	});

	it("accepts explicit limit", () => {
		const parsed = CheckpointListInputSchema.parse({ limit: 5 });
		expect(parsed.limit).toBe(5);
	});

	it("rejects limit above 200", () => {
		expect(() => CheckpointListInputSchema.parse({ limit: 201 })).toThrow();
	});

	it("rejects non-integer limit", () => {
		expect(() => CheckpointListInputSchema.parse({ limit: 1.5 })).toThrow();
	});

	it("accepts concrete and explicit wildcard scopes", () => {
		expect(CheckpointListInputSchema.parse({ scope: "clinical" }).scope).toBe(
			"clinical",
		);
		expect(CheckpointListInputSchema.parse({ scope: "*" }).scope).toBe("*");
	});

	it("rejects ambiguous or unbounded scope values", () => {
		expect(() => CheckpointListInputSchema.parse({ scope: "" })).toThrow();
		expect(() =>
			CheckpointListInputSchema.parse({ scope: " clinical " }),
		).toThrow();
		expect(() =>
			CheckpointListInputSchema.parse({ scope: "clinical*research" }),
		).toThrow();
		expect(() =>
			CheckpointListInputSchema.parse({ scope: "x".repeat(257) }),
		).toThrow();
	});

	it("accepts and strips unknown input fields for backward compatibility", () => {
		const parsed = CheckpointListInputSchema.parse({
			limit: 5,
			scope: "clinical",
			future_option: true,
		});
		expect(parsed).toEqual({ limit: 5, scope: "clinical" });
	});
});

// ---------------------------------------------------------------------------
// checkpointListTool runtime
// ---------------------------------------------------------------------------

describe("checkpointListTool runtime", () => {
	it("returns empty list when index does not exist", () => {
		const { vault } = makeTempVault("chkl-noindex");
		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries).toHaveLength(0);
			expect(res.data.total_in_index).toBe(0);
		}
	});

	it("returns the single entry from a one-line index", () => {
		const { vault, rootDir } = makeTempVault("chkl-one");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);

		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries).toHaveLength(1);
			expect(res.data.total_in_index).toBe(1);
			const e = res.data.entries[0];
			expect(e?.anchor).toBe(SAMPLE_ANCHOR);
			expect(e?.session_id).toBe("s-2026-06-14");
			expect(e?.item_count).toBe(3);
			expect(e?.top_salience).toBe(0.92);
		}
	});

	it("returns entries newest-first (last appended = first returned)", () => {
		const { vault, rootDir } = makeTempVault("chkl-order");
		const line1 = {
			...SAMPLE_INDEX_LINE,
			anchor: SAMPLE_ANCHOR,
			created: "2026-06-14T08:00:00Z",
		};
		const line2 = {
			...SAMPLE_INDEX_LINE,
			anchor: SAMPLE_ANCHOR_2,
			created: "2026-06-14T10:00:00Z",
			path: `checkpoints/2026-06-14-${SAMPLE_ANCHOR_2}.md`,
		};
		writeIndex(rootDir, [line1, line2]);

		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);
		expect(res.ok).toBe(true);
		if (res.ok) {
			// newest-first: line2 (def456) should be first
			expect(res.data.entries[0]?.anchor).toBe(SAMPLE_ANCHOR_2);
			expect(res.data.entries[1]?.anchor).toBe(SAMPLE_ANCHOR);
		}
	});

	it("respects limit parameter", () => {
		const { vault, rootDir } = makeTempVault("chkl-limit");
		const lines = Array.from({ length: 10 }, (_, i) => ({
			...SAMPLE_INDEX_LINE,
			anchor: `anchor${i}`,
			path: `checkpoints/2026-06-14-anchor${i}.md`,
		}));
		writeIndex(rootDir, lines);

		const res = checkpointListTool(
			CheckpointListInputSchema.parse({ limit: 3 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries).toHaveLength(3);
			expect(res.data.total_in_index).toBe(10);
		}
	});

	it("skips malformed JSONL lines gracefully", () => {
		const { vault, rootDir } = makeTempVault("chkl-malformed");
		const dir = join(rootDir, ".mneme", "checkpoints");
		mkdirSync(dir, { recursive: true });
		writeFileSync(
			join(dir, "index.jsonl"),
			[
				"not valid json",
				JSON.stringify(SAMPLE_INDEX_LINE),
				'{"missing_anchor": true}',
			].join("\n"),
			"utf8",
		);

		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);
		expect(res.ok).toBe(true);
		if (res.ok) {
			// Only the valid line with anchor should be returned.
			expect(res.data.entries).toHaveLength(1);
			expect(res.data.entries[0]?.anchor).toBe(SAMPLE_ANCHOR);
		}
	});

	it("uses the configured default scope when scope is omitted", () => {
		const { vault, rootDir } = makeTempVault("chkl-default-scope");
		vi.spyOn(vault, "defaultScope").mockReturnValue("clinical");
		writeIndex(rootDir, [
			{ ...SAMPLE_INDEX_LINE, anchor: "default-entry" },
			{ ...SAMPLE_INDEX_LINE, anchor: "clinical-entry", scope: "clinical" },
		]);

		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);

		expect(res.ok).toBe(true);
		if (res.ok) {
			expect(res.data.entries.map((entry) => entry.anchor)).toEqual([
				"clinical-entry",
			]);
			expect(res.data.total_in_index).toBe(1);
		}
	});

	it("requires an explicit wildcard for cross-scope discovery", () => {
		const { vault, rootDir } = makeTempVault("chkl-wildcard");
		writeIndex(rootDir, [
			{ ...SAMPLE_INDEX_LINE, anchor: "default-entry" },
			{ ...SAMPLE_INDEX_LINE, anchor: "clinical-entry", scope: "clinical" },
			{ ...SAMPLE_INDEX_LINE, anchor: "research-entry", scope: "research" },
		]);

		const defaults = checkpointListTool(
			CheckpointListInputSchema.parse({}),
			vault,
		);
		const all = checkpointListTool(
			CheckpointListInputSchema.parse({ scope: "*" }),
			vault,
		);

		expect(
			defaults.ok && defaults.data.entries.map((entry) => entry.anchor),
		).toEqual(["default-entry"]);
		expect(all.ok && all.data.entries.map((entry) => entry.anchor)).toEqual([
			"research-entry",
			"clinical-entry",
			"default-entry",
		]);
	});

	it("fails closed for legacy index records without scope", () => {
		const { vault, rootDir } = makeTempVault("chkl-legacy-scope");
		const { scope: _scope, ...legacy } = SAMPLE_INDEX_LINE;
		writeIndex(rootDir, [legacy, { ...SAMPLE_INDEX_LINE, anchor: "scoped" }]);

		for (const scope of ["default", "*"]) {
			const res = checkpointListTool(
				CheckpointListInputSchema.parse({ scope }),
				vault,
			);
			expect(res.ok && res.data.entries.map((entry) => entry.anchor)).toEqual([
				"scoped",
			]);
		}
	});

	it("rejects an oversized checkpoint index before parsing", () => {
		const { vault, rootDir } = makeTempVault("chkl-oversized-index");
		const dir = join(rootDir, ".mneme", "checkpoints");
		mkdirSync(dir, { recursive: true });
		writeFileSync(join(dir, "index.jsonl"), Buffer.alloc(16 * 1024 * 1024 + 1));

		const res = checkpointListTool(CheckpointListInputSchema.parse({}), vault);

		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe("IO_ERROR");
	});
});

// ---------------------------------------------------------------------------
// WorkingSetLoadInputSchema
// ---------------------------------------------------------------------------

describe("WorkingSetLoadInputSchema", () => {
	it("requires anchor", () => {
		expect(() => WorkingSetLoadInputSchema.parse({})).toThrow();
	});

	it("accepts anchor only", () => {
		const parsed = WorkingSetLoadInputSchema.parse({ anchor: "abc123" });
		expect(parsed.anchor).toBe("abc123");
		expect(parsed.top_k).toBeUndefined();
	});

	it("accepts anchor with top_k", () => {
		const parsed = WorkingSetLoadInputSchema.parse({
			anchor: "abc123",
			top_k: 5,
		});
		expect(parsed.top_k).toBe(5);
	});

	it("accepts a scope and strips unknown input fields", () => {
		const parsed = WorkingSetLoadInputSchema.parse({
			anchor: "abc123",
			scope: "clinical",
			future_option: "ignored",
		});
		expect(parsed).toEqual({ anchor: "abc123", scope: "clinical" });
	});

	it("rejects top_k above 500", () => {
		expect(() =>
			WorkingSetLoadInputSchema.parse({ anchor: "x", top_k: 501 }),
		).toThrow();
	});

	it("rejects unsafe anchors and ambiguous scopes", () => {
		expect(() =>
			WorkingSetLoadInputSchema.parse({ anchor: "../escape" }),
		).toThrow();
		expect(() =>
			WorkingSetLoadInputSchema.parse({
				anchor: SAMPLE_ANCHOR,
				scope: "clinical*research",
			}),
		).toThrow();
	});
});

// ---------------------------------------------------------------------------
// workingSetLoadTool runtime
// ---------------------------------------------------------------------------

describe("workingSetLoadTool runtime", () => {
	it("returns not_found for unknown anchor (no index, no file)", () => {
		const { vault } = makeTempVault("wsl-notfound");
		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: "nope" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { found: false; reason: string };
			expect(data.found).toBe(false);
			expect(data.reason).not.toContain("nope");
			expect(data.reason).toContain("requested scope");
		}
	});

	it("loads checkpoint via index lookup", () => {
		const { vault, rootDir } = makeTempVault("wsl-index");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as {
				anchor: string;
				items: Array<{ salience: number; text: string; section: string }>;
				total_items: number;
				truncated: boolean;
				frontmatter: Record<string, string>;
			};
			expect(data.anchor).toBe(SAMPLE_ANCHOR);
			expect(data.total_items).toBe(3);
			expect(data.truncated).toBe(false);
			// Items should be sorted by descending salience.
			expect(data.items[0]?.salience).toBe(0.92);
			expect(data.items[1]?.salience).toBe(0.75);
			expect(data.items[2]?.salience).toBe(0.6);
		}
	});

	it("falls back to glob when anchor not in index", () => {
		const { vault, rootDir } = makeTempVault("wsl-glob");
		// No index written — pure glob fallback.
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { anchor: string; total_items: number };
			expect(data.anchor).toBe(SAMPLE_ANCHOR);
			expect(data.total_items).toBe(3);
		}
	});

	it("respects top_k and sets truncated flag", () => {
		const { vault, rootDir } = makeTempVault("wsl-topk");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR, top_k: 2 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as {
				items: unknown[];
				total_items: number;
				truncated: boolean;
			};
			expect(data.items).toHaveLength(2);
			expect(data.total_items).toBe(3);
			expect(data.truncated).toBe(true);
		}
	});

	it("returns all items when top_k equals item count (not truncated)", () => {
		const { vault, rootDir } = makeTempVault("wsl-topk-exact");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR, top_k: 3 }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { items: unknown[]; truncated: boolean };
			expect(data.items).toHaveLength(3);
			expect(data.truncated).toBe(false);
		}
	});

	it("parses frontmatter correctly", () => {
		const { vault, rootDir } = makeTempVault("wsl-frontmatter");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as {
				frontmatter: Record<string, string>;
			};
			expect(data.frontmatter.type).toBe("checkpoint");
			expect(data.frontmatter.anchor).toBe(SAMPLE_ANCHOR);
			expect(data.frontmatter.session_id).toBe("s-2026-06-14");
		}
	});

	it("assigns section names to items correctly", () => {
		const { vault, rootDir } = makeTempVault("wsl-sections");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as {
				items: Array<{ section: string; salience: number; text: string }>;
			};
			// After sorting by descending salience: 0.92, 0.75, 0.60
			// 0.92 and 0.75 are under "Core Decisions", 0.60 under "Open Questions"
			const bySection = Object.fromEntries(
				data.items.map((item) => [item.salience.toFixed(2), item.section]),
			);
			expect(bySection["0.92"]).toBe("Core Decisions");
			expect(bySection["0.75"]).toBe("Core Decisions");
			expect(bySection["0.60"]).toBe("Open Questions");
		}
	});

	it("returns not_found when anchor in index but md file missing", () => {
		const { vault, rootDir } = makeTempVault("wsl-missingfile");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		// Do NOT write the checkpoint .md file.

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { found: false; reason: string };
			expect(data.found).toBe(false);
		}
	});

	it("uses the configured default scope when scope is omitted", () => {
		const { vault, rootDir } = makeTempVault("wsl-default-scope");
		vi.spyOn(vault, "defaultScope").mockReturnValue("clinical");
		writeIndex(rootDir, [{ ...SAMPLE_INDEX_LINE, scope: "clinical" }]);
		writeCheckpointMd(
			rootDir,
			`2026-06-14-${SAMPLE_ANCHOR}.md`,
			SAMPLE_MD.replace("scope: default", "scope: clinical"),
		);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);

		expect(res.ok && (res.data as { scope?: string }).scope).toBe("clinical");
	});

	it("loads cross-scope data only for an explicit wildcard", () => {
		const { vault, rootDir } = makeTempVault("wsl-wildcard");
		writeIndex(rootDir, [{ ...SAMPLE_INDEX_LINE, scope: "clinical" }]);
		writeCheckpointMd(
			rootDir,
			`2026-06-14-${SAMPLE_ANCHOR}.md`,
			SAMPLE_MD.replace("scope: default", "scope: clinical"),
		);

		const hidden = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({
				anchor: SAMPLE_ANCHOR,
				scope: "research",
			}),
			vault,
		);
		const visible = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR, scope: "*" }),
			vault,
		);

		expect(hidden.ok && (hidden.data as { found?: boolean }).found).toBe(false);
		expect(visible.ok && (visible.data as { scope?: string }).scope).toBe(
			"clinical",
		);
	});

	it("uses the same neutral not-found reason for unknown and hidden anchors", () => {
		const { vault, rootDir } = makeTempVault("wsl-neutral-not-found");
		writeIndex(rootDir, [{ ...SAMPLE_INDEX_LINE, scope: "clinical" }]);
		writeCheckpointMd(
			rootDir,
			`2026-06-14-${SAMPLE_ANCHOR}.md`,
			SAMPLE_MD.replace("scope: default", "scope: clinical"),
		);

		const hidden = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({
				anchor: SAMPLE_ANCHOR,
				scope: "research",
			}),
			vault,
		);
		const unknown = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({
				anchor: "unknown",
				scope: "research",
			}),
			vault,
		);

		expect(hidden.ok && (hidden.data as { reason?: string }).reason).toBe(
			unknown.ok && (unknown.data as { reason?: string }).reason,
		);
	});

	it("fails closed for legacy index records even with wildcard scope", () => {
		const { vault, rootDir } = makeTempVault("wsl-legacy-index");
		const { scope: _scope, ...legacy } = SAMPLE_INDEX_LINE;
		writeIndex(rootDir, [legacy]);
		writeCheckpointMd(rootDir, `2026-06-14-${SAMPLE_ANCHOR}.md`, SAMPLE_MD);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR, scope: "*" }),
			vault,
		);

		expect(res.ok && (res.data as { found?: boolean }).found).toBe(false);
	});

	it("fails closed when index and Markdown scopes disagree", () => {
		const { vault, rootDir } = makeTempVault("wsl-scope-mismatch");
		writeIndex(rootDir, [{ ...SAMPLE_INDEX_LINE, scope: "clinical" }]);
		writeCheckpointMd(
			rootDir,
			`2026-06-14-${SAMPLE_ANCHOR}.md`,
			SAMPLE_MD.replace("scope: default", "scope: research"),
		);

		for (const scope of ["research", "*"]) {
			const res = workingSetLoadTool(
				WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR, scope }),
				vault,
			);
			expect(res.ok && (res.data as { found?: boolean }).found).toBe(false);
		}
	});

	it("rejects an oversized checkpoint file before parsing", () => {
		const { vault, rootDir } = makeTempVault("wsl-oversized-checkpoint");
		writeIndex(rootDir, [SAMPLE_INDEX_LINE]);
		const checkpointsDir = join(rootDir, "checkpoints");
		mkdirSync(checkpointsDir, { recursive: true });
		writeFileSync(
			join(checkpointsDir, `2026-06-14-${SAMPLE_ANCHOR}.md`),
			Buffer.alloc(4 * 1024 * 1024 + 1),
		);

		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: SAMPLE_ANCHOR }),
			vault,
		);

		expect(res.ok).toBe(false);
		if (!res.ok) expect(res.error.code).toBe("IO_ERROR");
	});
});
