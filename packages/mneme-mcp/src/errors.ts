/**
 * Structured error codes for mneme MCP tools.
 *
 * Tools return `{ ok: false, error: { code, message } }` on failure so
 * clients can branch on `code` without parsing message text. See
 * `docs/MCP.md#error-handling`.
 */

import { ZodError } from "zod";

export const ERROR_CODES = {
	/** A required argument was missing or wrong type. */
	INVALID_ARGUMENT: "INVALID_ARGUMENT",
	/** The client requested a tool name this server does not expose. */
	UNKNOWN_TOOL: "UNKNOWN_TOOL",
	/** The FTS5 database file is missing. Run `mneme-core index rebuild` first. */
	INDEX_NOT_FOUND: "INDEX_NOT_FOUND",
	/** The target path resolved outside vault root. */
	PATH_OUTSIDE_VAULT: "PATH_OUTSIDE_VAULT",
	/** The target record is not visible in the requested isolation scope. */
	SCOPE_MISMATCH: "SCOPE_MISMATCH",
	/** The requested feature requires the standard or full install profile. */
	FEATURE_UNAVAILABLE: "FEATURE_UNAVAILABLE",
	/** The query was below the gating threshold and was dropped. */
	QUERY_TOO_SHORT: "QUERY_TOO_SHORT",
	/** An unexpected I/O failure during read or write. */
	IO_ERROR: "IO_ERROR",
	/** The FTS5 index was built with a different normalizer profile than the
	 *  query-time normalizer. Re-run `mneme index rebuild` with the correct
	 *  --locale flag to realign the index. */
	INDEX_STALE_OR_LOCALE_MISMATCH: "INDEX_STALE_OR_LOCALE_MISMATCH",
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
	if (err instanceof ZodError) {
		return {
			code: ERROR_CODES.INVALID_ARGUMENT,
			message: err.issues.map((issue) => issue.message).join("; "),
		};
	}
	const message = err instanceof Error ? err.message : String(err);
	return { code: ERROR_CODES.IO_ERROR, message };
}
