import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, parse, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { VaultConfig, VaultNotFoundError } from "../../src/vault/config.js";

function freshTmp(prefix: string): string {
	return mkdtempSync(join(tmpdir(), `mneme-config-fault-${prefix}-`));
}

describe("VaultConfig fallback and fault behavior", () => {
	it("expands home paths without requiring the target to exist", () => {
		expect(VaultConfig.fromPath("~").root).toBe(resolve(homedir()));
		expect(VaultConfig.fromPath("~/mneme-nested").root).toBe(
			resolve(join(homedir(), "mneme-nested")),
		);
	});

	it("reads default_scope from the operator TOML file", () => {
		const home = freshTmp("scope-home");
		const vault = VaultConfig.fromPath(freshTmp("scope-vault"));
		mkdirSync(join(home, ".mneme"));
		writeFileSync(
			join(home, ".mneme", "config.toml"),
			'default_scope = "clinical" # local policy\n',
			"utf8",
		);

		expect(vault.defaultScope({} as NodeJS.ProcessEnv, home)).toBe("clinical");
	});

	it("uses the default home vault only after config and marker lookup miss", () => {
		const home = freshTmp("default-home");
		const cwd = join(home, "work", "nested");
		const defaultVault = join(home, "mneme-vault");
		mkdirSync(cwd, { recursive: true });
		mkdirSync(defaultVault);

		const resolved = VaultConfig.resolve({
			cwd,
			home,
			env: {} as NodeJS.ProcessEnv,
		});

		expect(resolved.root).toBe(defaultVault);
	});

	it("fails closed when config.toml is unreadable as a file", () => {
		const home = freshTmp("bad-config-home");
		const cwd = join(home, "work");
		const configPath = join(home, ".mneme", "config.toml");
		mkdirSync(cwd, { recursive: true });
		mkdirSync(configPath, { recursive: true });
		const vault = VaultConfig.fromPath(freshTmp("bad-config-vault"));

		expect(vault.defaultScope({} as NodeJS.ProcessEnv, home)).toBe("default");
		expect(() =>
			VaultConfig.resolve({ cwd, home, env: {} as NodeJS.ProcessEnv }),
		).toThrow(VaultNotFoundError);
	});

	it("terminates marker traversal at a filesystem root", () => {
		const home = freshTmp("root-home");
		const filesystemRoot = parse(home).root;

		expect(() =>
			VaultConfig.resolve({
				cwd: filesystemRoot,
				home,
				env: {} as NodeJS.ProcessEnv,
			}),
		).toThrow(VaultNotFoundError);
	});
});
