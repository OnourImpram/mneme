import { describe, expect, it } from "vitest";
import {
  publicToolDefinitions,
  TOOL_DEFINITIONS,
  toMcpInputSchema,
} from "../src/tool_registry.js";
import {
  CANONICAL_MEMORY_TYPES,
  SearchInputSchema,
} from "../src/tools/search.js";

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
  items?: JsonSchemaObject | JsonSchemaObject[];
}

function schemaFor(name: string): JsonSchemaObject {
  const tool = publicToolDefinitions().find((candidate) => candidate.name === name);
  expect(tool, `missing public schema for ${name}`).toBeDefined();
  return tool?.inputSchema as JsonSchemaObject;
}

function property(schema: JsonSchemaObject, name: string): JsonSchemaObject {
  const value = schema.properties?.[name];
  expect(value, `missing property ${name}`).toBeDefined();
  return value ?? {};
}

describe("MCP tool schema registry", () => {
  it("uses one runtime schema authority for all nine public tools", () => {
    const publicTools = publicToolDefinitions();
    expect(publicTools).toHaveLength(9);
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
    expect(property(filters, "date_to").pattern).toBe(
      "^\\d{4}-\\d{2}-\\d{2}$",
    );
  });

  it("generates the public contract directly from the supplied Zod schema", () => {
    expect(toMcpInputSchema(SearchInputSchema)).toEqual(
      schemaFor("mneme_search"),
    );
  });
});
