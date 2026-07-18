import { describe, expect, it } from "vitest";
import {
	ConcreteScopeSchema,
	classifyMarkdownScope,
	DocumentScopeError,
	legacyScopeMatches,
	ScopeSchema,
} from "../src/scope.js";

describe("scope contract", () => {
	it.each([
		"default",
		"clinical",
		"case 42",
		"proj:alpha",
		"*",
	])("accepts supported read selector %s", (value) =>
		expect(ScopeSchema.safeParse(value).success).toBe(true));

	it.each([
		"",
		" clinical",
		"clinical ",
		"case*all",
		"\0bad",
		"bad\nname",
		"bad\u200bname",
		"x".repeat(257),
	])("rejects ambiguous selector %s", (value) => {
		expect(ScopeSchema.safeParse(value).success).toBe(false);
	});

	it("rejects wildcard for durable records", () => {
		expect(ConcreteScopeSchema.safeParse("*").success).toBe(false);
	});

	it("maps legacy missing scope only to default", () => {
		expect(legacyScopeMatches(undefined, "default")).toBe(true);
		expect(legacyScopeMatches(undefined, "clinical")).toBe(false);
		expect(legacyScopeMatches(undefined, "*")).toBe(true);
	});

	it("classifies explicit and legacy project frontmatter", () => {
		expect(
			classifyMarkdownScope('---\nscope: "clinical"\n---\nbody').scope,
		).toBe("clinical");
		expect(classifyMarkdownScope("---\nproject: legacy\n---\nbody").scope).toBe(
			"legacy",
		);
	});

	it("fails closed on malformed, wildcard, duplicate, or conflicting metadata", () => {
		for (const text of [
			"---\nscope: clinical\nbody",
			'---\nscope: "*"\n---\nbody',
			"---\nscope: a\nscope: a\n---\nbody",
			"---\nscope: a\nproject: b\n---\nbody",
		]) {
			expect(() => classifyMarkdownScope(text)).toThrow(DocumentScopeError);
		}
	});
});
