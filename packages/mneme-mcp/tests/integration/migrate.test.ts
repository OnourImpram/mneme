/**
 * Integration tests for the claude-mem migration.
 *
 * A synthetic SQLite database is materialized that mirrors the
 * relevant claude-mem v13.2.0 schema. The migrator is then run
 * against that fixture and against the actual filesystem. No
 * Python process is spawned; the TS layer is self-contained.
 */

import Database from "better-sqlite3";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { VaultConfig } from "../../src/vault/config.js";
import {
  buildObservationMarkdown,
  migrate,
  observationContentHash,
  readExistingContentHash,
  redact,
  serializeFrontmatter,
  type MigrationOptions,
} from "../../src/cli/migrate.js";

interface FixtureRow {
  id: number;
  memory_session_id: string;
  project: string;
  text: string;
  type: string;
  title: string;
  subtitle: string;
  facts: string;
  narrative: string;
  concepts: string;
  files_read: string;
  files_modified: string;
  prompt_number: number;
  discovery_tokens: number;
  created_at: string;
  created_at_epoch: number;
  content_hash: string;
  generated_by_model: string;
  agent_type: string;
  agent_id: string;
  metadata: string;
}

const SCHEMA_STATEMENTS: string[] = [
  `CREATE TABLE observations (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     project TEXT,
     text TEXT,
     type TEXT,
     title TEXT,
     subtitle TEXT,
     facts TEXT,
     narrative TEXT,
     concepts TEXT,
     files_read TEXT,
     files_modified TEXT,
     prompt_number INTEGER,
     discovery_tokens INTEGER,
     created_at TEXT,
     created_at_epoch INTEGER,
     content_hash TEXT,
     generated_by_model TEXT,
     agent_type TEXT,
     agent_id TEXT,
     metadata TEXT
   )`,
  `CREATE TABLE session_summaries (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     summary TEXT,
     created_at TEXT
   )`,
  `CREATE TABLE user_prompts (
     id INTEGER PRIMARY KEY,
     memory_session_id TEXT,
     prompt_text TEXT,
     created_at TEXT
   )`,
];

function makeFixtureDb(path: string, rows: FixtureRow[]): void {
  const db = new Database(path);
  try {
    for (const stmt of SCHEMA_STATEMENTS) {
      db.prepare(stmt).run();
    }
    const insert = db.prepare(`
      INSERT INTO observations
      (id, memory_session_id, project, text, type, title, subtitle, facts,
       narrative, concepts, files_read, files_modified, prompt_number,
       discovery_tokens, created_at, created_at_epoch, content_hash,
       generated_by_model, agent_type, agent_id, metadata)
      VALUES (@id, @memory_session_id, @project, @text, @type, @title,
              @subtitle, @facts, @narrative, @concepts, @files_read,
              @files_modified, @prompt_number, @discovery_tokens,
              @created_at, @created_at_epoch, @content_hash,
              @generated_by_model, @agent_type, @agent_id, @metadata)
    `);
    const tx = db.transaction((items: FixtureRow[]) => {
      for (const r of items) insert.run(r);
    });
    tx(rows);
    db.prepare(
      "INSERT INTO session_summaries (id, memory_session_id, summary, created_at) VALUES (?, ?, ?, ?)",
    ).run(1, "session-A", "First wrap-up", "2026-04-01T08:00:00Z");
    db.prepare(
      "INSERT INTO user_prompts (id, memory_session_id, prompt_text, created_at) VALUES (?, ?, ?, ?)",
    ).run(
      1,
      "session-A",
      "How do we wire the indexer?",
      "2026-04-01T08:01:00Z",
    );
  } finally {
    db.close();
  }
}

function sampleRow(overrides: Partial<FixtureRow> = {}): FixtureRow {
  return {
    id: 1,
    memory_session_id: "session-A",
    project: "demo-project",
    text: "Sample observation text body.",
    type: "discovery",
    title: "Sample title",
    subtitle: "Sample subtitle",
    facts: "Fact one. Fact two.",
    narrative: "Narrative content.",
    concepts: "concept-a, concept-b",
    files_read: "[]",
    files_modified: "[]",
    prompt_number: 1,
    discovery_tokens: 42,
    created_at: "2026-04-01T08:00:00Z",
    created_at_epoch: 1_775_376_000,
    content_hash: "synthetic-hash-1",
    generated_by_model: "synthetic-model",
    agent_type: "main",
    agent_id: "agent-1",
    metadata: "{}",
    ...overrides,
  };
}

describe("redact", () => {
  it("returns input unchanged when there is no private tag", () => {
    const r = redact("Hello world");
    expect(r.text).toBe("Hello world");
    expect(r.count).toBe(0);
  });

  it("replaces a single private block with REDACTED", () => {
    const r = redact("before <private>secret</private> after");
    expect(r.text).toBe("before [REDACTED] after");
    expect(r.count).toBe(1);
  });

  it("counts multiple substitutions including multi-line blocks", () => {
    const text = "<private>a\nb</private> mid <private>c</private>";
    const r = redact(text);
    expect(r.count).toBe(2);
    expect(r.text).toBe("[REDACTED] mid [REDACTED]");
  });

  it("treats null and empty string as zero-substitution passthrough", () => {
    expect(redact(null).count).toBe(0);
    expect(redact("").text).toBe("");
  });
});

describe("serializeFrontmatter", () => {
  it("emits a deterministic YAML block with proper delimiters", () => {
    const block = serializeFrontmatter(
      [
        ["id", "cm-obs-1"],
        ["type", "session"],
        ["schema_version", 1],
      ],
      [["tags", ["alpha", "beta"]]],
    );
    expect(block.startsWith("---\n")).toBe(true);
    expect(block).toContain("id: 'cm-obs-1'");
    expect(block).toContain("type: 'session'");
    expect(block).toContain("schema_version: 1");
    expect(block).toContain("tags:");
    expect(block).toContain("  - 'alpha'");
    expect(block.trimEnd().endsWith("---")).toBe(true);
  });

  it("escapes inner single quotes by doubling", () => {
    const block = serializeFrontmatter([["title", "it's fine"]]);
    expect(block).toContain("title: 'it''s fine'");
  });

  it("omits empty arrays entirely", () => {
    const block = serializeFrontmatter(
      [["id", "cm-obs-2"]],
      [["tags", []]],
    );
    expect(block).not.toContain("tags:");
  });
});

describe("observationContentHash", () => {
  it("is stable for identical row payloads", () => {
    const a = observationContentHash(sampleRow() as never);
    const b = observationContentHash(sampleRow() as never);
    expect(a).toBe(b);
    expect(a.length).toBe(64);
  });

  it("changes when narrative changes", () => {
    const a = observationContentHash(sampleRow() as never);
    const b = observationContentHash(
      sampleRow({ narrative: "Different narrative" }) as never,
    );
    expect(a).not.toBe(b);
  });
});

describe("readExistingContentHash", () => {
  it("returns null on missing file", () => {
    expect(readExistingContentHash("/nonexistent/path/file.md")).toBe(null);
  });

  it("extracts the content_hash line from a written file", () => {
    const dir = mkdtempSync(join(tmpdir(), "mneme-frontmatter-"));
    try {
      const path = join(dir, "doc.md");
      writeFileSync(
        path,
        "---\nid: 'cm-obs-1'\ncontent_hash: 'abc123'\n---\n\nbody\n",
        "utf8",
      );
      expect(readExistingContentHash(path)).toBe("abc123");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("returns null when the file has no frontmatter", () => {
    const dir = mkdtempSync(join(tmpdir(), "mneme-frontmatter-"));
    try {
      const path = join(dir, "doc.md");
      writeFileSync(path, "no frontmatter here", "utf8");
      expect(readExistingContentHash(path)).toBe(null);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("buildObservationMarkdown", () => {
  it("uses title when present, falls back to subtitle, then to default", () => {
    const row = sampleRow({ title: "", subtitle: "fallback-sub" });
    const md = buildObservationMarkdown(row as never, {
      title: "",
      subtitle: "fallback-sub",
      narrative: "",
      text: "",
    });
    expect(md.startsWith("# fallback-sub")).toBe(true);
  });

  it("emits all four section headings when content is present", () => {
    const md = buildObservationMarkdown(sampleRow() as never, {
      title: "T",
      subtitle: "S",
      narrative: "N",
      text: "X",
    });
    expect(md).toContain("## Narrative");
    expect(md).toContain("## Facts");
    expect(md).toContain("## Concepts");
    expect(md).toContain("## Text");
  });

  it("skips a section when its content is empty", () => {
    const md = buildObservationMarkdown(sampleRow() as never, {
      title: "T",
      subtitle: "",
      narrative: "",
      text: "X",
    });
    expect(md).not.toContain("## Narrative");
    expect(md).toContain("## Text");
  });
});

describe("migrate (full integration)", () => {
  let workDir: string;
  let vaultDir: string;
  let dbPath: string;
  let vault: VaultConfig;

  beforeEach(() => {
    workDir = mkdtempSync(join(tmpdir(), "mneme-migrate-"));
    vaultDir = join(workDir, "vault");
    dbPath = join(workDir, "claude-mem.db");
    mkdirSync(join(vaultDir, ".mneme"), { recursive: true });
    vault = VaultConfig.fromPath(vaultDir);
    makeFixtureDb(dbPath, [
      sampleRow(),
      sampleRow({
        id: 2,
        title: "Second observation",
        narrative: "Has <private>sensitive</private> content.",
        created_at: "2026-04-02T09:30:00Z",
        created_at_epoch: 1_775_466_600,
      }),
      sampleRow({
        id: 3,
        title: "Third observation",
        subtitle: "",
        narrative: "",
        text: "Body-only content.",
        created_at: "2026-04-02T10:15:00Z",
        created_at_epoch: 1_775_469_300,
      }),
    ]);
  });

  afterEach(() => {
    rmSync(workDir, { recursive: true, force: true });
  });

  function baseOpts(over: Partial<MigrationOptions> = {}): MigrationOptions {
    return {
      sourceDb: dbPath,
      vault,
      archive: "preserve",
      ...over,
    };
  }

  it("migrates three observations into per-date buckets", () => {
    const stats = migrate(baseOpts());
    expect(stats.status).toBe("ok");
    expect(stats.observations.migrated).toBe(3);
    expect(stats.observations.skippedDedup).toBe(0);
    expect(stats.errors).toEqual([]);
    const bucket1 = join(stats.exportRoot, "2026-04-01");
    const bucket2 = join(stats.exportRoot, "2026-04-02");
    expect(existsSync(join(bucket1, "cm-obs-1.md"))).toBe(true);
    expect(existsSync(join(bucket2, "cm-obs-2.md"))).toBe(true);
    expect(existsSync(join(bucket2, "cm-obs-3.md"))).toBe(true);
  });

  it("strips <private> content and counts the substitutions", () => {
    const stats = migrate(baseOpts());
    expect(stats.redactionsApplied).toBeGreaterThanOrEqual(1);
    const second = readFileSync(
      join(stats.exportRoot, "2026-04-02", "cm-obs-2.md"),
      "utf8",
    );
    expect(second).toContain("[REDACTED]");
    expect(second).not.toContain("<private>");
    expect(second).not.toContain("sensitive");
  });

  it("rerunning the same migration is fully idempotent", () => {
    const first = migrate(baseOpts());
    expect(first.observations.migrated).toBe(3);

    const second = migrate(baseOpts());
    expect(second.observations.migrated).toBe(0);
    expect(second.observations.rewritten).toBe(0);
    expect(second.observations.skippedDedup).toBe(3);
  });

  it("rewrites a record when its content_hash changes", () => {
    const first = migrate(baseOpts());
    expect(first.observations.migrated).toBe(3);

    // Mutate the source row to force a hash drift.
    const db = new Database(dbPath);
    try {
      db.prepare(
        "UPDATE observations SET narrative = ? WHERE id = 1",
      ).run("Newly edited narrative.");
    } finally {
      db.close();
    }

    const second = migrate(baseOpts());
    expect(second.observations.skippedDedup).toBe(2);
    expect(second.observations.rewritten).toBe(1);
    expect(second.observations.migrated).toBe(0);
  });

  it("dry-run writes nothing to disk but reports counts", () => {
    const stats = migrate(baseOpts({ dryRun: true }));
    expect(stats.dryRun).toBe(true);
    expect(stats.observations.migrated).toBe(3);
    // The export root must not exist after a dry-run (no file, no folder).
    expect(existsSync(stats.exportRoot)).toBe(false);
    expect(existsSync(join(stats.exportRoot, "_manifest.json"))).toBe(false);
    expect(stats.archive.status).toBe("skipped (dry-run)");
  });

  it("migrates session_summaries and user_prompts heads into their dirs", () => {
    const stats = migrate(baseOpts());
    expect(stats.sessionSummaries.migrated).toBe(1);
    expect(stats.userPromptsHeads.migrated).toBe(1);
    expect(
      existsSync(join(stats.exportRoot, "_sessions", "cm-sess-1.md")),
    ).toBe(true);
    expect(
      existsSync(join(stats.exportRoot, "_prompts", "cm-prompt-1.md")),
    ).toBe(true);
  });

  it("writes a manifest that records the run", () => {
    const stats = migrate(baseOpts());
    const manifestRaw = readFileSync(
      join(stats.exportRoot, "_manifest.json"),
      "utf8",
    );
    const manifest = JSON.parse(manifestRaw) as {
      schema: string;
      observations: { migrated: number };
      archive: { mode: string; status: string };
    };
    expect(manifest.schema).toBe("mneme-migration-manifest/1");
    expect(manifest.observations.migrated).toBe(3);
    expect(manifest.archive.mode).toBe("preserve");
    expect(manifest.archive.status).toBe("preserved");
  });

  it("archive=copy snapshots the DB and leaves the source intact", () => {
    const stats = migrate(baseOpts({ archive: "copy" }));
    expect(stats.archive.mode).toBe("copy");
    expect(stats.archive.status).toBe("copied");
    expect(
      existsSync(join(stats.exportRoot, "_archive", "claude-mem.db")),
    ).toBe(true);
    expect(existsSync(dbPath)).toBe(true);
  });

  it("archive=move without confirmDelete refuses to run", () => {
    const stats = migrate(baseOpts({ archive: "move" }));
    expect(stats.status).toBe("error");
    expect(
      stats.errors.some((e) => e.includes("confirmDelete")),
    ).toBe(true);
    expect(existsSync(dbPath)).toBe(true);
  });

  it("archive=move with confirmDelete copies then deletes the source", () => {
    const stats = migrate(
      baseOpts({ archive: "move", confirmDelete: true }),
    );
    expect(stats.archive.status).toBe("moved");
    expect(
      existsSync(join(stats.exportRoot, "_archive", "claude-mem.db")),
    ).toBe(true);
    expect(existsSync(dbPath)).toBe(false);
  });

  it("reports an error when the source DB is missing", () => {
    rmSync(dbPath, { force: true });
    const stats = migrate(baseOpts());
    expect(stats.status).toBe("error");
    expect(stats.errors[0]).toContain("Source DB not found");
  });

  it("frontmatter on emitted observation files carries the canonical source tag", () => {
    const stats = migrate(baseOpts());
    const file = readFileSync(
      join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
      "utf8",
    );
    expect(file).toContain("source: 'claude-mem-v13.2.0'");
    expect(file).toContain("type: 'observation'");
    expect(file).toMatch(/content_hash: '[0-9a-f]{64}'/);
  });

  it("writes the canonical type frontmatter for all three record kinds (Phase J Day 1 regression)", () => {
    const stats = migrate(baseOpts());
    expect(stats.status).toBe("ok");

    const obsFile = readFileSync(
      join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
      "utf8",
    );
    expect(obsFile).toContain("type: 'observation'");

    const sessFile = readFileSync(
      join(stats.exportRoot, "_sessions", "cm-sess-1.md"),
      "utf8",
    );
    expect(sessFile).toContain("type: 'session_summary'");

    const promptFile = readFileSync(
      join(stats.exportRoot, "_prompts", "cm-prompt-1.md"),
      "utf8",
    );
    expect(promptFile).toContain("type: 'user_prompt'");
  });
});

describe("regression: code-review fixes", () => {
  function tmpWork(): { workDir: string; dbPath: string; vault: VaultConfig } {
    const workDir = mkdtempSync(join(tmpdir(), "mneme-rev-"));
    const vaultDir = join(workDir, "vault");
    mkdirSync(join(vaultDir, ".mneme"), { recursive: true });
    return {
      workDir,
      dbPath: join(workDir, "claude-mem.db"),
      vault: VaultConfig.fromPath(vaultDir),
    };
  }

  const OBS_DDL =
    "CREATE TABLE observations (id INTEGER PRIMARY KEY, memory_session_id TEXT," +
    " project TEXT, text TEXT, type TEXT, title TEXT, subtitle TEXT, facts TEXT," +
    " narrative TEXT, concepts TEXT, files_read TEXT, files_modified TEXT," +
    " prompt_number INTEGER, discovery_tokens INTEGER, created_at TEXT," +
    " created_at_epoch INTEGER, content_hash TEXT, generated_by_model TEXT," +
    " agent_type TEXT, agent_id TEXT, metadata TEXT)";

  it("archive=move with confirmDelete but table errors preserves the source DB", () => {
    // A source DB missing the observations table forces a migration error.
    // The destructive move must refuse to delete the source so the operator
    // can re-run after the underlying failure is understood.
    const { workDir, dbPath, vault } = tmpWork();
    try {
      const db = new Database(dbPath);
      db.prepare(
        "CREATE TABLE session_summaries (id INTEGER PRIMARY KEY, summary TEXT, created_at TEXT)",
      ).run();
      db.prepare(
        "INSERT INTO session_summaries (id, summary, created_at) VALUES (1, 'x', '2026-04-01T00:00:00Z')",
      ).run();
      db.close();

      const stats = migrate({
        sourceDb: dbPath,
        vault,
        archive: "move",
        confirmDelete: true,
      });
      expect(stats.status).toBe("error");
      expect(existsSync(dbPath)).toBe(true);
      expect(stats.archive.status).toContain("refused");
    } finally {
      rmSync(workDir, { recursive: true, force: true });
    }
  });

  it("redacts <private> blocks in observation facts and concepts", () => {
    const { workDir, dbPath, vault } = tmpWork();
    try {
      const db = new Database(dbPath);
      db.prepare(OBS_DDL).run();
      db.prepare(
        "INSERT INTO observations (id, text, type, title, facts, concepts," +
          " created_at, created_at_epoch) VALUES (1, 'body', 'discovery', 'T'," +
          " 'fact <private>SECRETFACT</private> end'," +
          " '<private>SECRETCONCEPT</private>', '2026-04-01T00:00:00Z', 1775376000)",
      ).run();
      db.close();

      const stats = migrate({ sourceDb: dbPath, vault, archive: "preserve" });
      const file = readFileSync(
        join(stats.exportRoot, "2026-04-01", "cm-obs-1.md"),
        "utf8",
      );
      expect(file).not.toContain("SECRETFACT");
      expect(file).not.toContain("SECRETCONCEPT");
      expect(file).not.toContain("<private>");
      expect(file).toContain("[REDACTED]");
    } finally {
      rmSync(workDir, { recursive: true, force: true });
    }
  });

  it("redacts a <private> block straddling the user-prompt truncation boundary", () => {
    const { workDir, dbPath, vault } = tmpWork();
    try {
      const db = new Database(dbPath);
      db.prepare(
        "CREATE TABLE user_prompts (id INTEGER PRIMARY KEY, prompt_text TEXT, created_at TEXT)",
      ).run();
      // Opening tag before char 200, closing tag after: a truncate-then-redact
      // order would cut the closing tag off and leak the visible head.
      const prompt =
        "x".repeat(180) +
        "<private>" +
        "S".repeat(60) +
        "</private>" +
        "y".repeat(40);
      db.prepare(
        "INSERT INTO user_prompts (id, prompt_text, created_at) VALUES (1, ?, '2026-04-01T00:00:00Z')",
      ).run(prompt);
      db.close();

      const stats = migrate({ sourceDb: dbPath, vault, archive: "preserve" });
      const file = readFileSync(
        join(stats.exportRoot, "_prompts", "cm-prompt-1.md"),
        "utf8",
      );
      expect(file).not.toContain("<private>");
      expect(file).not.toContain("SSSSSS");
    } finally {
      rmSync(workDir, { recursive: true, force: true });
    }
  });
});
