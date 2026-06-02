import { readFileSync } from "node:fs";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { CANONICAL_MEMORY_TYPES, searchTool, SearchInputSchema } from "../../src/tools/search.js";
import { defaultDocs, makeTempVault } from "../helpers/vault_fixture.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Write a normalization_profile row into the index_meta table of an existing
 * FTS5 database. Creates the table if absent.
 */
function setIndexProfile(dbPath: string, profile: string): void {
  const db = new Database(dbPath);
  try {
    db.prepare(
      "CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ).run();
    db.prepare(
      "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('normalization_profile', ?)",
    ).run(profile);
  } finally {
    db.close();
  }
}

// ---------------------------------------------------------------------------
// T5 — canonical memory type enum parity
// ---------------------------------------------------------------------------

interface CanonicalTypeFixture {
  type: string;
  description: string;
}

const FIXTURE_PATH = join(
  __dirname,
  "../../../mneme-core/tests/fixtures/canonical_memory_types.json",
);

const fixtureTypes: CanonicalTypeFixture[] = JSON.parse(
  readFileSync(FIXTURE_PATH, "utf8"),
) as CanonicalTypeFixture[];

describe("CANONICAL_MEMORY_TYPES — parity with shared fixture", () => {
  it("contains exactly the same types as canonical_memory_types.json (no extras, no missing)", () => {
    const fromFixture = fixtureTypes.map((e) => e.type).sort();
    const fromTs = [...CANONICAL_MEMORY_TYPES].sort();
    expect(fromTs).toEqual(fromFixture);
  });
});

describe("SearchInputSchema — type enum (T5)", () => {
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

  it("accepts original three types: session, topic, reference", () => {
    for (const t of ["session", "topic", "reference"]) {
      expect(() =>
        SearchInputSchema.parse({ query: "x", filters: { type: t } }),
      ).not.toThrow();
    }
  });

  it("accepts extended types: pattern, trajectory, compressed, observation, session_summary, user_prompt", () => {
    for (const t of [
      "pattern",
      "trajectory",
      "compressed",
      "observation",
      "session_summary",
      "user_prompt",
    ]) {
      expect(() =>
        SearchInputSchema.parse({ query: "x", filters: { type: t } }),
      ).not.toThrow();
    }
  });

  it("rejects unknown type value", () => {
    expect(() =>
      SearchInputSchema.parse({ query: "x", filters: { type: "unknown_type" } }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// searchTool runtime
// ---------------------------------------------------------------------------

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
    if (res.ok) {
      expect(res.data.hits).toEqual([]);
      // backends_used must be empty when no hits are returned (stopword path).
      expect(res.data.backends_used).toEqual([]);
    }
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
      // Snippet is at most SNIPPET_CHARS chars + 2 possible ellipsis chars.
      expect(first.snippet.length).toBeLessThanOrEqual(202);
    }
  });

  it("backends_used contains 'fts5' when hits are returned", () => {
    const { vault } = makeTempVault("search-backends-ok", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.backends_used).toContain("fts5");
    }
  });

  it("backends_used is empty array when query produces no hits (stopword path)", () => {
    const { vault } = makeTempVault("search-backends-empty", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "the and or" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.backends_used).toEqual([]);
    }
  });

  it("every card in cards array has backend field set to 'fts5'", () => {
    const { vault } = makeTempVault("search-card-backend", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.cards.length > 0) {
      for (const card of res.data.cards) {
        expect(card.backend).toBe("fts5");
      }
    }
  });

  it("backends_used has no duplicates even with multiple hits from same backend", () => {
    const { vault } = makeTempVault("search-backends-dedup", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "memory retrieval privacy", top_k: 20 }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.cards.length > 1) {
      // All cards are from fts5 — backends_used must still be exactly ['fts5'].
      const unique = new Set(res.data.backends_used);
      expect(unique.size).toBe(res.data.backends_used.length);
      expect(res.data.backends_used).toEqual(["fts5"]);
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

  it("snippet does not expose private block content (Fix 2 — P3 chain)", () => {
    const { vault } = makeTempVault("search-snip-redact", [
      {
        path: "sessions/sensitive.md",
        title: "Sensitive Session",
        titleNormalized: "sensitive session",
        contentRaw: "Public preamble. <private>SECRET_SNIP</private> public tail.",
        bodyText: "Public preamble. <private>SECRET_SNIP</private> public tail.",
        contentNormalized: "public preamble public tail",
        mtime: 1_717_300_000,
        frontmatterType: "session",
      },
    ]);
    const res = searchTool(
      SearchInputSchema.parse({ query: "public preamble" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.hits.length).toBeGreaterThan(0);
      const snippet = res.data.hits[0].snippet;
      expect(snippet).not.toContain("SECRET_SNIP");
    }
  });

  it("snippet length is bounded by SNIPPET_CHARS + 2 ellipsis chars even for body-only content", () => {
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
      // SNIPPET_CHARS (200) + up to 2 ellipsis chars.
      expect(res.data.hits[0].snippet.length).toBeLessThanOrEqual(202);
    }
  });
});

// ---------------------------------------------------------------------------
// T7 — match-centered snippet
// ---------------------------------------------------------------------------

describe("searchTool — match-centered snippet (T7)", () => {
  it("snippet is centered on the query term when it appears mid-document", () => {
    // Build a body where the match term appears after a long prefix
    // so a naive prefix slice would miss it entirely.
    const prefix = "irrelevant ".repeat(20); // 220 chars — beyond SNIPPET_CHARS
    const match = "centeredterm";
    const suffix = " more irrelevant text";
    const body = prefix + match + suffix;

    const { vault } = makeTempVault("search-centered", [
      {
        path: "notes/centered.md",
        title: "Centered Doc",
        titleNormalized: "centered doc",
        contentRaw: body,
        bodyText: body,
        contentNormalized: body.toLowerCase(),
        mtime: 1_717_400_000,
        frontmatterType: "topic",
      },
    ]);
    const res = searchTool(
      SearchInputSchema.parse({ query: "centeredterm" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.hits.length > 0) {
      const snippet = res.data.hits[0].snippet;
      // The match term must appear in the snippet.
      expect(snippet).toContain("centeredterm");
      // And the snippet must be bounded.
      expect(snippet.length).toBeLessThanOrEqual(202);
    }
  });

  it("snippet falls back to prefix when no query token is found in bodyText", () => {
    // Query token won't match body content (body uses different words).
    const body = "alpha beta gamma delta epsilon zeta eta theta iota kappa";
    const { vault } = makeTempVault("search-fallback", [
      {
        path: "notes/fallback.md",
        title: "Fallback Doc",
        titleNormalized: "fallback doc",
        contentRaw: body,
        bodyText: body,
        contentNormalized: body.toLowerCase(),
        mtime: 1_717_500_000,
        frontmatterType: "topic",
      },
    ]);
    // Use a token that appears in FTS index (via title normalized) but not bodyText.
    const res = searchTool(
      SearchInputSchema.parse({ query: "fallback" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.hits.length > 0) {
      const snippet = res.data.hits[0].snippet;
      // Fallback prefix: snippet starts with the beginning of body.
      expect(snippet).toContain("alpha");
    }
  });

  it("snippet still redacts private blocks even when centered on match (privacy pipeline intact)", () => {
    const prefix = "safe text ".repeat(15); // 150 chars
    const body =
      prefix + "keyword <private>HIDDEN_SECRET</private> more safe text";
    const { vault } = makeTempVault("search-centered-redact", [
      {
        path: "notes/redact.md",
        title: "Redact Doc",
        titleNormalized: "redact doc",
        contentRaw: body,
        bodyText: body,
        contentNormalized: "safe text keyword more safe text",
        mtime: 1_717_600_000,
        frontmatterType: "topic",
      },
    ]);
    const res = searchTool(
      SearchInputSchema.parse({ query: "keyword" }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok && res.data.hits.length > 0) {
      const snippet = res.data.hits[0].snippet;
      expect(snippet).not.toContain("HIDDEN_SECRET");
      expect(snippet).toContain("keyword");
    }
  });
});

// ---------------------------------------------------------------------------
// P6 — locale / normalizer profile mismatch guard
// ---------------------------------------------------------------------------

describe("searchTool — locale profile mismatch guard (P6)", () => {
  it("proceeds normally when index_meta table is absent (legacy index, soft-degrade)", () => {
    // defaultDocs() creates a DB without index_meta — this is the legacy case.
    const { vault } = makeTempVault("search-p6-absent", defaultDocs());
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    // Must succeed — absent profile is a soft-degrade, not an error.
    expect(res.ok).toBe(true);
  });

  it("proceeds normally when index_meta profile matches query-side profile (tr-cldr)", () => {
    const { vault } = makeTempVault("search-p6-match", defaultDocs());
    setIndexProfile(vault.fts5Db, "tr-cldr");
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(true);
  });

  it("returns INDEX_STALE_OR_LOCALE_MISMATCH when profile is present but different", () => {
    const { vault } = makeTempVault("search-p6-mismatch", defaultDocs());
    setIndexProfile(vault.fts5Db, "identity");
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error.code).toBe(ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH);
    }
  });

  it("returns INDEX_STALE_OR_LOCALE_MISMATCH for tr-ascii-fold profile mismatch", () => {
    const { vault } = makeTempVault("search-p6-asciifold", defaultDocs());
    setIndexProfile(vault.fts5Db, "tr-ascii-fold");
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error.code).toBe(ERROR_CODES.INDEX_STALE_OR_LOCALE_MISMATCH);
    }
  });

  it("error message names both the stored profile and the expected profile", () => {
    const { vault } = makeTempVault("search-p6-msg", defaultDocs());
    setIndexProfile(vault.fts5Db, "identity");
    const res = searchTool(
      SearchInputSchema.parse({ query: "rank fusion" }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error.message).toContain("identity");
      expect(res.error.message).toContain("tr-cldr");
    }
  });
});
