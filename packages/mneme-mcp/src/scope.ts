import { z } from "zod";

export const DEFAULT_SCOPE = "default";
export const MAX_SCOPE_LENGTH = 256;
const MAX_FRONTMATTER_BYTES = 64 * 1024;
const MAX_FRONTMATTER_LINES = 512;

const SCOPE_PATTERN =
	/^(?!.*[\p{Cc}\p{Cf}\p{Zl}\p{Zp}])(?:\*|[^\s*](?:[^*]*[^\s*])?)$/u;
const CONCRETE_SCOPE_PATTERN =
	/^(?!.*[\p{Cc}\p{Cf}\p{Zl}\p{Zp}])[^\s*](?:[^*]*[^\s*])?$/u;

/** Read selector. The exact literal `*` is the only cross-scope value. */
export const ScopeSchema = z
	.string()
	.min(1)
	.max(MAX_SCOPE_LENGTH)
	.regex(
		SCOPE_PATTERN,
		"scope must be a non-empty identifier, or exactly '*' for cross-scope access",
	);

/** Persisted records and writes require a concrete scope. */
export const ConcreteScopeSchema = z
	.string()
	.min(1)
	.max(MAX_SCOPE_LENGTH)
	.regex(
		CONCRETE_SCOPE_PATTERN,
		"durable scope must be a concrete non-empty identifier and cannot be '*'",
	);

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

export function legacyScopeMatches(
	recordScope: unknown,
	requestedScope: string,
): boolean {
	const normalized =
		recordScope === undefined || recordScope === null || recordScope === ""
			? DEFAULT_SCOPE
			: recordScope;
	const persisted = ConcreteScopeSchema.safeParse(normalized);
	return (
		persisted.success &&
		(requestedScope === "*" || persisted.data === requestedScope)
	);
}

export class DocumentScopeError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "DocumentScopeError";
	}
}

export interface MarkdownScope {
	scope: string;
	hasFrontmatter: boolean;
	hasExplicitScope: boolean;
}

function parseScopeScalar(raw: string): string {
	if (raw.length === 0) throw new DocumentScopeError("scope value is empty");
	if (raw.startsWith('"')) {
		try {
			const parsed = JSON.parse(raw) as unknown;
			if (typeof parsed === "string") return parsed;
		} catch {
			// Report one neutral malformed-scalar error below.
		}
		throw new DocumentScopeError("quoted scope value is malformed");
	}
	if (raw.startsWith("'")) {
		if (!raw.endsWith("'") || raw.length < 2) {
			throw new DocumentScopeError("quoted scope value is malformed");
		}
		return raw.slice(1, -1).replaceAll("''", "'");
	}
	return raw;
}

/**
 * Parse only the bounded top-level `scope` and legacy `project` scalars.
 * Complex YAML for these security-sensitive fields is rejected fail closed.
 */
export function classifyMarkdownScope(text: string): MarkdownScope {
	const lines = text.split(/(?<=\n)/);
	if (lines.length === 0 || lines[0]?.trim() !== "---") {
		return {
			scope: DEFAULT_SCOPE,
			hasFrontmatter: false,
			hasExplicitScope: false,
		};
	}
	let consumedBytes = Buffer.byteLength(lines[0] ?? "", "utf8");
	let closed = false;
	let scopeValue: string | undefined;
	let projectValue: string | undefined;
	for (let index = 1; index <= MAX_FRONTMATTER_LINES; index += 1) {
		const line = lines[index];
		if (line === undefined) break;
		consumedBytes += Buffer.byteLength(line, "utf8");
		if (consumedBytes > MAX_FRONTMATTER_BYTES) {
			throw new DocumentScopeError(
				"frontmatter exceeds the safe parsing bound",
			);
		}
		if (line.trim() === "---") {
			closed = true;
			break;
		}
		const match = /^(scope|project)\s*:\s*(.*?)\s*(?:\r?\n)?$/.exec(line);
		if (match === null) continue;
		const key = match[1];
		const value = parseScopeScalar(match[2] ?? "");
		if (key === "scope") {
			if (scopeValue !== undefined) {
				throw new DocumentScopeError(
					"frontmatter contains duplicate scope keys",
				);
			}
			scopeValue = value;
		} else {
			if (projectValue !== undefined) {
				throw new DocumentScopeError(
					"frontmatter contains duplicate project keys",
				);
			}
			projectValue = value;
		}
	}
	if (!closed) {
		throw new DocumentScopeError(
			"frontmatter is not closed within the safe parsing bound",
		);
	}
	if (
		scopeValue !== undefined &&
		projectValue !== undefined &&
		scopeValue !== projectValue
	) {
		throw new DocumentScopeError("scope and legacy project metadata conflict");
	}
	const candidate = scopeValue ?? projectValue ?? DEFAULT_SCOPE;
	const scope = concreteScopeOrNull(candidate);
	if (scope === null) {
		throw new DocumentScopeError("frontmatter scope is invalid");
	}
	return {
		scope,
		hasFrontmatter: true,
		hasExplicitScope: scopeValue !== undefined || projectValue !== undefined,
	};
}
