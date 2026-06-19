/**
 * End-to-end smoke test for the MCP server.
 *
 * Spawns the server via `tsx src/index.ts` and connects an MCP Client
 * over stdio. Verifies the tools/list response surfaces all seven
 * `mneme_*` tools with non-empty descriptions and JSON Schema input
 * declarations.
 *
 * MNEME_VAULT is pointed at a fresh temp directory. Tools that need
 * an FTS5 index would return INDEX_NOT_FOUND, but this test only
 * exercises the listing path so no index is required.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const PACKAGE_ROOT = resolve(__dirname, "..", "..");
const SERVER_ENTRY = join(PACKAGE_ROOT, "src", "index.ts");

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
];

describe("mneme-mcp server end-to-end", () => {
  let client: Client;
  let transport: StdioClientTransport;
  let tempVault: string;

  beforeAll(async () => {
    tempVault = mkdtempSync(join(tmpdir(), "mneme-mcp-smoke-"));
    transport = new StdioClientTransport({
      command: "npx",
      args: ["tsx", SERVER_ENTRY],
      env: { ...process.env, MNEME_VAULT: tempVault },
      cwd: PACKAGE_ROOT,
    });
    client = new Client(
      { name: "mneme-mcp-smoke-test", version: "0.0.0" },
      { capabilities: {} },
    );
    await client.connect(transport);
  }, 60_000);

  afterAll(async () => {
    await client?.close();
  });

  it("lists all nine mneme_ tools", async () => {
    const res = await client.listTools();
    const names = res.tools.map((t) => t.name).sort();
    expect(names.sort()).toEqual([...EXPECTED_TOOL_NAMES].sort());
  });

  it("each tool exposes a non-empty description", async () => {
    const res = await client.listTools();
    for (const t of res.tools) {
      expect(t.description).toBeTruthy();
      expect((t.description ?? "").length).toBeGreaterThan(10);
    }
  });

  it("each tool exposes a JSON Schema input declaration", async () => {
    const res = await client.listTools();
    for (const t of res.tools) {
      expect(t.inputSchema).toBeTruthy();
      expect((t.inputSchema as { type?: string }).type).toBe("object");
    }
  });

  it("calls mneme_search and gets a structured response", async () => {
    const res = await client.callTool({
      name: "mneme_search",
      arguments: { query: "anything" },
    });
    // No index yet, so we expect INDEX_NOT_FOUND wrapped in a text payload.
    expect(res.isError).toBe(true);
    const content = res.content as Array<{ type: string; text: string }>;
    expect(content[0]?.type).toBe("text");
    const body = JSON.parse(content[0]?.text ?? "{}") as {
      ok: boolean;
      error?: { code?: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error?.code).toBe("INDEX_NOT_FOUND");
  });
});

describe("mneme-mcp CLI metadata", () => {
  it("--version does not require a vault", () => {
    const env = { ...process.env };
    delete env.MNEME_VAULT;
    const res = spawnSync(`npx tsx ${SERVER_ENTRY} --version`, {
      cwd: PACKAGE_ROOT,
      encoding: "utf-8",
      env,
      shell: true,
    });
    expect(res.status).toBe(0);
    expect(res.stdout).toMatch(/mneme-mcp \d+\.\d+\.\d+/);
    expect(res.stderr).not.toContain("VaultNotFound");
    expect(res.stderr).not.toContain("[mneme-mcp] fatal");
    // Cold `npx tsx` spawn on a loaded Windows CI runner can exceed vitest's
    // 5s default; match the beforeAll budget. This asserts behavior, not latency.
  }, 60_000);
});
