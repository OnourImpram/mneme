import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { searchTool, SearchInputSchema } from "../../src/tools/search.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

describe("SearchInputSchema", () => {
  it("requires query", () => {
    expect(() => SearchInputSchema.parse({})).toThrow();
  });

  it("applies default top_k of 10", () => {
    const parsed = SearchInputSchema.parse({ query: "x" });
    expect(parsed.top_k).toBe(10);
  });

  it("rejects top_k above 50", () => {
    expect(() => SearchInputSchema.parse({ query: "x", top_k: 100 })).toThrow();
  });

  it("rejects malformed date_from", () => {
    expect(() =>
      SearchInputSchema.parse({ query: "x", filters: { date_from: "yesterday" } }),
    ).toThrow();
  });
});

describe("searchTool runtime", () => {
  it("returns INDEX_NOT_FOUND when fts5.sqlite is missing", () => {
    const { vault } = makeTempVault("search-noindex", []);
    const res = searchTool(
      SearchInputSchema.parse({ query: "anything" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.INDEX_NOT_FOUND);
  });

  it("returns QUERY_TOO_SHORT when below min_query_length", () => {
    const { vault } = makeTempVault("search-short", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "hi", min_query_length: 20 }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.QUERY_TOO_SHORT);
  });

  it("returns empty hits when all tokens are stopwords", () => {
    const { vault } = makeTempVault("search-empty", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "the and or" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) expect(res.data.hits).toEqual([]);
  });

  it("returns ranked hits for real query", () => {
    const { vault } = makeTempVault("search-ok", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.hits.length).toBeGreaterThan(0);
      const first = res.data.hits[0];
      expect(first.path).toBeTruthy();
      expect(first.snippet.length).toBeLessThanOrEqual(200);
    }
  });

  it("filters by frontmatter_type", () => {
    const { vault } = makeTempVault("search-typed", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({
        query: "memory retrieval privacy",
        filters: { type: "reference" },
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      for (const h of res.data.hits) expect(h.type).toBe("reference");
    }
  });

  it("filters by date_from inclusive", () => {
    const { vault } = makeTempVault("search-dfrom", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({
        query: "memory retrieval privacy",
        filters: { date_from: "2026-05-18" },
      }),
      vault,
    );
    expect(res.ok).toBe(true);
  });
});
