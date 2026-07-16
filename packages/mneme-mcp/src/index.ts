#!/usr/bin/env node
/**
 * mneme-mcp, the stdio MCP server for Mneme vault memory.
 *
 * The server exposes nine tools. Runtime validation and ListTools schemas are
 * supplied by the authoritative registry in tool_registry.ts.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
	CallToolRequestSchema,
	ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { toMnemeError } from "./errors.js";
import { isToolError } from "./tool_error.js";
import { TOOLS } from "./tool_registry.js";
import { VaultConfig } from "./vault/config.js";

const SERVER_NAME = "mneme-mcp";
const SERVER_VERSION = "3.5.0";

const HELP = `${SERVER_NAME} - MCP server for Mneme vault memory

USAGE
  mneme-mcp
  mneme-mcp --version

ENVIRONMENT
  MNEME_VAULT  Vault root override. Without it, Mneme walks for a .mneme marker
               and then falls back to ~/mneme-vault.
  MNEME_SCOPE  Default isolation scope. Omitted tool scope arguments use it.
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
		tools: TOOLS.map((tool) => ({
			name: tool.name,
			description: tool.description,
			inputSchema: tool.inputSchema,
		})),
	}));

	server.setRequestHandler(CallToolRequestSchema, async (request) => {
		const { name, arguments: args } = request.params;
		const definition = TOOLS.find((tool) => tool.name === name);
		if (!definition) {
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
			const result = await definition.handler(args ?? {}, vault);
			return {
				content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
				isError: isToolError(result),
			};
		} catch (err) {
			const error = toMnemeError(err);
			return {
				content: [
					{
						type: "text",
						text: JSON.stringify({ ok: false, error }, null, 2),
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
