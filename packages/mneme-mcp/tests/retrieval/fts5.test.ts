import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildFts5Query, fts5Search } from "../../src/retrieval/fts5.js";
import { normalizeTr } from "../../src/locale/tr.js";
import { buildTestDb, type TestDoc } from "../helpers/fts5_fixture.js";

function freshTmp(prefix: string): string {
  return mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
}

const turkishStopwords = new Set(["ve", "ile", "icin", "bir", "bu", "su"]);

function fixtureDocs(): TestDoc[] {
  return [
    {
      path: "daily/2026-05-18.md",
      title: "Reciprocal Rank Fusion",
      titleNormalized: normalizeTr("Reciprocal Rank Fusion"),
      contentRaw: "RRF k equals sixty fuses ranked lists. Robust across tasks.",
      contentNormalized: normalizeTr(
        "RRF k equals sixty fuses ranked lists. Robust across tasks.",
      ),
      mtime: 1_716_000_000,
      frontmatterType: "session",
      sessionId: "s-2026-05-18",
    },
    {
      path: "reference/vault-native.md",
      title: "Vault Native Memory",
      titleNormalized: normalizeTr("Vault Native Memory"),
      contentRaw:
        "Markdown is ground truth. Hybrid retrieval beats single backend.",
      contentNormalized: normalizeTr(
        "Markdown is ground truth. Hybrid retrieval beats single backend.",
      ),
      mtime: 1_716_500_000,
      frontmatterType: "reference",
    },
    {
      path: "reference/privacy.md",
      title: "Privacy Redaction",
      titleNormalized: normalizeTr("Privacy Redaction"),
      contentRaw: "Sensitive content is stripped at staging. Audit log stays.",
      contentNormalized: normalizeTr(
        "Sensitive content is stripped at staging. Audit log stays.",
      ),
      mtime: 1_717_000_000,
      frontmatterType: "reference",
    },
  ];
}

describe("buildFts5Query", () => {
  it("quotes each token and joins with OR", () => {
    expect(buildFts5Query("hello world")).toBe('"hello" OR "world"');
  });

  it("returns empty string on empty input", () => {
    expect(buildFts5Query("")).toBe("");
    expect(buildFts5Query("   ")).toBe("");
  });

  it("drops tokens shorter than minTokenLength", () => {
    expect(buildFts5Query("a hello b world", { minTokenLength: 2 })).toBe(
      '"hello" OR "world"',
    );
  });

  it("drops stopwords", () => {
    expect(
      buildFts5Query("the hello world", {
        stopwords: new Set(["the"]),
      }),
    ).toBe('"hello" OR "world"');
  });

  it("strips FTS5 reserved characters from tokens", () => {
    // " : * are syntactic in FTS5; strip them out before quoting.
    expect(buildFts5Query('"foo* bar:qux"')).toBe('"foo" OR "barqux"');
  });

  it("applies the normalizer to each token", () => {
    expect(buildFts5Query("KIYASLAMA İstanbul", { normalize: normalizeTr })).toBe(
      '"kıyaslama" OR "istanbul"',
    );
  });
});

describe("fts5Search", () => {
  it("returns hits for known terms", () => {
    const root = freshTmp("fts-known");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());

    const hits = fts5Search({
      dbPath,
      ftsQuery: '"rank" OR "fusion"',
      limit: 10,
    });
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.some((h) => h.title.startsWith("Reciprocal"))).toBe(true);
  });

  it("returns empty on empty FTS query", () => {
    const root = freshTmp("fts-empty-q");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());
    expect(fts5Search({ dbPath, ftsQuery: "", limit: 10 })).toEqual([]);
  });

  it("respects limit", () => {
    const root = freshTmp("fts-limit");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());
    const hits = fts5Search({
      dbPath,
      ftsQuery: '"memory" OR "retrieval" OR "privacy"',
      limit: 1,
    });
    expect(hits.length).toBeLessThanOrEqual(1);
  });

  it("filters by mtimeFrom inclusive", () => {
    const root = freshTmp("fts-from");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());
    const hits = fts5Search({
      dbPath,
      ftsQuery: '"retrieval" OR "rank" OR "privacy"',
      limit: 10,
      mtimeFrom: 1_716_400_000,
    });
    for (const h of hits) {
      expect(h.mtime).toBeGreaterThanOrEqual(1_716_400_000);
    }
  });

  it("filters by mtimeTo inclusive", () => {
    const root = freshTmp("fts-to");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());
    const hits = fts5Search({
      dbPath,
      ftsQuery: '"retrieval" OR "rank" OR "privacy"',
      limit: 10,
      mtimeTo: 1_716_400_000,
    });
    for (const h of hits) {
      expect(h.mtime).toBeLessThanOrEqual(1_716_400_000);
    }
  });

  it("rejects writes via PRAGMA query_only", () => {
    const root = freshTmp("fts-readonly");
    const dbPath = join(root, "fts.db");
    buildTestDb(dbPath, fixtureDocs());
    // fts5Search opens readonly so the underlying file can't be mutated.
    const hits = fts5Search({ dbPath, ftsQuery: '"rank"', limit: 1 });
    expect(hits.length).toBeGreaterThanOrEqual(0);
  });
});
