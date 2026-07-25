import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
	type WriteInput,
	WriteInputSchema,
	writeTool,
} from "../../src/tools/write.js";
import { VaultConfig } from "../../src/vault/config.js";

function freshVault(prefix: string): { root: string; vault: VaultConfig } {
	const root = mkdtempSync(join(tmpdir(), `mneme-write-edge-${prefix}-`));
	return { root, vault: VaultConfig.fromPath(root) };
}

describe("writeTool defensive branches", () => {
	it("preserves finite numeric frontmatter values", () => {
		const { root, vault } = freshVault("number");

		const result = writeTool(
			WriteInputSchema.parse({
				path: "number.md",
				section: "Metrics",
				content: "Measured.",
				frontmatter: { schema_version: 2, confidence: 0.75 },
			}),
			vault,
		);

		expect(result.ok).toBe(true);
		const written = readFileSync(join(root, "number.md"), "utf8");
		expect(written).toContain("schema_version: 2");
		expect(written).toContain("confidence: 0.75");
	});

	it("writes into an existing empty file without inventing frontmatter", () => {
		const { root, vault } = freshVault("empty");
		const target = join(root, "empty.md");
		writeFileSync(target, "", "utf8");

		const result = writeTool(
			WriteInputSchema.parse({
				path: "empty.md",
				section: "First",
				content: "Body.",
			}),
			vault,
		);

		expect(result.ok).toBe(true);
		if (result.ok) expect(result.data.created_new_file).toBe(false);
		expect(readFileSync(target, "utf8")).toBe("## First\n\nBody.\n");
	});

	it("replaces only the selected section before the next H2 boundary", () => {
		const { root, vault } = freshVault("bounded-replace");
		const target = join(root, "sections.md");
		writeFileSync(
			target,
			"## Target\n\nOld body.\n\n## Preserve\n\nKeep this.\n",
			"utf8",
		);

		const result = writeTool(
			WriteInputSchema.parse({
				path: "sections.md",
				section: "Target",
				content: "New body.",
				replace: true,
			}),
			vault,
		);

		expect(result.ok).toBe(true);
		const written = readFileSync(target, "utf8");
		expect(written).toContain("## Target\n\nNew body.");
		expect(written).toContain("## Preserve\n\nKeep this.");
		expect(written).not.toContain("Old body.");
	});

	it("rejects unsafe frontmatter keys before writing", () => {
		const { vault } = freshVault("unsafe-key");
		const args = WriteInputSchema.parse({
			path: "unsafe-key.md",
			section: "Notes",
			content: "Body.",
			frontmatter: { "unsafe:key": "value" },
		});

		expect(() => writeTool(args, vault)).toThrow("Unsafe frontmatter key");
	});

	it("rejects control characters in frontmatter values", () => {
		const { vault } = freshVault("control-char");
		const args = WriteInputSchema.parse({
			path: "control.md",
			section: "Notes",
			content: "Body.",
			frontmatter: { label: "safe\u0000unsafe" },
		});

		expect(() => writeTool(args, vault)).toThrow("Control character");
	});

	it("rejects non-finite numbers even when called without schema parsing", () => {
		const { vault } = freshVault("non-finite");
		const args: WriteInput = {
			path: "non-finite.md",
			section: "Metrics",
			content: "Body.",
			replace: false,
			frontmatter: { score: Number.NaN },
		};

		expect(() => writeTool(args, vault)).toThrow(
			"Non-finite frontmatter number",
		);
	});
});
