#!/usr/bin/env node
/**
 * mneme-mcp — MCP server for vault-native memory in Claude Code.
 *
 * Nine tools over stdio transport, all prefixed `mneme_` to avoid
 * namespace clash with other MCP servers running in the same client:
 *
 *   mneme_search     FTS5 retrieval with scope and provenance
 *   mneme_recall     session by id or date range, with optional body
 *   mneme_write      atomic section append/replace with frontmatter
 *   mneme_summarize  topic grouped by directory, KG-enriched when active
 *   mneme_timeline   subject ordered by mtime, KG-enriched when active
 *   mneme_prime      preflight context bundle within token budget
 *   mneme_propose    queue a memory-edit proposal for the policy drain
 *
 * The server resolves a VaultConfig once at startup. Vault root comes
 * from `MNEME_VAULT` or the documented resolution order. Tool
 * handlers receive the same config so all nine agree on paths.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
	CallToolRequestSchema,
	ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { toMnemeError } from "./errors.js";
import { redact, redactValue } from "./privacy.js";
import { isToolError } from "./tool_error.js";
import { publicToolDefinitions, TOOL_DEFINITIONS } from "./tool_registry.js";
import { VaultConfig } from "./vault/config.js";

const SERVER_NAME = "mneme-mcp";
const SERVER_VERSION = "3.6.0";

const HELP = `${SERVER_NAME} - MCP server for mneme vault memory

USAGE
  mneme-mcp
  mneme-mcp --version

ENVIRONMENT
  MNEME_VAULT  Vault root override. Without it, mneme walks for a .mneme marker
               and then falls back to ~/mneme-vault.
`;

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
		tools: publicToolDefinitions(),
	}));

	server.setRequestHandler(CallToolRequestSchema, async (request) => {
		const { name, arguments: args } = request.params;
		const def = TOOL_DEFINITIONS.find((tool) => tool.name === name);
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
									message: `Unknown tool: ${redact(name).text}`,
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
			const payload = JSON.stringify(redactValue(result), null, 2);
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
						text: JSON.stringify(redactValue({ ok: false, error: e }), null, 2),
					},
				],
				isError: true,
			};
		}
	});

	const transport = new StdioServerTransport();
	await server.connect(transport);
	console.error(`[${SERVER_NAME}] connected v${SERVER_VERSION}`);
}

main().catch((err: unknown) => {
	const message = err instanceof Error ? err.message : String(err);
	console.error("[mneme-mcp] fatal:", redact(message).text);
	process.exit(1);
});
