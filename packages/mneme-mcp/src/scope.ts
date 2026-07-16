import { z } from "zod";

const SCOPE_PATTERN =
	/^(?!.*[\p{Cc}\p{Cf}\p{Zl}\p{Zp}])(?:\*|[^\s*\u0000-\u001f\u007f](?:[^*\u0000-\u001f\u007f]*[^\s*\u0000-\u001f\u007f])?)$/u;
const CONCRETE_SCOPE_PATTERN =
	/^(?!.*[\p{Cc}\p{Cf}\p{Zl}\p{Zp}])[^\s*\u0000-\u001f\u007f](?:[^*\u0000-\u001f\u007f]*[^\s*\u0000-\u001f\u007f])?$/u;

/**
 * Public scope identifier contract shared by every read-capable MCP tool.
 *
 * Omission means the configured default scope. The exact literal "*" is the
 * only cross-scope selector. Whitespace ambiguity, controls, format
 * characters, separators, and embedded asterisks are rejected.
 */
export const ScopeSchema = z
	.string()
	.min(1)
	.max(256)
	.regex(
		SCOPE_PATTERN,
		"scope must be a non-empty identifier, or exactly '*' for cross-scope access",
	)
	.meta({
		description:
			"Isolation scope. Omit to use MNEME_SCOPE or the configured default. " +
			"The exact literal '*' explicitly opts into a cross-scope read.",
	});

/** Persisted records and durable writes must always use a concrete scope. */
export const ConcreteScopeSchema = z
	.string()
	.min(1)
	.max(256)
	.regex(
		CONCRETE_SCOPE_PATTERN,
		"durable scope must be a concrete non-empty identifier and cannot be '*'",
	)
	.meta({
		description:
			"Concrete isolation scope for a durable record. The cross-scope '*' selector is not valid for writes.",
	});

/** Return a safe concrete default, never the cross-scope wildcard. */
export function concreteScopeOrNull(value: unknown): string | null {
	const parsed = ConcreteScopeSchema.safeParse(value);
	return parsed.success ? parsed.data : null;
}

export function effectiveScope(
	requested: string | undefined,
	defaultScope: () => string,
): string {
	return requested ?? defaultScope();
}

/** Legacy records without a scope belong only to the default scope. */
export function legacyScopeMatches(
	recordScope: unknown,
	requestedScope: string,
): boolean {
	const normalized =
		recordScope === undefined || recordScope === null || recordScope === ""
			? "default"
			: recordScope;
	const persisted = ConcreteScopeSchema.safeParse(normalized);
	if (!persisted.success) return false;
	return requestedScope === "*" || persisted.data === requestedScope;
}
