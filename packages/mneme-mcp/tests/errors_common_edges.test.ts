import { describe, expect, it } from "vitest";
import { z } from "zod";
import { ERROR_CODES, MnemeToolError, toMnemeError } from "../src/errors.js";
import { isoDateToUnix, isoDateToUnixEndOfDay } from "../src/tools/common.js";

describe("structured MCP error conversion", () => {
	it("preserves explicit Mneme tool errors", () => {
		expect(
			toMnemeError(
				new MnemeToolError(ERROR_CODES.QUERY_TOO_SHORT, "query was gated"),
			),
		).toEqual({
			code: ERROR_CODES.QUERY_TOO_SHORT,
			message: "query was gated",
		});
	});

	it("converts Zod validation issues without exposing an internal stack", () => {
		const parsed = z.object({ count: z.number().int().positive() }).safeParse({
			count: "many",
		});
		expect(parsed.success).toBe(false);
		if (parsed.success) return;

		const converted = toMnemeError(parsed.error);

		expect(converted.code).toBe(ERROR_CODES.INVALID_ARGUMENT);
		expect(converted.message).toContain("number");
		expect(converted.message).not.toContain("ZodError");
	});

	it("maps native and non-Error faults to IO_ERROR", () => {
		expect(toMnemeError(new Error("disk unavailable"))).toEqual({
			code: ERROR_CODES.IO_ERROR,
			message: "disk unavailable",
		});
		expect(toMnemeError("string fault")).toEqual({
			code: ERROR_CODES.IO_ERROR,
			message: "string fault",
		});
	});

	it("redacts private spans from every error boundary", () => {
		const secret = "ERROR_CANARY";
		for (const error of [
			new Error(`<private>${secret}</private>`),
			new MnemeToolError(ERROR_CODES.IO_ERROR, `<private>${secret}</private>`),
		]) {
			const converted = toMnemeError(error);
			expect(converted.message).toContain("[REDACTED]");
			expect(converted.message).not.toContain(secret);
		}
	});
});

describe("ISO date conversion", () => {
	it("rejects malformed and impossible calendar dates", () => {
		expect(() => isoDateToUnix("2026/07/18")).toThrow(
			"Invalid ISO date format",
		);
		expect(() => isoDateToUnix("2026-99-99")).toThrow("Unparseable date");
	});

	it("returns UTC midnight and an inclusive end of day", () => {
		const midnight = Math.floor(Date.parse("2026-07-18T00:00:00Z") / 1000);
		expect(isoDateToUnix("2026-07-18")).toBe(midnight);
		expect(isoDateToUnixEndOfDay("2026-07-18")).toBe(midnight + 86_399);
	});
});
