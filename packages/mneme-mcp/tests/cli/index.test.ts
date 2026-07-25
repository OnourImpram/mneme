import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const doubles = vi.hoisted(() => {
	class TestVaultNotFoundError extends Error {
		constructor(message: string) {
			super(message);
			this.name = "VaultNotFoundError";
		}
	}

	return {
		TestVaultNotFoundError,
		fromPath: vi.fn(),
		resolve: vi.fn(),
		migrate: vi.fn(),
		rollbackMigration: vi.fn(),
	};
});

vi.mock("../../src/vault/config.js", () => ({
	VaultConfig: {
		fromPath: doubles.fromPath,
		resolve: doubles.resolve,
	},
	VaultNotFoundError: doubles.TestVaultNotFoundError,
}));

vi.mock("../../src/cli/migrate.js", () => ({
	migrate: doubles.migrate,
}));

vi.mock("../../src/cli/migration_rollback.js", () => ({
	rollbackMigration: doubles.rollbackMigration,
}));

import { runCli } from "../../src/cli/index.js";

function captureStreams() {
	const stdout = vi
		.spyOn(process.stdout, "write")
		.mockImplementation((() => true) as typeof process.stdout.write);
	const stderr = vi
		.spyOn(process.stderr, "write")
		.mockImplementation((() => true) as typeof process.stderr.write);
	return { stdout, stderr };
}

function emittedText(spy: ReturnType<typeof vi.spyOn>): string {
	return spy.mock.calls.map((call) => String(call[0])).join("");
}

describe("mneme-migrate CLI dispatch", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		doubles.fromPath.mockImplementation((root: string) => ({ root }));
		doubles.resolve.mockReturnValue({ root: "/resolved-vault" });
		doubles.migrate.mockReturnValue({ status: "ok", errors: [] });
		doubles.rollbackMigration.mockReturnValue({ status: "ok", errors: [] });
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("prints help without resolving a vault", () => {
		const { stdout, stderr } = captureStreams();

		expect(runCli(["--help"])).toBe(0);

		expect(emittedText(stdout)).toContain("migrate-from-claude-mem");
		expect(stderr).not.toHaveBeenCalled();
		expect(doubles.resolve).not.toHaveBeenCalled();
	});

	it("reports missing values, invalid archive modes, unknown flags, and extra arguments", () => {
		const { stderr } = captureStreams();

		const code = runCli([
			"migrate-from-claude-mem",
			"--source",
			"--dry-run",
			"--archive",
			"destroy",
			"--unknown",
			"extra",
		]);

		expect(code).toBe(2);
		const output = emittedText(stderr);
		expect(output).toContain("Flag --source requires a value");
		expect(output).toContain("--archive must be one of preserve|copy|move");
		expect(output).toContain("Unknown flag: --unknown");
		expect(output).toContain("Unexpected positional argument: extra");
		expect(doubles.migrate).not.toHaveBeenCalled();
	});

	it("rejects a missing subcommand", () => {
		const { stderr } = captureStreams();

		expect(runCli([])).toBe(2);

		expect(emittedText(stderr)).toContain("Missing subcommand");
	});

	it("rejects an unknown subcommand", () => {
		const { stderr } = captureStreams();

		expect(runCli(["unknown-command"])).toBe(2);

		expect(emittedText(stderr)).toContain(
			"Unknown subcommand: unknown-command",
		);
	});

	it("passes every explicit migration option and returns success", () => {
		const { stdout } = captureStreams();

		const code = runCli([
			"migrate-from-claude-mem",
			"--source",
			"/data/claude-mem.db",
			"--vault",
			"/vault",
			"--archive",
			"move",
			"--confirm-delete",
			"--dry-run",
			"--help",
		]);

		expect(code).toBe(0);
		expect(doubles.fromPath).toHaveBeenCalledWith("/vault");
		expect(doubles.migrate).toHaveBeenCalledWith(
			expect.objectContaining({
				sourceDb: "/data/claude-mem.db",
				archive: "move",
				confirmDelete: true,
				dryRun: true,
			}),
		);
		expect(emittedText(stdout)).toContain('"status": "ok"');
	});

	it("accepts explicit preserve and copy archive modes", () => {
		captureStreams();

		expect(
			runCli([
				"migrate-from-claude-mem",
				"--vault",
				"/vault",
				"--archive",
				"preserve",
			]),
		).toBe(0);
		expect(
			runCli([
				"migrate-from-claude-mem",
				"--vault",
				"/vault",
				"--archive",
				"copy",
			]),
		).toBe(0);

		expect(doubles.migrate.mock.calls[0]?.[0]).toEqual(
			expect.objectContaining({ archive: "preserve" }),
		);
		expect(doubles.migrate.mock.calls[1]?.[0]).toEqual(
			expect.objectContaining({ archive: "copy" }),
		);
	});

	it("uses resolved vault and default source when overrides are absent", () => {
		captureStreams();

		expect(runCli(["migrate-from-claude-mem"])).toBe(0);

		expect(doubles.resolve).toHaveBeenCalledOnce();
		expect(doubles.migrate).toHaveBeenCalledWith(
			expect.objectContaining({
				vault: { root: "/resolved-vault" },
				sourceDb: expect.stringMatching(
					/[\\/]\.claude-mem[\\/]claude-mem\.db$/,
				),
			}),
		);
	});

	it("returns a structured error when vault discovery fails", () => {
		const { stdout } = captureStreams();
		doubles.resolve.mockImplementation(() => {
			throw new doubles.TestVaultNotFoundError("vault unavailable");
		});

		expect(runCli(["migrate-from-claude-mem"])).toBe(1);

		expect(emittedText(stdout)).toContain("vault unavailable");
		expect(doubles.migrate).not.toHaveBeenCalled();
	});

	it("does not hide unexpected vault resolution faults", () => {
		captureStreams();
		doubles.resolve.mockImplementation(() => {
			throw new TypeError("resolver contract broken");
		});

		expect(() => runCli(["migrate-from-claude-mem"])).toThrow(
			"resolver contract broken",
		);
	});

	it("returns one when migration reports an error status", () => {
		const { stdout } = captureStreams();
		doubles.migrate.mockReturnValue({
			status: "error",
			errors: ["source database is corrupt"],
		});

		expect(runCli(["migrate-from-claude-mem", "--vault", "/vault"])).toBe(1);
		expect(emittedText(stdout)).toContain("source database is corrupt");
	});

	it("dispatches rollback with explicit source restoration consent", () => {
		const { stdout } = captureStreams();

		const code = runCli([
			"rollback",
			"--manifest",
			"/vault/.mneme/migrations/run/rollback.json",
			"--vault",
			"/vault",
			"--confirm-source-restore",
			"--source",
			"/home/operator/.claude-mem/claude-mem.db",
		]);

		expect(code).toBe(0);
		expect(doubles.rollbackMigration).toHaveBeenCalledWith({
			vault: { root: "/vault" },
			manifestPath: "/vault/.mneme/migrations/run/rollback.json",
			confirmSourceRestore: true,
			sourceRestorePath: "/home/operator/.claude-mem/claude-mem.db",
		});
		expect(emittedText(stdout)).toContain('"status": "ok"');
	});

	it("requires a rollback manifest", () => {
		const { stderr } = captureStreams();

		expect(runCli(["rollback", "--vault", "/vault"])).toBe(2);

		expect(emittedText(stderr)).toContain("rollback requires --manifest");
		expect(doubles.rollbackMigration).not.toHaveBeenCalled();
	});
});
