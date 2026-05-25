import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { FENCE_CLOSE, FENCE_OPEN, NOTICE } from "../../src/injection.js";
import { primeTool, PrimeInputSchema } from "../../src/tools/prime.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

function writeBody(rootDir: string, relPath: string, body: string): void {
  const abs = join(rootDir, relPath);
  mkdirSync(dirname(abs), { recursive: true });
  writeFileSync(abs, body, "utf8");
}

describe("PrimeInputSchema", () => {
  it("requires task_description", () => {
    expect(() => PrimeInputSchema.parse({})).toThrow();
  });

  it("defaults budget_tokens to 4000", () => {
    const parsed = PrimeInputSchema.parse({ task_description: "x" });
    expect(parsed.budget_tokens).toBe(4000);
  });

  it("rejects budget_tokens above 20000", () => {
    expect(() =>
      PrimeInputSchema.parse({ task_description: "x", budget_tokens: 50_000 }),
    ).toThrow();
  });
});

describe("primeTool runtime", () => {
  it("INDEX_NOT_FOUND when fts5.sqlite missing", () => {
    const { vault } = makeTempVault("prime-noindex", []);
    const res = primeTool(
      PrimeInputSchema.parse({ task_description: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
  });

  it("returns IO_ERROR when the index file is not a valid database", () => {
    // The file exists (so the INDEX_NOT_FOUND guard passes) but a read
    // fails. Every sibling tool returns a structured envelope here; prime
    // must not leak an unhandled exception out of the tool call.
    const { vault } = makeTempVault("prime-corrupt", []);
    writeFileSync(vault.fts5Db, "this is not a sqlite database", "utf8");
    const res = primeTool(
      PrimeInputSchema.parse({ task_description: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.IO_ERROR);
  });

  it("builds preamble with recent and topic sections", () => {
    const { vault, rootDir } = makeTempVault("prime-build", defaultDocs());
    writeBody(rootDir, "daily/2026-05-18.md", "# Daily\n\nSession ran.\n");
    const res = primeTool(
      PrimeInputSchema.parse({ task_description: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.preamble).toContain("##");
      expect(res.data.bytes).toBeGreaterThan(0);
      expect(res.data.sources.length).toBeGreaterThan(0);
    }
  });

  it("returns sources tagged as recent or topic", () => {
    const { vault, rootDir } = makeTempVault("prime-tag", defaultDocs());
    writeBody(rootDir, "daily/2026-05-18.md", "# Daily\n\nbody\n");
    const res = primeTool(
      PrimeInputSchema.parse({
        task_description: "rank fusion",
        recent_session_count: 2,
        topic_doc_count: 2,
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      const kinds = res.data.sources.map((s) => s.kind);
      const distinct = new Set(kinds);
      expect(distinct.size).toBeGreaterThanOrEqual(1);
    }
  });

  it("truncates when budget_tokens is very small", () => {
    const { vault, rootDir } = makeTempVault("prime-truncate", defaultDocs());
    writeBody(rootDir, "daily/2026-05-18.md", "# Daily\n\n" + "Lorem ".repeat(500));
    const res = primeTool(
      PrimeInputSchema.parse({
        task_description: "rank fusion privacy retrieval memory",
        budget_tokens: 50,
        recent_session_count: 5,
        topic_doc_count: 5,
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.truncated).toBe(true);
      // Budget caps the CONTENT (approx 50 tokens * 4 chars). The returned
      // preamble also carries the fixed spotlighting fence (G-3), so allow
      // for that deterministic framing overhead.
      const fenceOverhead =
        FENCE_OPEN.length +
        " source=vault-prime\n".length +
        NOTICE.length +
        2 +
        FENCE_CLOSE.length;
      expect(res.data.bytes).toBeLessThanOrEqual(50 * 4 + 100 + fenceOverhead);
    }
  });

  it("approx_tokens matches char-based heuristic", () => {
    const { vault } = makeTempVault("prime-tokens", defaultDocs());
    const res = primeTool(
      PrimeInputSchema.parse({ task_description: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.approx_tokens).toBe(
        Math.ceil(res.data.preamble.length / 4),
      );
    }
  });
});
