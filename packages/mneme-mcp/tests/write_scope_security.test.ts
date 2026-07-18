import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { WriteInputSchema, writeTool } from "../src/tools/write.js";
import { makeTempVault } from "./helpers/vault_fixture.js";

describe("write scope security", () => {
	it("rejects wildcard scope at schema and frontmatter boundaries", () => {
		expect(
			WriteInputSchema.safeParse({
				path: "notes/a.md",
				section: "A",
				content: "body",
				scope: "*",
			}).success,
		).toBe(false);
		const { vault, rootDir } = makeTempVault("write-wildcard", []);
		const result = writeTool(
			WriteInputSchema.parse({
				path: "notes/a.md",
				section: "A",
				content: "body",
				frontmatter: { scope: "*" },
			}),
			vault,
		);
		expect(result.ok).toBe(false);
		expect(existsSync(join(rootDir, "notes", "a.md"))).toBe(false);
	});

	it("stamps and reports a concrete scope for new files", () => {
		const { vault, rootDir } = makeTempVault("write-concrete", []);
		const result = writeTool(
			WriteInputSchema.parse({
				path: "notes/a.md",
				section: "A",
				content: "body",
				scope: "clinical",
			}),
			vault,
		);
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.data.scope).toBe("clinical");
		expect(readFileSync(join(rootDir, "notes", "a.md"), "utf8")).toContain(
			'scope: "clinical"',
		);
	});

	it("refuses an existing target outside the requested scope", () => {
		const { vault, rootDir } = makeTempVault("write-cross-scope", []);
		const target = join(rootDir, "notes.md");
		writeFileSync(
			target,
			'---\nscope: "clinical"\n---\n\n## Existing\n\nold\n',
		);
		const refused = writeTool(
			WriteInputSchema.parse({
				path: "notes.md",
				section: "New",
				content: "new",
			}),
			vault,
		);
		expect(refused.ok).toBe(false);
		expect(readFileSync(target, "utf8")).not.toContain("## New");

		const accepted = writeTool(
			WriteInputSchema.parse({
				path: "notes.md",
				section: "New",
				content: "new",
				scope: "clinical",
			}),
			vault,
		);
		expect(accepted.ok).toBe(true);
	});

	it("rejects conflicting explicit and frontmatter scopes", () => {
		const { vault } = makeTempVault("write-scope-conflict", []);
		const result = writeTool(
			WriteInputSchema.parse({
				path: "notes/a.md",
				section: "A",
				content: "body",
				scope: "clinical",
				frontmatter: { scope: "research" },
			}),
			vault,
		);
		expect(result.ok).toBe(false);
	});
});
