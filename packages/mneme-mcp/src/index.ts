#!/usr/bin/env node
/**
 * mneme-mcp — MCP server for vault-native memory in Claude Code.
 *
 * Seven tools over stdio transport, all prefixed `mneme_` to avoid
 * namespace clash with other MCP servers running in the same client:
 *
 *   mneme_search     FTS5 retrieval today, dense search roadmap
 *   mneme_recall     session by id or date range, with optional body
 *   mneme_write      atomic section append/replace with frontmatter
 *   mneme_summarize  topic grouped by directory, KG-enriched when active
 *   mneme_timeline   subject ordered by mtime, KG-enriched when active
 *   mneme_prime      preflight context bundle within token budget
 *   mneme_propose    queue a memory-edit proposal for the policy drain
 *
 * The server resolves a VaultConfig once at startup. Vault root comes
 * from `MNEME_VAULT` or the documented resolution order. Tool
 * handlers receive the same config so all seven agree on paths.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
	CallToolRequestSchema,
	ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { z } from "zod";
import { toMnemeError } from "./errors.js";
import { isToolError } from "./tool_error.js";
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
import { VaultConfig } from "./vault/config.js";

const SERVER_NAME = "mneme-mcp";
const SERVER_VERSION = "3.5.0";

const HELP = `${SERVER_NAME} - MCP server for mneme vault memory

USAGE
  mneme-mcp
  mneme-mcp --version

ENVIRONMENT
  MNEME_VAULT  Vault root override. Without it, mneme walks for a .mneme marker
               and then falls back to ~/mneme-vault.
`;

interface ToolDef {
	name: string;
	description: string;
	inputSchema: Record<string, unknown>;
	zodSchema: z.ZodTypeAny;
	handler: (args: unknown, vault: VaultConfig) => unknown | Promise<unknown>;
}

const TOOLS: ToolDef[] = [
	{
		name: "mneme_search",
		description:
			"Retrieval over the vault. v1.0 ships FTS5 BM25 with " +
			"Turkish casefold normalization. Optional date and frontmatter " +
			"type filters. Returns ranked hits with snippets. " +
			"The cards array (EvidenceCard) is the preferred output and carries " +
			"content_hash, trust, and confidence_label. " +
			"Each card carries a backend field identifying which retrieval leg produced it (fts5, dense, kg). " +
			"The backends_used array lists every backend that returned at least one hit. " +
			"hits is kept for backward compatibility.",
		zodSchema: SearchInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				query: { type: "string", description: "Free-text query." },
				top_k: { type: "integer", default: 10, maximum: 50 },
				filters: {
					type: "object",
					properties: {
						date_from: {
							type: "string",
							pattern: "^\\d{4}-\\d{2}-\\d{2}$",
							description: "YYYY-MM-DD inclusive lower bound.",
						},
						date_to: {
							type: "string",
							pattern: "^\\d{4}-\\d{2}-\\d{2}$",
							description: "YYYY-MM-DD inclusive upper bound.",
						},
						type: {
							type: "string",
							enum: ["session", "topic", "reference"],
						},
					},
				},
				min_query_length: { type: "integer", default: 0 },
			},
			required: ["query"],
		},
		handler: (args, vault) => searchTool(SearchInputSchema.parse(args), vault),
	},
	{
		name: "mneme_recall",
		description:
			"Retrieve documents by session_id or date range. Returns paths, " +
			"titles, mtimes, and (when include_body) the markdown body of each match.",
		zodSchema: RecallInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				session_id: { type: "string" },
				date_from: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
				date_to: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
				top_n: { type: "integer", default: 10, maximum: 50 },
				include_body: { type: "boolean", default: true },
			},
		},
		handler: (args, vault) => recallTool(RecallInputSchema.parse(args), vault),
	},
	{
		name: "mneme_write",
		description:
			"Atomically append or replace a markdown section in a vault file. " +
			"Enforces assertWithinVault path containment. Optional frontmatter " +
			"applies to newly created files only.",
		zodSchema: WriteInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				path: { type: "string", description: "Path relative to vault root." },
				section: {
					type: "string",
					description: "H2 heading text without `## `.",
				},
				content: {
					type: "string",
					description: "Body content for the section.",
				},
				replace: { type: "boolean", default: false },
				frontmatter: {
					type: "object",
					additionalProperties: { type: ["string", "number"] },
				},
			},
			required: ["path", "section", "content"],
		},
		handler: (args, vault) => writeTool(WriteInputSchema.parse(args), vault),
	},
	{
		name: "mneme_summarize",
		description:
			"Topic summary grouped by directory. v1.0 uses FTS5 and can add " +
			"Graphiti fields when full-profile KG state is active.",
		zodSchema: SummarizeInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				topic: { type: "string" },
				date_range: {
					type: "array",
					items: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
					minItems: 2,
					maxItems: 2,
				},
				top_k: { type: "integer", default: 20, maximum: 50 },
			},
			required: ["topic"],
		},
		handler: (args, vault) =>
			summarizeTool(SummarizeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_timeline",
		description:
			"Temporal-ordered references for a subject. v1.0 returns FTS5 hits " +
			"sorted by mtime ascending and can add bi-temporal Graphiti facts " +
			"when full-profile KG state is active.",
		zodSchema: TimelineInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				subject: { type: "string" },
				valid_from: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
				valid_to: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
				as_of: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
				top_k: { type: "integer", default: 25, maximum: 100 },
			},
			required: ["subject"],
		},
		handler: (args, vault) =>
			timelineTool(TimelineInputSchema.parse(args), vault),
	},
	{
		name: "mneme_prime",
		description:
			"Preflight context bundle for a new session. Combines recent " +
			"session-typed docs and topic-relevant matches inside a token " +
			"budget. v1.0 uses the `full` injection format; Phase F.5 adds " +
			"the keypoints/ref Adaptive Context Layer. " +
			"Pass session_id (from CLAUDE_SESSION_ID) to activate per-session " +
			"injection deduplication and progressive format selection " +
			"(full → keypoints → ref as context fills).",
		zodSchema: PrimeInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				task_description: { type: "string" },
				budget_tokens: { type: "integer", default: 4000, maximum: 20000 },
				recent_session_count: { type: "integer", default: 3, maximum: 20 },
				topic_doc_count: { type: "integer", default: 5, maximum: 20 },
				session_id: {
					type: "string",
					description:
						"Caller session identifier (e.g. CLAUDE_SESSION_ID). Enables per-session injection deduplication and progressive format selection (full to keypoints to ref).",
				},
			},
			required: ["task_description"],
		},
		handler: (args, vault) => primeTool(PrimeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_propose",
		description:
			"Queue a memory-edit proposal (create/update/delete) for the " +
			"policy drain. The server never applies the edit directly: the " +
			"proposal is staged under .mneme/proposals/ and applied by the " +
			"policy engine — autonomously when the operator's policy.json " +
			"allows the declared edit_class on an ephemeral edit, otherwise " +
			"held for human approval. Durable categories (identity, " +
			"preference, clinical, legal, financial) always require human " +
			"approval. Content is redacted before it is queued.",
		zodSchema: ProposeInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				action: { type: "string", enum: ["create", "update", "delete"] },
				path: {
					type: "string",
					description: "Target path relative to the vault root.",
				},
				content: {
					type: "string",
					description: "Proposed full file content (ignored for delete).",
				},
				category: {
					type: "string",
					enum: [
						"ephemeral",
						"identity",
						"preference",
						"clinical",
						"legal",
						"financial",
					],
					default: "ephemeral",
				},
				edit_class: {
					type: "string",
					enum: [
						"dedup-merge",
						"typo-fix",
						"tag-normalize",
						"supersede-link",
						"stale-archive",
					],
					description:
						"Low-risk mechanical edit class for autonomous apply eligibility.",
				},
			},
			required: ["action", "path"],
		},
		handler: (args, vault) =>
			proposeTool(ProposeInputSchema.parse(args), vault),
	},
	{
		name: "mneme_checkpoint_list",
		description:
			"List recent Context Continuity Engine checkpoints from the " +
			"append-only index at <vault>/.mneme/checkpoints/index.jsonl. " +
			"Returns up to `limit` entries newest-first, each carrying anchor, " +
			"created timestamp, session_id, item_count, and top_salience. " +
			"Use this to discover available anchors before calling " +
			"mneme_working_set_load. Missing index file returns an empty list.",
		zodSchema: CheckpointListInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				limit: {
					type: "integer",
					default: 20,
					maximum: 200,
					description: "Maximum number of checkpoint entries to return.",
				},
			},
		},
		handler: (args, vault) =>
			checkpointListTool(CheckpointListInputSchema.parse(args), vault),
	},
	{
		name: "mneme_working_set_load",
		description:
			"Load the working-set items from a Context Continuity Engine " +
			"checkpoint for cross-agent handoff or JIT context injection. " +
			"Resolves the anchor via the checkpoint index, reads the checkpoint " +
			"markdown, parses salience bullets, and returns items sorted by " +
			"descending salience. Pass top_k to limit retrieval to the highest- " +
			"salience items. Unknown anchor returns a not-found result, not an error.",
		zodSchema: WorkingSetLoadInputSchema,
		inputSchema: {
			type: "object",
			properties: {
				anchor: {
					type: "string",
					description:
						"Checkpoint anchor string (e.g. 'abc123'). " +
						"Use mneme_checkpoint_list to discover available anchors.",
				},
				top_k: {
					type: "integer",
					maximum: 500,
					description:
						"Return only the top_k items by salience. Omit for all items.",
				},
			},
			required: ["anchor"],
		},
		handler: (args, vault) =>
			workingSetLoadTool(WorkingSetLoadInputSchema.parse(args), vault),
	},
];

async function main(): Promise<void> {
	if (process.argv.includes("--version") || process.argv.includes("-V")) {
		process.stdout.write(`${SERVER_NAME} ${SERVER_VERSION}\n`);
		return;
	}
	if (process.argv.includes("--help") || process.argv.includes("-h")) {
		process.stdout.write(HELP);
		return;
	}

	// When the MCP client disconnects, a final write to the now-closed
	// stdio pipe raises EPIPE. Node escalates an EventEmitter "error" with
	// no listener into a process-crashing throw, so treat a broken stdout
	// pipe as a normal shutdown rather than a fatal error.
	process.stdout.on("error", (err: NodeJS.ErrnoException) => {
		if (err.code === "EPIPE") {
			process.exit(0);
		}
		throw err;
	});

	const vault = VaultConfig.resolve();

	const server = new Server(
		{ name: SERVER_NAME, version: SERVER_VERSION },
		{ capabilities: { tools: {} } },
	);

	server.setRequestHandler(ListToolsRequestSchema, async () => ({
		tools: TOOLS.map((t) => ({
			name: t.name,
			description: t.description,
			inputSchema: t.inputSchema,
		})),
	}));

	server.setRequestHandler(CallToolRequestSchema, async (request) => {
		const { name, arguments: args } = request.params;
		const def = TOOLS.find((t) => t.name === name);
		if (!def) {
			return {
				content: [
					{
						type: "text",
						text: JSON.stringify(
							{
								ok: false,
								error: {
									code: "UNKNOWN_TOOL",
									message: `Unknown tool: ${name}`,
								},
							},
							null,
							2,
						),
					},
				],
				isError: true,
			};
		}
		try {
			const result = await def.handler(args ?? {}, vault);
			const payload = JSON.stringify(result, null, 2);
			return {
				content: [{ type: "text", text: payload }],
				isError: isToolError(result),
			};
		} catch (err) {
			const e = toMnemeError(err);
			return {
				content: [
					{
						type: "text",
						text: JSON.stringify({ ok: false, error: e }, null, 2),
					},
				],
				isError: true,
			};
		}
	});

	const transport = new StdioServerTransport();
	await server.connect(transport);
	console.error(
		`[${SERVER_NAME}] connected v${SERVER_VERSION}, vault=${vault.root}`,
	);
}

main().catch((err: unknown) => {
	console.error("[mneme-mcp] fatal:", err);
	process.exit(1);
});
