/**
 * S5: atomicWriteText cleanup failure surfaces as an error.
 *
 * This file uses vi.mock("node:fs") so vitest intercepts all imports of
 * "node:fs" within this process — including the import inside atomic_write.ts.
 * Kept in a separate file from other tests so the module-level mock does not
 * contaminate unrelated test files (pool: "forks" isolates per file).
 */

import * as fsModule from "node:fs";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

// Hoist mock before any other imports so vitest intercepts "node:fs" in
// atomic_write.ts as well. The factory spreads the real module so all
// functions work normally; individual tests spy on rmSync to simulate failure.
vi.mock("node:fs", async (importOriginal) => {
	const actual = await importOriginal<typeof import("node:fs")>();
	return { ...actual };
});

import { atomicWriteText } from "../../src/vault/atomic_write.js";

describe("S5: atomicWriteText cleanup failure surfaces as error", () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("persistent EPERM on rmSync after successful write surfaces as an error", () => {
		// Spy on rmSync (intercepted via vi.mock above) to always throw EPERM.
		// rmSyncWithRetry retries EPERM up to 3 times before throwing.
		vi.spyOn(fsModule, "rmSync").mockImplementation(() => {
			const err = new Error("EPERM: simulated lock") as NodeJS.ErrnoException;
			err.code = "EPERM";
			throw err;
		});

		const root = mkdtempSync(join(tmpdir(), "mneme-s5-"));
		const target = join(root, "out.md");
		// Create an existing target so there is real content to overwrite.
		writeFileSync(target, "initial content\n", "utf8");

		// With writeOk=true after the rename, a cleanup failure must propagate.
		expect(() => atomicWriteText(target, "new content\n")).toThrow(/EPERM/);
	});

	it("successful write (no mock) still completes without error", () => {
		const root = mkdtempSync(join(tmpdir(), "mneme-s5-ok-"));
		const target = join(root, "out.md");
		expect(() => atomicWriteText(target, "hello\n")).not.toThrow();
	});
});
