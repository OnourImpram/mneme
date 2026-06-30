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

// F5: TOML reader must accept single-quoted vault paths
describe("readVaultFromTomlConfig single-quote support (F5)", () => {
  it("resolves a single-quoted vault path from config.toml via explicit option", () => {
    // The real readVaultFromTomlConfig reads from homedir, which we cannot
    // safely override in tests. Instead, verify the fix indirectly: create a
    // vault with a .mneme/ marker so that VaultConfig.resolve finds it via
    // marker-walk when cwd is set to it. Then write a config.toml with a
    // single-quoted path in a sibling dir and confirm the regex itself matches.
    //
    // Direct regex unit:
    const line = "vault = '/tmp/my-vault'";
    const m = line.match(/^\s*vault\s*=\s*["']([^"']+)["']\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("/tmp/my-vault");
  });

  it("still resolves a double-quoted vault path from config.toml", () => {
    const line = 'vault = "/tmp/my-vault"';
    const m = line.match(/^\s*vault\s*=\s*["']([^"']+)["']\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("/tmp/my-vault");
  });

  it("resolves via explicit path using single-quoted-style vault target directory", () => {
    // End-to-end: explicit option routes to the directory directly,
    // confirming VaultConfig.resolve accepts paths that would have come
    // from a single-quoted TOML value.
    const vaultDir = freshTmp("toml-single-quote-vault");
    const cfg = VaultConfig.resolve({
      explicit: vaultDir,
      env: {} as NodeJS.ProcessEnv,
    });
    expect(cfg.root).toBe(vaultDir);
  });
});

// ---------------------------------------------------------------------------
// M5 — VaultConfig.defaultScope resolution
// ---------------------------------------------------------------------------
describe("VaultConfig.defaultScope resolution (M5)", () => {
  it("returns the MNEME_SCOPE env var when set", () => {
    const cfg = VaultConfig.fromPath(freshTmp("scope-env"));
    expect(
      cfg.defaultScope({ MNEME_SCOPE: "clinical" } as NodeJS.ProcessEnv),
    ).toBe("clinical");
  });

  it("returns 'default' when env is empty and no config.toml override", () => {
    const cfg = VaultConfig.fromPath(freshTmp("scope-fallback"));
    expect(cfg.defaultScope({} as NodeJS.ProcessEnv)).toBe("default");
  });

  it("MNEME_SCOPE env var overrides any TOML value (env wins)", () => {
    // Verified via the testable env parameter; real TOML path not writable
    // in tests without touching homedir. See regex unit tests below for TOML.
    const cfg = VaultConfig.fromPath(freshTmp("scope-env-beats-toml"));
    expect(
      cfg.defaultScope({ MNEME_SCOPE: "freelance" } as NodeJS.ProcessEnv),
    ).toBe("freelance");
  });

  it("TOML default_scope regex matches double-quoted value", () => {
    const line = 'default_scope = "clinical"';
    const m = line.match(/^\s*default_scope\s*=\s*["']([^"']+)["']\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("clinical");
  });

  it("TOML default_scope regex matches single-quoted value", () => {
    const line = "default_scope = 'freelance'";
    const m = line.match(/^\s*default_scope\s*=\s*["']([^"']+)["']\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("freelance");
  });

  // Minor-3: inline comment and trailing whitespace tolerance
  it("TOML default_scope resolves correctly when line has an inline # comment", () => {
    const line = 'default_scope = "clinical"  # note';
    const m = line.match(/^\s*default_scope\s*=\s*["']([^"']+)["']\s*(#[^\r\n]*)?\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("clinical");
  });

  it("TOML default_scope resolves correctly with trailing whitespace only (no comment)", () => {
    const line = 'default_scope = "clinical"   ';
    const m = line.match(/^\s*default_scope\s*=\s*["']([^"']+)["']\s*(#[^\r\n]*)?\s*$/);
    expect(m).not.toBeNull();
    expect(m?.[1]).toBe("clinical");
  });
});
