import { AjvJsonSchemaValidator } from "@modelcontextprotocol/sdk/validation/ajv";
import { describe, expect, it } from "vitest";
import {
	publicToolDefinitions,
	TOOL_DEFINITIONS,
	toMcpInputSchema,
} from "../src/tool_registry.js";
import { CheckpointListInputSchema } from "../src/tools/checkpoint_list.js";
import {
	CANONICAL_MEMORY_TYPES,
	SearchInputSchema,
} from "../src/tools/search.js";
import { WorkingSetLoadInputSchema } from "../src/tools/working_set_load.js";

interface JsonSchemaObject {
	type?: string;
	properties?: Record<string, JsonSchemaObject>;
	required?: string[];
	enum?: unknown[];
	default?: unknown;
	maximum?: number;
	maxLength?: number;
	pattern?: string;
	description?: string;
	$schema?: string;
	additionalProperties?: boolean;
	items?: JsonSchemaObject | JsonSchemaObject[];
}

function schemaFor(name: string): JsonSchemaObject {
	const tool = publicToolDefinitions().find(
		(candidate) => candidate.name === name,
	);
	expect(tool, `missing public schema for ${name}`).toBeDefined();
	return tool?.inputSchema as JsonSchemaObject;
}

function property(schema: JsonSchemaObject, name: string): JsonSchemaObject {
	const value = schema.properties?.[name];
	expect(value, `missing property ${name}`).toBeDefined();
	return value ?? {};
}

describe("MCP tool schema registry", () => {
	it("uses one runtime schema authority for all ten public tools", () => {
		const publicTools = publicToolDefinitions();
		expect(publicTools).toHaveLength(10);
		expect(publicTools.map((tool) => tool.name)).toEqual(
			TOOL_DEFINITIONS.map((tool) => tool.name),
		);
		for (const tool of publicTools) {
			expect(tool.inputSchema.type).toBe("object");
			expect(tool.description.length).toBeGreaterThan(20);
		}
	});

	it("advertises scope for every scope-aware tool", () => {
		const scopeAware = [
			"mneme_search",
			"mneme_recall",
			"mneme_summarize",
			"mneme_timeline",
			"mneme_prime",
			"mneme_propose",
			"mneme_checkpoint_list",
			"mneme_working_set_load",
		];
		for (const name of scopeAware) {
			const scope = property(schemaFor(name), "scope");
			expect(scope.type).toBe("string");
			expect(scope.maxLength).toBe(256);
			expect(scope.description).toContain("scope");
		}
	});

	it("advertises all canonical memory types accepted by runtime search", () => {
		const filters = property(schemaFor("mneme_search"), "filters");
		const memoryType = property(filters, "type");
		expect(memoryType.enum).toEqual([...CANONICAL_MEMORY_TYPES]);
	});

	it("preserves runtime defaults and numeric limits", () => {
		const search = schemaFor("mneme_search");
		expect(property(search, "top_k")).toMatchObject({
			type: "integer",
			default: 10,
			maximum: 50,
		});
		expect(property(search, "min_query_length")).toMatchObject({
			type: "integer",
			default: 0,
		});

		const prime = schemaFor("mneme_prime");
		expect(property(prime, "budget_tokens")).toMatchObject({
			type: "integer",
			default: 4000,
			maximum: 20000,
		});
	});

	it("preserves date validation in nested generated schemas", () => {
		const filters = property(schemaFor("mneme_search"), "filters");
		expect(property(filters, "date_from").pattern).toBe(
			"^\\d{4}-\\d{2}-\\d{2}$",
		);
		expect(property(filters, "date_to").pattern).toBe("^\\d{4}-\\d{2}-\\d{2}$");
	});

	it("generates the public contract directly from the supplied Zod schema", () => {
		expect(toMcpInputSchema(SearchInputSchema)).toEqual(
			schemaFor("mneme_search"),
		);
	});

	it("advertises Draft 7 schemas that match CCE runtime validation", () => {
		const provider = new AjvJsonSchemaValidator();
		const cases = [
			{
				name: "mneme_checkpoint_list",
				runtime: CheckpointListInputSchema,
				values: [
					{},
					{ limit: 5, scope: "clinical", future_option: true },
					{ scope: "*" },
					{ limit: 201 },
					{ scope: " clinical " },
					{ scope: "clinical*research" },
				],
			},
			{
				name: "mneme_working_set_load",
				runtime: WorkingSetLoadInputSchema,
				values: [
					{ anchor: "abc123" },
					{ anchor: "abc123", scope: "*", future_option: true },
					{},
					{ anchor: "../escape" },
					{ anchor: "abc123", top_k: 501 },
					{ anchor: "abc123", scope: "research*clinical" },
				],
			},
		] as const;

		for (const testCase of cases) {
			const schema = schemaFor(testCase.name);
			expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
			const validate = provider.getValidator(schema as never);
			for (const value of testCase.values) {
				expect(
					validate(value).valid,
					`${testCase.name}: ${JSON.stringify(value)}`,
				).toBe(testCase.runtime.safeParse(value).success);
			}
		}
	});

	it("keeps unknown CCE input fields backward compatible", () => {
		for (const [name, runtime, value] of [
			[
				"mneme_checkpoint_list",
				CheckpointListInputSchema,
				{ scope: "clinical", future_option: true },
			],
			[
				"mneme_working_set_load",
				WorkingSetLoadInputSchema,
				{ anchor: "abc123", scope: "clinical", future_option: true },
			],
		] as const) {
			const schema = schemaFor(name);
			const validate = new AjvJsonSchemaValidator().getValidator(
				schema as never,
			);
			expect(validate(value).valid).toBe(true);
			const parsed = runtime.parse(value);
			expect(parsed).not.toHaveProperty("future_option");
			expect(schema.additionalProperties).not.toBe(false);
		}
	});
});
