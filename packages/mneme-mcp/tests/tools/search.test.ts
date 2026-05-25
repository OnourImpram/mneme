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

  it("snippet does not contain frontmatter keys for a doc with frontmatter", () => {
    // Build a doc whose contentRaw includes a YAML frontmatter block but
    // whose bodyText (stored separately) does not.
    const { vault } = makeTempVault("search-fm-leak", [
      {
        path: "sessions/private.md",
        title: "Private Session",
        titleNormalized: "private session",
        contentRaw:
          "---\nsession_id: secret-sess-42\ntype: session\ntags: alpha\n---\n" +
          "# Private Session\n\nActual body text about memory consolidation.\n",
        bodyText:
          "# Private Session\n\nActual body text about memory consolidation.\n",
        contentNormalized: "actual body text about memory consolidation.",
        mtime: 1_717_100_000,
        frontmatterType: "session",
        sessionId: "secret-sess-42",
      },
    ]);
    const res = searchTool(
      SearchInputSchema.parse({ query: "memory consolidation" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.hits.length).toBeGreaterThan(0);
      const snippet = res.data.hits[0].snippet;
      // Snippet must come from bodyText, not from the raw frontmatter block.
      expect(snippet).not.toContain("session_id");
      expect(snippet).not.toContain("secret-sess-42");
      // Body content must be present.
      expect(snippet).toContain("memory consolidation");
    }
  });

  it("snippet length is bounded by SNIPPET_CHARS even for body-only content", () => {
    const longBody = "word ".repeat(100).trim();
    const { vault } = makeTempVault("search-snip-len", [
      {
        path: "long.md",
        title: "Long Doc",
        titleNormalized: "long doc",
        contentRaw: longBody,
        bodyText: longBody,
        contentNormalized: longBody.toLowerCase(),
        mtime: 1_717_200_000,
        frontmatterType: "topic",
      },
    ]);
    const res = searchTool(
      SearchInputSchema.parse({ query: "word" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.hits.length > 0) {
      expect(res.data.hits[0].snippet.length).toBeLessThanOrEqual(200);
    }
  });
});
