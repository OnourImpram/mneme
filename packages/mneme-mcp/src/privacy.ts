/**
 * Canonical ``<private>`` redaction for the mneme-mcp package.
 *
 * Semantics are identical to ``mneme_core.privacy`` (Python). Both
 * implementations are validated against the shared conformance fixture
 * at ``tests/fixtures/redaction_cases.json``.
 *
 * Rules:
 *
 * - **Case-insensitive** — ``<PRIVATE>``, ``<Private>``, ``<private>``.
 * - **Attribute-tolerant** — ``<private reason="x">`` is matched.
 * - **Inner-whitespace-tolerant** — ``< private >`` is matched.
 * - **Fail-closed** — an unbalanced opening tag with no closing tag
 *   causes everything from the opener to end-of-string to be replaced,
 *   so a truncated secret never leaks its visible head.
 * - **Single replacement token** — every match becomes ``[REDACTED]``.
 */

/** Replacement token used for every redacted span. */
export const REDACTED_TOKEN = "[REDACTED]";

/**
 * Closed-pair pattern: ``<private ...>...</private>`` (case-insensitive,
 * attribute-tolerant, DOTALL via ``[\s\S]``).
 */
const PRIVATE_CLOSED_RE =
	/<\s*private(?:\s[^>]*)?\s*>[\s\S]*?<\/\s*private\s*>/gi;

/**
 * Fail-closed sentinel: an unbalanced opening tag followed by the rest
 * of the string. Applied after the closed-pair pass so it only fires
 * when an opener has no matching closer.
 */
const PRIVATE_OPEN_RE = /<\s*private(?:\s[^>]*)?\s*>[\s\S]*/gi;

/**
 * Replace every ``<private>...</private>`` block with ``[REDACTED]``.
 *
 * An unbalanced opening tag (no closing ``</private>``) causes
 * everything from the opener to the end of the string to be redacted
 * (fail-closed).
 *
 * Returns ``{ text, count }`` where ``count`` is the total number of
 * substitutions made (closed pairs + any fail-closed tail).
 *
 * ``null`` / ``undefined`` inputs are treated as empty strings with
 * zero substitutions (same as the previous ``migrate.ts`` behaviour).
 */
export function redact(input: string | null | undefined): {
	text: string;
	count: number;
} {
	if (typeof input !== "string" || input.length === 0) {
		return { text: input ?? "", count: 0 };
	}
	let count = 0;

	// Pass 1: replace all closed pairs.
	const afterClosed = input.replace(PRIVATE_CLOSED_RE, () => {
		count += 1;
		return REDACTED_TOKEN;
	});

	// Pass 2: fail-closed — replace any remaining opener + tail.
	const afterOpen = afterClosed.replace(PRIVATE_OPEN_RE, () => {
		count += 1;
		return REDACTED_TOKEN;
	});

	return { text: afterOpen, count };
}

/** Recursively redact string values and user-controlled object keys. */
export function redactValue(input: unknown): unknown {
	if (typeof input === "string") return redact(input).text;
	if (Array.isArray(input)) return input.map((item) => redactValue(item));
	if (input !== null && typeof input === "object") {
		const output: Record<string, unknown> = {};
		for (const [key, value] of Object.entries(input)) {
			output[redact(key).text] = redactValue(value);
		}
		return output;
	}
	return input;
}
