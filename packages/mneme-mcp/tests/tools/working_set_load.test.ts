/**
 * Smoke tests for working_set_load tool schema.
 *
 * Runtime integration tests (index lookup, glob fallback, top_k, truncated
 * flag, section parsing) live in checkpoint_list.test.ts alongside the
 * companion checkpointListTool tests, because the two tools share the same
 * checkpoint infrastructure and fixture helpers.
 */
import { describe, expect, it } from "vitest";
import {
	WorkingSetLoadInputSchema,
	workingSetLoadTool,
} from "../../src/tools/working_set_load.js";
import { makeTempVault } from "../helpers/vault_fixture.js";

describe("WorkingSetLoadInputSchema smoke", () => {
	it("requires anchor", () => {
		expect(() => WorkingSetLoadInputSchema.parse({})).toThrow();
	});

	it("accepts anchor only, top_k defaults to undefined", () => {
		const parsed = WorkingSetLoadInputSchema.parse({ anchor: "abc123" });
		expect(parsed.anchor).toBe("abc123");
		expect(parsed.top_k).toBeUndefined();
	});

	it("accepts anchor with top_k", () => {
		const parsed = WorkingSetLoadInputSchema.parse({
			anchor: "abc123",
			top_k: 10,
		});
		expect(parsed.top_k).toBe(10);
	});

	it("rejects top_k above 500", () => {
		expect(() =>
			WorkingSetLoadInputSchema.parse({ anchor: "x", top_k: 501 }),
		).toThrow();
	});
});

describe("workingSetLoadTool smoke", () => {
	it("returns ok:true with found:false for unknown anchor when no index exists", () => {
		const { vault } = makeTempVault("wsl-smoke-notfound");
		const res = workingSetLoadTool(
			WorkingSetLoadInputSchema.parse({ anchor: "unknown-anchor" }),
			vault,
		);
		expect(res.ok).toBe(true);
		if (res.ok) {
			const data = res.data as { found: boolean };
			expect(data.found).toBe(false);
		}
	});
});
