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
	zodSchema: z.ZodTypeAny;
	handler: (args: unknown, vault: VaultConfig) => unknown | Promise<unknown>;
}

/** Build the Draft 7 client contract from the authoritative runtime schema. */
export function toMcpInputSchema(
	schema: z.ZodTypeAny,
): Record<string, unknown> {
	return z.toJSONSchema(schema, {
		target: "draft-07",
		io: "input",
	}) as Record<string, unknown>;
}

export const TOOL_DEFINITIONS: readonly ToolDefinition[] = [
	{
		name: "mneme_search",
		description:
			"FTS5 BM25 retrieval over the vault with Turkish casefold normalization, " +
			"date, memory-type, and scope filters. Returns ranked hits and EvidenceCards " +
			"with content hashes, trust, confidence, and the backend that actually ran. " +
			"The hits array is retained for backward compatibility.",
		zodSchema: SearchInputSchema,
		handler: (args, vault) => searchTool(SearchInputSchema.parse(args), vault),
	},
	{
		name: "mneme_recall",
		description:
			"Retrieve indexed documents by session identifier, date range, and scope. " +
			"Returns paths, titles, modification times, memory types, and optionally " +
			"the full markdown body.",
		zodSchema: RecallInputSchema,
		handler: (args, vault) => recallTool(RecallInputSchema.parse(args), vault),
	},
	{
		name: "mneme_write",
		description:
			"Atomically append or replace a markdown section in a vault file. " +
			"Enforces vault path containment and redacts private spans before storage. " +
			"Optional frontmatter applies to newly created files only.",
		zodSchema: WriteInputSchema,
		handler: (args, vault) => writeTool(WriteInputSchema.parse(args), vault),
	},
	{
		name: "mneme_summarize",
		description:
			"Group FTS5 matches for a topic by vault directory within optional date and " +
			"scope filters. Full-profile Graphiti enrichment is included only when its " +
			"optional local graph integration is configured and available.",
		zodSchema: SummarizeInputSchema,
		handler: (args, vault) =>
			summarizeTool(SummarizeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_timeline",
		description:
			"Return scope-restricted FTS5 references for a subject in chronological " +
			"order. Full-profile Graphiti facts and bi-temporal filtering are included " +
			"only when the optional local graph integration is configured and available.",
		zodSchema: TimelineInputSchema,
		handler: (args, vault) =>
			timelineTool(TimelineInputSchema.parse(args), vault),
	},
	{
		name: "mneme_prime",
		description:
			"Build a token-budgeted preflight context bundle from recent sessions and " +
			"topic-relevant FTS5 matches. A caller session identifier enables " +
			"per-session injection deduplication and progressive full, keypoints, and " +
			"reference formatting.",
		zodSchema: PrimeInputSchema,
		handler: (args, vault) => primeTool(PrimeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_propose",
		description:
			"Queue a redacted memory-edit proposal for the policy drain. The MCP server " +
			"does not apply the edit directly. Operator-authorized low-risk ephemeral " +
			"edits may be applied by the policy engine, while durable categories always " +
			"require human approval.",
		zodSchema: ProposeInputSchema,
		handler: (args, vault) =>
			proposeTool(ProposeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_checkpoint_list",
		description:
			"List recent Context Continuity Engine checkpoints from the active scope, " +
			"newest first. Pass scope='*' explicitly for a cross-scope read. Missing " +
			"checkpoint state returns an empty list.",
		zodSchema: CheckpointListInputSchema,
		handler: (args, vault) =>
			checkpointListTool(CheckpointListInputSchema.parse(args), vault),
	},
	{
		name: "mneme_working_set_load",
		description:
			"Load salience-ranked working-set items from a Context Continuity Engine " +
			"checkpoint in the active scope. Unknown and out-of-scope anchors return " +
			"the same neutral not-found result. Pass scope='*' explicitly for a " +
			"cross-scope read.",
		zodSchema: WorkingSetLoadInputSchema,
		handler: (args, vault) =>
			workingSetLoadTool(WorkingSetLoadInputSchema.parse(args), vault),
	},
];

export interface PublicToolDefinition {
	name: string;
	description: string;
	inputSchema: Record<string, unknown>;
}

export function publicToolDefinitions(): PublicToolDefinition[] {
	return TOOL_DEFINITIONS.map((tool) => ({
		name: tool.name,
		description: tool.description,
		inputSchema: toMcpInputSchema(tool.zodSchema),
	}));
}
