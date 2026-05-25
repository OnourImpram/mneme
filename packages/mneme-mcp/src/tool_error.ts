/**
 * Runtime type guard for tool error responses.
 * Returns true only when `value` is a non-null object that carries
 * `ok === false`, which is the contract every mneme tool uses to
 * signal a recoverable error back to the MCP client.
 *
 * Extracted into its own module so tests can import it without
 * triggering the top-level `main()` call in src/index.ts.
 */
export function isToolError(
	value: unknown,
): value is { ok: false; error?: unknown } {
	return (
		typeof value === "object" &&
		value !== null &&
		"ok" in value &&
		(value as { ok: unknown }).ok === false
	);
}
