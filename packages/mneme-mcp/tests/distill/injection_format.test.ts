/**
 * Tests for ``src/distill/injection_format.ts``.
 *
 * Table-driven vitest covering all 4 ``selectFormat`` combinations
 * (hasBeenInjected × highPressure) and all 3 ``renderInjection``
 * branches (full, keypoints with points, keypoints without points, ref).
 *
 * Fully self-contained — no imports from prime.ts or tracker.
 */

import { describe, expect, it } from "vitest";
import {
	HIGH_PRESSURE_THRESHOLD,
	type InjectionDoc,
	type InjectionFormat,
	renderInjection,
	selectFormat,
} from "../../src/distill/injection_format.js";

// ---------------------------------------------------------------------------
// selectFormat — 4 decision-table combinations
// ---------------------------------------------------------------------------

interface SelectFormatCase {
	label: string;
	hasBeenInjected: boolean;
	contextPressure: number;
	expected: InjectionFormat;
}

const SELECT_CASES: SelectFormatCase[] = [
	{
		label: "first injection, low pressure → full",
		hasBeenInjected: false,
		contextPressure: 0.0,
		expected: "full",
	},
	{
		label: "first injection, high pressure → keypoints",
		hasBeenInjected: false,
		contextPressure: HIGH_PRESSURE_THRESHOLD,
		expected: "keypoints",
	},
	{
		label: "re-injection, low pressure → keypoints",
		hasBeenInjected: true,
		contextPressure: 0.5,
		expected: "keypoints",
	},
	{
		label: "re-injection, high pressure → ref",
		hasBeenInjected: true,
		contextPressure: 1.0,
		expected: "ref",
	},
];

describe("selectFormat — decision table", () => {
	for (const c of SELECT_CASES) {
		it(c.label, () => {
			expect(selectFormat(c.hasBeenInjected, c.contextPressure)).toBe(
				c.expected,
			);
		});
	}

	it("pressure exactly at threshold is high pressure (boundary)", () => {
		// 0.75 is >= threshold, so first injection → keypoints not full
		expect(selectFormat(false, 0.75)).toBe("keypoints");
	});

	it("pressure just below threshold is low pressure (boundary)", () => {
		expect(selectFormat(false, 0.74)).toBe("full");
	});

	it("pressure is clamped — value > 1.0 treated as 1.0 (high pressure)", () => {
		expect(selectFormat(false, 2.0)).toBe("keypoints");
	});

	it("pressure is clamped — negative value treated as 0.0 (low pressure)", () => {
		expect(selectFormat(false, -0.5)).toBe("full");
	});
});

// ---------------------------------------------------------------------------
// HIGH_PRESSURE_THRESHOLD constant
// ---------------------------------------------------------------------------

describe("HIGH_PRESSURE_THRESHOLD", () => {
	it("is 0.75", () => {
		expect(HIGH_PRESSURE_THRESHOLD).toBe(0.75);
	});
});

// ---------------------------------------------------------------------------
// renderInjection — all 3 format branches + empty body / empty keypoints
// ---------------------------------------------------------------------------

const BASE_DOC: InjectionDoc = {
	path: "notes/2026-05-24.md",
	title: "My Session Note",
	body: "This is the full body.",
	keyPoints: ["Point A", "Point B"],
};

describe("renderInjection — full", () => {
	it("renders H2 + body", () => {
		const result = renderInjection(BASE_DOC, "full");
		expect(result).toBe("## My Session Note\n\nThis is the full body.\n");
	});

	it("trims body whitespace", () => {
		const doc = { ...BASE_DOC, body: "  trimmed  " };
		const result = renderInjection(doc, "full");
		expect(result).toBe("## My Session Note\n\ntrimmed\n");
	});

	it("empty body renders H2 only (no blank paragraph)", () => {
		const doc = { ...BASE_DOC, body: "" };
		const result = renderInjection(doc, "full");
		expect(result).toBe("## My Session Note\n");
	});

	it("whitespace-only body is treated as empty", () => {
		const doc = { ...BASE_DOC, body: "   \n  " };
		const result = renderInjection(doc, "full");
		expect(result).toBe("## My Session Note\n");
	});
});

describe("renderInjection — keypoints with points", () => {
	it("renders H2 + bullets + see-path line", () => {
		const result = renderInjection(BASE_DOC, "keypoints");
		expect(result).toBe(
			"## My Session Note\n\n- Point A\n- Point B\n\nSee `notes/2026-05-24.md`.\n",
		);
	});

	it("each key point gets a dash prefix", () => {
		const doc = { ...BASE_DOC, keyPoints: ["Only one"] };
		const result = renderInjection(doc, "keypoints");
		expect(result).toContain("- Only one");
	});
});

describe("renderInjection — keypoints without points (empty keyPoints fallback)", () => {
	it("renders H2 + see-path only when keyPoints is empty", () => {
		const doc = { ...BASE_DOC, keyPoints: [] };
		const result = renderInjection(doc, "keypoints");
		expect(result).toBe("## My Session Note\n\nSee `notes/2026-05-24.md`.\n");
	});
});

describe("renderInjection — ref", () => {
	it("renders single see vault:// line", () => {
		const result = renderInjection(BASE_DOC, "ref");
		expect(result).toBe("see vault://notes/2026-05-24.md\n");
	});
});

describe("renderInjection — title fallback", () => {
	it("uses path as title when title is empty string", () => {
		const doc = { ...BASE_DOC, title: "" };
		const result = renderInjection(doc, "full");
		expect(result).toContain("## notes/2026-05-24.md");
	});
});
