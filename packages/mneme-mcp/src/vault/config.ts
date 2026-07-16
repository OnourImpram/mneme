/**
 * Vault location resolution and derived paths.
 *
 * Mirrors the resolution order documented in `mneme_core.vault.config`:
 *
 *   1. MNEME_VAULT environment variable
 *   2. The explicit path passed to `VaultConfig.resolve({ explicit })`
 *   3. The `vault` key in `~/.mneme/config.toml`
 *   4. The nearest ancestor of cwd that contains a `.mneme/` marker
 *   5. `~/mneme-vault` if it exists
 *
 * If none resolve, `VaultNotFoundError` is thrown with a remediation hint.
 *
 * TS does NOT depend on a TOML parser unless step 3 is reached AND the
 * file exists, so the dependency surface stays minimal in the common
 * case. We parse a tiny subset (key = "value" lines) inline.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve as resolvePath } from "node:path";
import { concreteScopeOrNull } from "../scope.js";

export const MARKER_DIR = ".mneme";
export const CONFIG_FILENAME = "config.toml";
export const DEFAULT_VAULT_NAME = "mneme-vault";

export class VaultNotFoundError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "VaultNotFoundError";
	}
}

export interface ResolveOptions {
	explicit?: string;
	cwd?: string;
	env?: NodeJS.ProcessEnv;
}

export class VaultConfig {
	readonly root: string;

	private constructor(root: string) {
		this.root = root;
	}

	get stateDir(): string {
		return join(this.root, MARKER_DIR);
	}

	get fts5Db(): string {
		return join(this.stateDir, "fts5.sqlite");
	}

	get telemetryDir(): string {
		return join(this.stateDir, "telemetry");
	}

	get auditLogDir(): string {
		return join(this.stateDir, "audit");
	}

	get stagingDir(): string {
		return join(this.stateDir, "staging");
	}

	get kgQueueDir(): string {
		return join(this.stateDir, "kg-queue");
	}

	get kgProcessedDir(): string {
		return join(this.stateDir, "kg-processed");
	}

	get kgActiveFlag(): string {
		return join(this.stateDir, "kg-active.flag");
	}

	get kgCredentialsPath(): string {
		return join(this.stateDir, "neo4j-credentials.json");
	}

	get kgCommunityRefreshFlag(): string {
		return join(this.stateDir, "kg-community-refresh.flag");
	}

	get kgWorkerLog(): string {
		return join(this.telemetryDir, "kg-worker.jsonl");
	}

	get kgCostLedger(): string {
		return join(this.stateDir, "cost-ledger.jsonl");
	}

	/**
	 * Default scope applied to read tools when no explicit scope arg is supplied.
	 *
	 * Resolution order:
	 *   1. MNEME_SCOPE environment variable
	 *   2. `default_scope = "..."` key in ~/.mneme/config.toml
	 *   3. The literal 'default'
	 *
	 * Invalid values and the wildcard are ignored. A cross-scope read must be
	 * requested explicitly on the individual MCP call with scope="*".
	 *
	 * Pass a custom `env` in tests to control the env without mutating
	 * process.env.
	 */
	defaultScope(env?: NodeJS.ProcessEnv): string {
		const e = env ?? process.env;
		const envScope = concreteScopeOrNull(e.MNEME_SCOPE);
		if (envScope !== null) return envScope;
		const tomlScope = concreteScopeOrNull(readDefaultScopeFromTomlConfig());
		if (tomlScope !== null) return tomlScope;
		return "default";
	}

	static fromPath(path: string): VaultConfig {
		return new VaultConfig(resolvePath(expandHome(path)));
	}

	static resolve(opts: ResolveOptions = {}): VaultConfig {
		const env = opts.env ?? process.env;
		const cwd = opts.cwd ?? process.cwd();

		const candidate = resolveCandidate(opts.explicit, env, cwd);
		if (candidate === null) {
			throw new VaultNotFoundError(
				`Could not locate an mneme vault. Set MNEME_VAULT, pass explicit path, add a vault key to ~/.mneme/${CONFIG_FILENAME}, place a ${MARKER_DIR}/ marker in or above the current directory, or create ~/${DEFAULT_VAULT_NAME}.`,
			);
		}
		return new VaultConfig(resolvePath(candidate));
	}
}

function expandHome(p: string): string {
	if (p === "~" || p.startsWith("~/") || p.startsWith("~\\")) {
		return join(homedir(), p.slice(2));
	}
	return p;
}

function resolveCandidate(
	explicit: string | undefined,
	env: NodeJS.ProcessEnv,
	cwd: string,
): string | null {
	const envVault = env.MNEME_VAULT;
	if (envVault && envVault.length > 0) {
		return expandHome(envVault);
	}

	if (explicit !== undefined) {
		return expandHome(explicit);
	}

	const tomlVault = readVaultFromTomlConfig();
	if (tomlVault !== null) {
		return expandHome(tomlVault);
	}

	const markerHit = findMarkerAncestor(cwd);
	if (markerHit !== null) {
		return markerHit;
	}

	const defaultVault = join(homedir(), DEFAULT_VAULT_NAME);
	if (existsSync(defaultVault)) {
		return defaultVault;
	}

	return null;
}

function readVaultFromTomlConfig(): string | null {
	const configPath = join(homedir(), MARKER_DIR, CONFIG_FILENAME);
	if (!existsSync(configPath)) return null;
	try {
		const raw = readFileSync(configPath, "utf8");
		for (const line of raw.split(/\r?\n/)) {
			const m = line.match(/^\s*vault\s*=\s*["']([^"']+)["']\s*$/);
			if (m) return m[1] ?? null;
		}
	} catch {
		return null;
	}
	return null;
}

/**
 * Read the `default_scope` key from `~/.mneme/config.toml` when present.
 * Returns null when the file is absent, the key is missing, or any I/O
 * error occurs.
 */
function readDefaultScopeFromTomlConfig(): string | null {
	const configPath = join(homedir(), MARKER_DIR, CONFIG_FILENAME);
	if (!existsSync(configPath)) return null;
	try {
		const raw = readFileSync(configPath, "utf8");
		for (const line of raw.split(/\r?\n/)) {
			const m = line.match(
				/^\s*default_scope\s*=\s*["']([^"']+)["']\s*(#[^\r\n]*)?\s*$/,
			);
			if (m) return m[1] ?? null;
		}
	} catch {
		return null;
	}
	return null;
}

function findMarkerAncestor(cwd: string): string | null {
	let current = isAbsolute(cwd) ? cwd : resolvePath(cwd);
	while (true) {
		const candidate = join(current, MARKER_DIR);
		if (existsSync(candidate) && statSync(candidate).isDirectory()) {
			return current;
		}
		const parent = dirname(current);
		if (parent === current) return null;
		current = parent;
	}
}
