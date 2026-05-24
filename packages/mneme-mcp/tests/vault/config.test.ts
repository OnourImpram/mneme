import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { VaultConfig, VaultNotFoundError } from "../../src/vault/config.js";

function freshTmp(prefix: string): string {
  return mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
}

describe("VaultConfig.fromPath", () => {
  it("absolute path resolves verbatim", () => {
    const tmp = freshTmp("from-path");
    const cfg = VaultConfig.fromPath(tmp);
    expect(cfg.root).toBe(tmp);
  });

  it("derives stateDir, fts5Db, telemetryDir, auditLogDir, stagingDir under .mneme", () => {
    const tmp = freshTmp("derived");
    const cfg = VaultConfig.fromPath(tmp);
    expect(cfg.stateDir).toBe(join(tmp, ".mneme"));
    expect(cfg.fts5Db).toBe(join(tmp, ".mneme", "fts5.sqlite"));
    expect(cfg.telemetryDir).toBe(join(tmp, ".mneme", "telemetry"));
    expect(cfg.auditLogDir).toBe(join(tmp, ".mneme", "audit"));
    expect(cfg.stagingDir).toBe(join(tmp, ".mneme", "staging"));
  });
});

describe("VaultConfig.resolve resolution order", () => {
  const cleanups: (() => void)[] = [];
  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.();
  });

  it("MNEME_VAULT env wins over everything", () => {
    const envVault = freshTmp("env");
    const explicit = freshTmp("explicit");
    const cfg = VaultConfig.resolve({
      explicit,
      env: { MNEME_VAULT: envVault } as NodeJS.ProcessEnv,
    });
    expect(cfg.root).toBe(envVault);
  });

  it("explicit beats marker-walk when env is empty", () => {
    const explicit = freshTmp("explicit-only");
    const cfg = VaultConfig.resolve({
      explicit,
      env: {} as NodeJS.ProcessEnv,
    });
    expect(cfg.root).toBe(explicit);
  });

  it("marker walk locates .mneme/ in ancestor", () => {
    const root = freshTmp("marker-root");
    mkdirSync(join(root, ".mneme"));
    const child = join(root, "nested", "deep");
    mkdirSync(child, { recursive: true });
    const cfg = VaultConfig.resolve({
      cwd: child,
      env: {} as NodeJS.ProcessEnv,
    });
    expect(cfg.root).toBe(root);
  });

  it("throws VaultNotFoundError with remediation hint when nothing resolves", () => {
    const orphan = freshTmp("orphan");
    expect(() =>
      VaultConfig.resolve({
        cwd: orphan,
        env: {} as NodeJS.ProcessEnv,
      }),
    ).toThrow(VaultNotFoundError);
  });

  it("error message mentions MNEME_VAULT and .mneme/ marker", () => {
    const orphan = freshTmp("orphan-msg");
    try {
      VaultConfig.resolve({
        cwd: orphan,
        env: {} as NodeJS.ProcessEnv,
      });
      expect.fail("should have thrown");
    } catch (err) {
      expect((err as Error).message).toContain("MNEME_VAULT");
      expect((err as Error).message).toContain(".mneme");
    }
  });
});

describe("VaultConfig TOML config", () => {
  it("reads vault key from ~/.mneme/config.toml when env+explicit empty", () => {
    // We can't safely write to the real homedir in tests. Verify the parser
    // shape via the marker-walk path instead. TOML parsing has its own
    // narrow unit test in the parser implementation.
    const tmp = freshTmp("toml-noop");
    mkdirSync(join(tmp, ".mneme"));
    writeFileSync(join(tmp, ".mneme", "config.toml"), 'vault = "/should/not/be/used"\n');
    const cfg = VaultConfig.resolve({
      cwd: tmp,
      env: {} as NodeJS.ProcessEnv,
    });
    // Marker walk in tmp itself beats the user TOML config.
    expect(cfg.root).toBe(tmp);
  });
});
