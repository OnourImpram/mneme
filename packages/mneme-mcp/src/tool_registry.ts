/**
 * Authoritative MCP tool registry.
 *
 * Runtime validation and ListTools JSON Schema are both derived from the same
 * Zod schema. This prevents a client-visible argument from drifting away from
 * the handler contract.
 */

import { z } from "zod";
import {
	CheckpointListInputSchema,
	checkpointListTool,
} from "./tools/checkpoint_list.js";
import { PrimeInputSchema, primeTool } from "./tools/prime.js";
import { ProposeInputSchema, proposeTool } from "./tools/propose.js";
import { RecallInputSchema, recallTool } from "./tools/recall.js";
import { SearchInputSchema, searchTool } from "./tools/search.js";
import { SummarizeInputSchema, summarizeTool } from "./tools/summarize.js";
import { TimelineInputSchema, timelineTool } from "./tools/timeline.js";
import {
	WorkingSetLoadInputSchema,
	workingSetLoadTool,
} from "./tools/working_set_load.js";
import { WriteInputSchema, writeTool } from "./tools/write.js";
import type { VaultConfig } from "./vault/config.js";

export interface ToolDefinition {
	name: string;
	description: string;
	inputSchema: Record<string, unknown>;
	zodSchema: z.ZodType;
	handler: (args: unknown, vault: VaultConfig) => unknown | Promise<unknown>;
}

/**
 * Match Zod's default object-input contract in the client-visible JSON Schema.
 *
 * `z.object(...)` accepts unknown properties and strips them unless a schema is
 * explicitly strict. Zod's JSON-Schema converter emits
 * `additionalProperties: false` for those objects, which would tell MCP
 * clients that the same payload is invalid. Mneme does not use strict public
 * input objects, so normalize every generated object node to permit unknown
 * properties while retaining all named-property constraints. Runtime parsing
 * remains authoritative and still strips the unknown values before handlers
 * execute.
 */
function normalizeObjectInputContract(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(normalizeObjectInputContract);
	if (value === null || typeof value !== "object") return value;

	const source = value as Record<string, unknown>;
	const normalized: Record<string, unknown> = {};
	for (const [key, child] of Object.entries(source)) {
		if (key === "$schema") continue;
		normalized[key] = normalizeObjectInputContract(child);
	}
	if (normalized.type === "object" && normalized.additionalProperties === false) {
		normalized.additionalProperties = true;
	}
	return normalized;
}

/** Convert a runtime input schema to the draft understood by MCP clients. */
export function toMcpInputSchema(schema: z.ZodType): Record<string, unknown> {
	const generated = z.toJSONSchema(schema, {
		target: "draft-07",
		io: "input",
	});
	return normalizeObjectInputContract(generated) as Record<string, unknown>;
}

function defineTool(
	name: string,
	description: string,
	zodSchema: z.ZodType,
	handler: ToolDefinition["handler"],
): ToolDefinition {
	return {
		name,
		description,
		zodSchema,
		inputSchema: toMcpInputSchema(zodSchema),
		handler,
	};
}

export const TOOLS: readonly ToolDefinition[] = [
	defineTool(
		"mneme_search",
		"Search the active memory scope with the production MCP FTS5 BM25 path. " +
			"Returns ranked hits and EvidenceCards with truthful backend provenance. " +
			"Pass scope='*' explicitly for a cross-scope read.",
		SearchInputSchema,
		(args, vault) => searchTool(SearchInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_recall",
		"Recall documents by session identifier or date range in the active scope. " +
			"The markdown body is optional. Pass scope='*' explicitly for a cross-scope read.",
		RecallInputSchema,
		(args, vault) => recallTool(RecallInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_write",
		"Atomically append or replace a markdown section in a vault-contained path. " +
			"Durable writes are bound to a concrete isolation scope and audit event.",
		WriteInputSchema,
		(args, vault) => writeTool(WriteInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_summarize",
		"Group FTS5 matches by directory in the active scope. When the optional local " +
			"knowledge-graph profile is active, related Graphiti entities are returned separately.",
		SummarizeInputSchema,
		(args, vault) => summarizeTool(SummarizeInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_timeline",
		"Return FTS5 references ordered by modification time in the active scope. " +
			"When the optional local knowledge graph is active, bi-temporal facts are returned separately.",
		TimelineInputSchema,
		(args, vault) => timelineTool(TimelineInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_prime",
		"Build a redacted, token-budgeted preflight context bundle from recent and " +
			"topic-relevant documents in the active scope.",
		PrimeInputSchema,
		(args, vault) => primeTool(PrimeInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_propose",
		"Queue a redacted create, update, or delete proposal for policy evaluation. " +
			"The MCP server never applies the edit directly.",
		ProposeInputSchema,
		(args, vault) => proposeTool(ProposeInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_checkpoint_list",
		"List recent Context Continuity Engine checkpoints from the active scope. " +
			"Pass scope='*' explicitly for a cross-scope read.",
		CheckpointListInputSchema,
		(args, vault) =>
			checkpointListTool(CheckpointListInputSchema.parse(args), vault),
	),
	defineTool(
		"mneme_working_set_load",
		"Load salience-ranked working-set items from a checkpoint in the active scope. " +
			"Unknown or out-of-scope anchors return a not-found result.",
		WorkingSetLoadInputSchema,
		(args, vault) =>
			workingSetLoadTool(WorkingSetLoadInputSchema.parse(args), vault),
	),
] as const;
