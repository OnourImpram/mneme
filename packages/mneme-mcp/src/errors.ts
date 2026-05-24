/**
 * Structured error codes for mneme MCP tools.
 *
 * Tools return `{ ok: false, error: { code, message } }` on failure so
 * clients can branch on `code` without parsing message text. See
 * `docs/MCP.md#error-handling`.
 */

export const ERROR_CODES = {
	/** A required argument was missing or wrong type. */
	INVALID_ARGUMENT: "INVALID_ARGUMENT",
	/** The FTS5 database file is missing. Run `mneme index` first. */
	INDEX_NOT_FOUND: "INDEX_NOT_FOUND",
	/** The target path resolved outside vault root. */
	PATH_OUTSIDE_VAULT: "PATH_OUTSIDE_VAULT",
	/** The requested feature requires the standard or full install profile. */
	FEATURE_UNAVAILABLE: "FEATURE_UNAVAILABLE",
	/** The query was below the gating threshold and was dropped. */
	QUERY_TOO_SHORT: "QUERY_TOO_SHORT",
	/** An unexpected I/O failure during read or write. */
	IO_ERROR: "IO_ERROR",
} as const;

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];

export interface MnemeError {
	code: ErrorCode;
	message: string;
}

export class MnemeToolError extends Error {
	readonly code: ErrorCode;
	constructor(code: ErrorCode, message: string) {
		super(message);
		this.name = "MnemeToolError";
		this.code = code;
	}
}

export function toMnemeError(err: unknown): MnemeError {
	if (err instanceof MnemeToolError) {
		return { code: err.code, message: err.message };
	}
	const message = err instanceof Error ? err.message : String(err);
	return { code: ERROR_CODES.IO_ERROR, message };
}
