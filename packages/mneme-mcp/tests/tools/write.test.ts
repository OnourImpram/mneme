import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { ERROR_CODES } from "../../src/errors.js";
import { VaultConfig } from "../../src/vault/config.js";
import { writeTool, WriteInputSchema } from "../../src/tools/write.js";

function makeBareVault(prefix: string): { vault: VaultConfig; rootDir: string } {
  const rootDir = mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
  return { vault: VaultConfig.fromPath(rootDir), rootDir };
}

describe("WriteInputSchema", () => {
  it("requires path, section, content", () => {
    expect(() => WriteInputSchema.parse({})).toThrow();
  });

  it("defaults replace to false", () => {
    const parsed = WriteInputSchema.parse({
      path: "a.md",
      section: "Notes",
      content: "x",
    });
    expect(parsed.replace).toBe(false);
  });

  it("accepts frontmatter record", () => {
    const parsed = WriteInputSchema.parse({
      path: "a.md",
      section: "S",
      content: "",
      frontmatter: { id: "abc", schema_version: 1 },
    });
    expect(parsed.frontmatter?.id).toBe("abc");
  });
});

describe("writeTool runtime", () => {
  it("appends a new section to a fresh file", () => {
    const { vault, rootDir } = makeBareVault("write-fresh");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "daily/notes.md",
        section: "Today",
        content: "Logged X.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.operation).toBe("added");
      expect(res.data.created_new_file).toBe(true);
      const written = readFileSync(join(rootDir, "daily/notes.md"), "utf8");
      expect(written).toContain("## Today");
      expect(written).toContain("Logged X.");
    }
  });

  it("appends to an existing file without overwriting", () => {
    const { vault, rootDir } = makeBareVault("write-append");
    const target = join(rootDir, "doc.md");
    writeFileSync(target, "Existing line.\n", "utf8");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "doc.md",
        section: "Second",
        content: "Second body.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      const written = readFileSync(target, "utf8");
      expect(written).toContain("Existing line.");
      expect(written).toContain("## Second");
    }
  });

  it("replaces an existing section in replace mode", () => {
    const { vault, rootDir } = makeBareVault("write-replace");
    const target = join(rootDir, "doc.md");
    writeFileSync(target, "## Notes\n\nOld body.\n", "utf8");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "doc.md",
        section: "Notes",
        content: "New body.",
        replace: true,
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.operation).toBe("replaced");
      const written = readFileSync(target, "utf8");
      expect(written).toContain("New body.");
      expect(written).not.toContain("Old body.");
    }
  });

  it("rejects paths that escape the vault root", () => {
    const { vault } = makeBareVault("write-escape");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "../outside.md",
        section: "S",
        content: "x",
      }),
      vault,
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(ERROR_CODES.PATH_OUTSIDE_VAULT);
  });

  it("creates parent directories as needed", () => {
    const { vault, rootDir } = makeBareVault("write-mkdir");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "a/b/c/deep.md",
        section: "S",
        content: "deep",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const created = readFileSync(join(rootDir, "a/b/c/deep.md"), "utf8");
    expect(created).toContain("## S");
  });

  it("prepends frontmatter only when creating a new file", () => {
    const { vault, rootDir } = makeBareVault("write-fm");
    const r1 = writeTool(
      WriteInputSchema.parse({
        path: "fm.md",
        section: "Body",
        content: "x",
        frontmatter: { id: "abc", type: "topic" },
      }),
      vault,
    );
    expect(r1.ok).toBe(true);
    const after1 = readFileSync(join(rootDir, "fm.md"), "utf8");
    expect(after1.startsWith("---\n")).toBe(true);
    // Codex Pass 2 YAML-escape fix emits double-quoted string scalars.
    expect(after1).toContain('id: "abc"');

    const r2 = writeTool(
      WriteInputSchema.parse({
        path: "fm.md",
        section: "Second",
        content: "y",
        frontmatter: { id: "different", type: "session" },
      }),
      vault,
    );
    expect(r2.ok).toBe(true);
    const after2 = readFileSync(join(rootDir, "fm.md"), "utf8");
    // Existing frontmatter is preserved unchanged on second write.
    expect(after2.split("---").length).toBeLessThanOrEqual(3);
    expect(after2).toContain('id: "abc"');
    expect(after2).not.toContain('id: "different"');
  });
});

// F1: canonical redact() — case-insensitive and attribute-tolerant
describe("writeTool redaction (F1)", () => {
  it("redacts uppercase <PRIVATE>secret</PRIVATE> before writing", () => {
    const { vault, rootDir } = makeBareVault("write-redact-upper");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "redact-upper.md",
        section: "Notes",
        content: "Prefix <PRIVATE>secret</PRIVATE> suffix.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const written = readFileSync(join(rootDir, "redact-upper.md"), "utf8");
    expect(written).not.toContain("secret");
    expect(written).toContain("[REDACTED]");
    if (res.ok) expect(res.data.redactions_applied).toBeGreaterThanOrEqual(1);
  });

  it("redacts attribute-bearing <private reason=\"x\">secret</private> before writing", () => {
    const { vault, rootDir } = makeBareVault("write-redact-attr");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "redact-attr.md",
        section: "Notes",
        content: 'Before <private reason="x">secret</private> after.',
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const written = readFileSync(join(rootDir, "redact-attr.md"), "utf8");
    expect(written).not.toContain("secret");
    expect(written).toContain("[REDACTED]");
    if (res.ok) expect(res.data.redactions_applied).toBeGreaterThanOrEqual(1);
  });
});

// F2: H2 heading guard
describe("writeTool H2 body guard (F2)", () => {
  it("throws when content contains a '## ' H2 line", () => {
    const { vault } = makeBareVault("write-h2-guard");
    expect(() =>
      writeTool(
        WriteInputSchema.parse({
          path: "h2guard.md",
          section: "Top",
          content: "Some text.\n## Sub\nMore text.",
        }),
        vault,
      ),
    ).toThrow(/H2/i);
  });

  it("succeeds when content contains only H3+ headings", () => {
    const { vault, rootDir } = makeBareVault("write-h3-ok");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "h3ok.md",
        section: "Top",
        content: "Some text.\n### Sub-heading\nMore text.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const written = readFileSync(join(rootDir, "h3ok.md"), "utf8");
    expect(written).toContain("### Sub-heading");
  });
});

// F3: append separator
describe("writeTool append separator (F3)", () => {
  it("appending to a file ending in a single newline yields exactly one blank line before the new heading", () => {
    const { vault, rootDir } = makeBareVault("write-sep-single-nl");
    const target = join(rootDir, "sep.md");
    writeFileSync(target, "Existing content.\n", "utf8");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "sep.md",
        section: "New Section",
        content: "New body.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const written = readFileSync(target, "utf8");
    // Exactly one blank line between old content and the new heading.
    expect(written).toContain("Existing content.\n\n## New Section");
    // Must NOT have two blank lines (three consecutive newlines).
    expect(written).not.toContain("Existing content.\n\n\n");
  });

  it("appending to a file already ending in double newline adds no extra blank line", () => {
    const { vault, rootDir } = makeBareVault("write-sep-double-nl");
    const target = join(rootDir, "sep2.md");
    writeFileSync(target, "Existing content.\n\n", "utf8");
    const res = writeTool(
      WriteInputSchema.parse({
        path: "sep2.md",
        section: "New Section",
        content: "New body.",
      }),
      vault,
    );
    expect(res.ok).toBe(true);
    const written = readFileSync(target, "utf8");
    expect(written).toContain("Existing content.\n\n## New Section");
    expect(written).not.toContain("Existing content.\n\n\n");
  });
});
