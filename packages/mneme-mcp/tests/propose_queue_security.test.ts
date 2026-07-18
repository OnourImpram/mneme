import {
	mkdirSync,
	readFileSync,
	symlinkSync,
	utimesSync,
	writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { proposeTool } from "../src/tools/propose.js";
import { makeTempVault } from "./helpers/vault_fixture.js";

const proposal = {
	action: "create" as const,
	path: "notes/a.md",
	content: "secret-value",
	category: "ephemeral" as const,
	edit_class: "typo-fix" as const,
};

describe("mneme_propose queue security", () => {
	it("fails closed on lock contention without leaking record content", () => {
		const { vault } = makeTempVault("propose-lock-contention");
		const lock = join(vault.stateDir, "proposals", "pending.jsonl.lock");
		mkdirSync(dirname(lock), { recursive: true });
		writeFileSync(lock, "active", "utf-8");

		const result = proposeTool(proposal, vault);

		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.error.code).toBe("IO_ERROR");
		expect(result.error.message).toBe("Could not queue proposal safely.");
		expect(result.error.message).not.toContain(proposal.content);
	});

	it("recovers a stale lock left by a crashed writer", () => {
		const { vault } = makeTempVault("propose-stale-lock");
		const lock = join(vault.stateDir, "proposals", "pending.jsonl.lock");
		mkdirSync(dirname(lock), { recursive: true });
		writeFileSync(lock, "crashed", "utf-8");
		const stale = new Date(Date.now() - 30_000);
		utimesSync(lock, stale, stale);

		const result = proposeTool(proposal, vault);

		expect(result.ok).toBe(true);
	});

	it.runIf(process.platform !== "win32")(
		"refuses a symlinked queue without modifying its target",
		() => {
			const { vault, rootDir } = makeTempVault("propose-queue-symlink");
			const queue = join(vault.stateDir, "proposals", "pending.jsonl");
			const outside = join(dirname(rootDir), "mneme-propose-outside.jsonl");
			mkdirSync(dirname(queue), { recursive: true });
			writeFileSync(outside, "outside\n", "utf-8");
			symlinkSync(outside, queue);

			const result = proposeTool(proposal, vault);

			expect(result.ok).toBe(false);
			expect(readFileSync(outside, "utf-8")).toBe("outside\n");
		},
	);
});
