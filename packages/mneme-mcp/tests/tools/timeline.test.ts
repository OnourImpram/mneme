import { writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { timelineTool, TimelineInputSchema } from "../../src/tools/timeline.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

describe("TimelineInputSchema", () => {
  it("requires subject", () => {
    expect(() => TimelineInputSchema.parse({})).toThrow();
  });

  it("rejects malformed dates", () => {
    expect(() =>
      TimelineInputSchema.parse({ subject: "x", valid_from: "yesterday" }),
    ).toThrow();
  });

  it("defaults top_k to 25", () => {
    const parsed = TimelineInputSchema.parse({ subject: "x" });
    expect(parsed.top_k).toBe(25);
  });
});

describe("timelineTool runtime", () => {
  it("INDEX_NOT_FOUND when fts5.sqlite missing", async () => {
    const { vault } = makeTempVault("tl-noindex", []);
    const res = await timelineTool(
      TimelineInputSchema.parse({ subject: "anything" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
  });

  it("orders entries ASC by mtime", async () => {
    const { vault } = makeTempVault("tl-asc", defaultDocs());
    const res = await timelineTool(
      TimelineInputSchema.parse({ subject: "memory retrieval privacy" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      const mtimes = res.data.entries.map((e) => e.mtime);
      const sorted = [...mtimes].sort((a, b) => a - b);
      expect(mtimes).toEqual(sorted);
    }
  });

  it("source is fts5 and as_of_applied is false when kg inactive", async () => {
    const { vault } = makeTempVault("tl-source", defaultDocs());
    const res = await timelineTool(
      TimelineInputSchema.parse({ subject: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.source).toBe("fts5");
      expect(res.data.as_of_applied).toBe(false);
      expect(res.data.facts).toBeUndefined();
    }
  });

  it("respects valid_from and valid_to bounds", async () => {
    const { vault } = makeTempVault("tl-bounds", defaultDocs());
    const res = await timelineTool(
      TimelineInputSchema.parse({
        subject: "memory retrieval privacy",
        valid_from: "2026-05-25",
        valid_to: "2026-06-01",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      const mtimes = res.data.entries.map((e) => e.mtime);
      const sorted = [...mtimes].sort((a, b) => a - b);
      expect(mtimes).toEqual(sorted);
    }
  });

  it("stays fts5-only when kg flag is set but credentials missing", async () => {
    const { vault } = makeTempVault("tl-flag-nocreds", defaultDocs());
    writeFileSync(vault.kgActiveFlag, "on\n", "utf8");
    const res = await timelineTool(
      TimelineInputSchema.parse({ subject: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.source).toBe("fts5");
      expect(res.data.facts).toBeUndefined();
    }
  });
});
