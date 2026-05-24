/**
 * Helper that builds a VaultConfig-shaped temp directory plus an
 * indexed FTS5 database inside its `.mneme/` state dir. Returns the
 * VaultConfig instance so tool tests get a self-contained workspace.
 */

import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { VaultConfig } from "../../src/vault/config.js";
import { buildTestDb, type TestDoc } from "./fts5_fixture.js";

export interface TempVault {
  vault: VaultConfig;
  rootDir: string;
}

export function makeTempVault(prefix: string, docs: TestDoc[] = []): TempVault {
  const rootDir = mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
  mkdirSync(join(rootDir, ".mneme"), { recursive: true });
  const vault = VaultConfig.fromPath(rootDir);
  if (docs.length > 0) {
    buildTestDb(vault.fts5Db, docs);
  }
  return { vault, rootDir };
}

export function defaultDocs(): TestDoc[] {
  return [
    {
      path: "daily/2026-05-18.md",
      title: "Reciprocal Rank Fusion",
      titleNormalized: "reciprocal rank fusion",
      contentRaw: "RRF k equals sixty fuses ranked lists. Robust across tasks.",
      contentNormalized: "rrf k equals sixty fuses ranked lists. robust across tasks.",
      mtime: 1_716_000_000,
      frontmatterType: "session",
      sessionId: "s-2026-05-18",
    },
    {
      path: "reference/vault-native.md",
      title: "Vault Native Memory",
      titleNormalized: "vault native memory",
      contentRaw: "Markdown is ground truth. Hybrid retrieval beats single backend.",
      contentNormalized: "markdown is ground truth. hybrid retrieval beats single backend.",
      mtime: 1_716_500_000,
      frontmatterType: "reference",
    },
    {
      path: "reference/privacy.md",
      title: "Privacy Redaction",
      titleNormalized: "privacy redaction",
      contentRaw: "Sensitive content is stripped at staging. Audit log stays.",
      contentNormalized: "sensitive content is stripped at staging. audit log stays.",
      mtime: 1_717_000_000,
      frontmatterType: "reference",
    },
  ];
}
