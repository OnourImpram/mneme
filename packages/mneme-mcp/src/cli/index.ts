#!/usr/bin/env node
/**
 * `mneme-migrate` CLI entry point.
 *
 * A deliberately small argv parser with no external dependency. The
 * surface area is one subcommand today (`migrate-from-claude-mem`) and
 * a handful of flags. Anything richer (interactive prompts, completion)
 * lives in the install plugin's Click CLI on the Python side.
 *
 * Exit codes:
 *
 *   0  Success, no errors reported.
 *   1  Migration ran but reported one or more errors (see stats.errors).
 *   2  Argv was malformed (unknown flag, missing required value, etc.).
 */

import { homedir } from "node:os";
import { join } from "node:path";
import { VaultConfig, VaultNotFoundError } from "../vault/config.js";
import { type ArchiveMode, type MigrationOptions, migrate } from "./migrate.js";

const HELP = `mneme-migrate - export claude-mem v13.2.0 SQLite into the mneme vault

USAGE
  mneme-migrate migrate-from-claude-mem [OPTIONS]

OPTIONS
  --source PATH          Source SQLite path. Defaults to ~/.claude-mem/claude-mem.db
  --vault PATH           Vault root override. Otherwise the standard
                         MNEME_VAULT / .mneme marker / ~/mneme-vault resolution applies.
  --archive MODE         preserve (default) | copy | move.
                         copy writes the DB to vault/imported/claude-mem/_archive/.
                         move copies then deletes the source. Requires --confirm-delete.
  --confirm-delete       Required two-factor flag for --archive=move.
  --dry-run              Plan only. No files written. Stats include what would happen.
  -h, --help             Show this message.

EXIT CODES
  0  Success.
  1  Migration ran but had errors. See the printed stats.errors list.
  2  Argv was malformed.
`;

interface ParsedArgs {
	subcommand: string | null;
	source: string | null;
	vault: string | null;
	archive: ArchiveMode;
	confirmDelete: boolean;
	dryRun: boolean;
	help: boolean;
	errors: string[];
}

function parseArgs(argv: string[]): ParsedArgs {
	const out: ParsedArgs = {
		subcommand: null,
		source: null,
		vault: null,
		archive: "preserve",
		confirmDelete: false,
		dryRun: false,
		help: false,
		errors: [],
	};
	for (let i = 0; i < argv.length; i++) {
		const a = argv[i];
		if (a === "-h" || a === "--help") {
			out.help = true;
			continue;
		}
		if (a === "--dry-run") {
			out.dryRun = true;
			continue;
		}
		if (a === "--confirm-delete") {
			out.confirmDelete = true;
			continue;
		}
		if (a === "--source" || a === "--vault" || a === "--archive") {
			const next = argv[i + 1];
			if (next === undefined || next.startsWith("-")) {
				out.errors.push(`Flag ${a} requires a value.`);
				continue;
			}
			i += 1;
			if (a === "--source") {
				out.source = next;
			} else if (a === "--vault") {
				out.vault = next;
			} else {
				if (next !== "preserve" && next !== "copy" && next !== "move") {
					out.errors.push(
						`--archive must be one of preserve|copy|move, got: ${next}`,
					);
				} else {
					out.archive = next;
				}
			}
			continue;
		}
		if (a?.startsWith("-")) {
			out.errors.push(`Unknown flag: ${a}`);
			continue;
		}
		if (a !== undefined && out.subcommand === null) {
			out.subcommand = a;
			continue;
		}
		if (a !== undefined) {
			out.errors.push(`Unexpected positional argument: ${a}`);
		}
	}
	return out;
}

function defaultSourceDb(): string {
	return join(homedir(), ".claude-mem", "claude-mem.db");
}

function resolveVault(explicit: string | null): VaultConfig {
	if (explicit !== null) {
		return VaultConfig.fromPath(explicit);
	}
	return VaultConfig.resolve();
}

function emit(payload: unknown): void {
	process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}

export function runCli(argv: string[]): number {
	const args = parseArgs(argv);

	if (args.help && args.subcommand === null) {
		process.stdout.write(HELP);
		return 0;
	}
	if (args.errors.length > 0) {
		process.stderr.write(`${args.errors.join("\n")}\n\n${HELP}`);
		return 2;
	}
	if (args.subcommand === null) {
		process.stderr.write(`Missing subcommand.\n\n${HELP}`);
		return 2;
	}
	if (args.subcommand !== "migrate-from-claude-mem") {
		process.stderr.write(`Unknown subcommand: ${args.subcommand}\n\n${HELP}`);
		return 2;
	}

	let vault: VaultConfig;
	try {
		vault = resolveVault(args.vault);
	} catch (err) {
		if (err instanceof VaultNotFoundError) {
			emit({
				status: "error",
				errors: [err.message],
			});
			return 1;
		}
		throw err;
	}

	const opts: MigrationOptions = {
		sourceDb: args.source ?? defaultSourceDb(),
		vault,
		archive: args.archive,
		confirmDelete: args.confirmDelete,
		dryRun: args.dryRun,
	};

	const stats = migrate(opts);
	emit(stats);
	return stats.status === "ok" ? 0 : 1;
}

const isMain =
	typeof process !== "undefined" &&
	Array.isArray(process.argv) &&
	process.argv[1] !== undefined &&
	/[\\/](?:mneme-migrate|index\.(?:js|ts))$/.test(process.argv[1]);

if (isMain) {
	const code = runCli(process.argv.slice(2));
	process.exit(code);
}
