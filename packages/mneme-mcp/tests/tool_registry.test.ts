import { describe, expect, it } from "vitest";
import { TOOLS, toMcpInputSchema } from "../src/tool_registry.js";

const EXPECTED_TOOL_NAMES = [
	"mneme_search",
	"mneme_recall",
	"mneme_write",
	"mneme_summarize",
	"mneme_timeline",
	"mneme_prime",
	"mneme_propose",
	"mneme_checkpoint_list",
	"mneme_working_set_load",
] as const;

const SCOPED_TOOL_NAMES = [
	"mneme_search",
	"mneme_recall",
	"mneme_write",
	"mneme_summarize",
	"mneme_timeline",
	"mneme_prime",
	"mneme_propose",
	"mneme_checkpoint_list",
	"mneme_working_set_load",
] as const;

describe("authoritative MCP tool registry", () => {
	it("contains exactly the nine supported public tools", () => {
		expect(TOOLS.map((tool) => tool.name).sort()).toEqual(
			[...EXPECTED_TOOL_NAMES].sort(),
		);
	});

	it("derives every ListTools schema from the runtime Zod schema", () => {
		for (const tool of TOOLS) {
			expect(tool.inputSchema).toEqual(toMcpInputSchema(tool.zodSchema));
		}
	});

	it("matches Zod's unknown-key stripping contract in advertised schemas", () => {
		for (const tool of TOOLS) {
			expect(tool.inputSchema).not.toHaveProperty("$schema");
			expect(tool.inputSchema.additionalProperties).toBe(true);
		}

		const search = TOOLS.find((tool) => tool.name === "mneme_search");
		expect(search).toBeDefined();
		const parsed = search?.zodSchema.parse({
			query: "scope contract",
			unknown_root_key: "accepted then stripped",
			filters: {
				type: "session",
				unknown_filter_key: "accepted then stripped",
			},
		}) as Record<string, unknown>;
		expect(parsed).not.toHaveProperty("unknown_root_key");
		expect(parsed.filters).not.toHaveProperty("unknown_filter_key");

		const properties = search?.inputSchema.properties as Record<
			string,
			Record<string, unknown>
		>;
		expect(properties.filters.additionalProperties).toBe(true);
	});

	it("surfaces scope for every scope-aware tool", () => {
		for (const name of SCOPED_TOOL_NAMES) {
			const tool = TOOLS.find((candidate) => candidate.name === name);
			expect(tool, `missing tool ${name}`).toBeDefined();
			const properties = tool?.inputSchema.properties as
				| Record<string, unknown>
				| undefined;
			expect(properties, `${name} has no properties`).toBeDefined();
			expect(properties).toHaveProperty("scope");
		}
	});

	it("keeps the cross-scope selector explicit for read schemas", () => {
		for (const name of SCOPED_TOOL_NAMES.filter(
			(candidate) => candidate !== "mneme_write" && candidate !== "mneme_propose",
		)) {
			const tool = TOOLS.find((candidate) => candidate.name === name);
			const properties = tool?.inputSchema.properties as Record<
				string,
				Record<string, unknown>
			>;
			const scopeSchema = properties.scope;
			expect(scopeSchema.maxLength).toBe(256);
			expect(String(scopeSchema.description)).toContain("exact literal '*'");
		}
	});
});
