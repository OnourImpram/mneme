/**
 * mneme_checkpoint_list, scoped discovery for CCE checkpoints.
 *
 * The append-only JSONL index is untrusted derived state. Reads are bounded,
 * malformed or legacy records fail closed, and cross-scope discovery requires
 * an explicit wildcard.
 */

import { closeSync, existsSync, fstatSync, openSync, readSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize } from "../injection.js";
import { redact } from "../privacy.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

const MAX_INDEX_BYTES = 16 * 1024 * 1024;
const REQUEST_SCOPE_PATTERN = /^(?:\*|(?!.*\p{Cc})[^\s*](?:[^*]*[^\s*])?)$/u;
const CONCRETE_SCOPE_PATTERN = /^(?!.*\p{Cc})[^\s*](?:[^*]*[^\s*])?$/u;
const ANCHOR_PATTERN = /^[A-Za-z0-9._:-]+$/u;

const RequestScopeSchema = z
	.string()
	.min(1)
	.max(256)
	.regex(
		REQUEST_SCOPE_PATTERN,
		"Scope must be a trimmed concrete name or the exact wildcard '*'.",
	);

const ConcreteScopeSchema = z
	.string()
	.min(1)
	.max(256)
	.regex(CONCRETE_SCOPE_PATTERN);

export const CheckpointListInputSchema = z.object({
	limit: z
		.number()
		.int()
		.positive()
		.max(200)
		.default(20)
		.describe("Maximum number of checkpoint index entries to return."),
	scope: RequestScopeSchema.describe(
		"Scope to list. Omit for the configured default scope. Pass the exact literal '*' only for an explicit cross-scope read.",
	).optional(),
});

export type CheckpointListInput = z.infer<typeof CheckpointListInputSchema>;

const CheckpointIndexRecordSchema = z.object({
	anchor: z.string().min(1).max(256).regex(ANCHOR_PATTERN),
	id: z.string().max(256).default(""),
	created: z.string().max(128).default(""),
	session_id: z.string().max(256).default(""),
	prev_anchor: z.string().max(256).nullable().default(null),
	path: z.string().min(1).max(4096),
	item_count: z.number().int().nonnegative().max(1_000_000).default(0),
	top_salience: z.number().finite().default(0),
	scope: ConcreteScopeSchema,
});

type CheckpointIndexRecord = z.infer<typeof CheckpointIndexRecordSchema>;

export interface CheckpointEntry {
	anchor: string;
	id: string;
	created: string;
	session_id: string;
	prev_anchor: string | null;
	path: string;
	item_count: number;
	top_salience: number;
	scope: string;
}

export interface CheckpointListOutput {
	entries: CheckpointEntry[];
	/** Number of valid records visible in the requested scope before limit. */
	total_in_index: number;
	/** Invalid JSON, invalid shapes, and unscoped legacy records skipped. */
	malformed_lines: number;
}

function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

function safeDisplay(value: string): string {
	return neutralize(redact(value).text);
}

function parseLine(line: string): CheckpointIndexRecord | null {
	const trimmed = line.trim();
	if (trimmed.length === 0) return null;
	try {
		const parsed = CheckpointIndexRecordSchema.safeParse(JSON.parse(trimmed));
		return parsed.success ? parsed.data : null;
	} catch {
		return null;
	}
}

function boundedRead(path: string): string {
	const descriptor = openSync(path, "r");
	try {
		const stat = fstatSync(descriptor);
		if (!stat.isFile()) {
			throw new Error("checkpoint index is not a regular file");
		}
		if (stat.size > MAX_INDEX_BYTES) {
			throw new Error("checkpoint index exceeds the safety limit");
		}
		const bytes = Buffer.alloc(stat.size + 1);
		const bytesRead = readSync(descriptor, bytes, 0, bytes.length, 0);
		if (bytesRead > stat.size || bytesRead > MAX_INDEX_BYTES) {
			throw new Error("checkpoint index grew beyond the safety limit");
		}
		return bytes.subarray(0, bytesRead).toString("utf8");
	} finally {
		closeSync(descriptor);
	}
}

function resolveRequestedScope(
	requested: string | undefined,
	vault: VaultConfig,
): string | null {
	if (requested !== undefined) return requested;
	const parsed = ConcreteScopeSchema.safeParse(vault.defaultScope());
	return parsed.success ? parsed.data : null;
}

function scopeMatches(recordScope: string, requestedScope: string): boolean {
	return requestedScope === "*" || recordScope === requestedScope;
}

export function checkpointListTool(
	args: CheckpointListInput,
	vault: VaultConfig,
): ToolResult<CheckpointListOutput> {
	const requestedScope = resolveRequestedScope(args.scope, vault);
	if (requestedScope === null) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INVALID_ARGUMENT,
				message: "The configured default checkpoint scope is invalid.",
			},
		};
	}

	const path = indexPath(vault);
	if (!existsSync(path)) {
		return {
			ok: true,
			data: { entries: [], total_in_index: 0, malformed_lines: 0 },
		};
	}

	let raw: string;
	try {
		raw = boundedRead(path);
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "Could not read the checkpoint index safely.",
			},
		};
	}

	const visible: CheckpointEntry[] = [];
	let malformedLines = 0;
	for (const line of raw.split(/\r?\n/u)) {
		if (line.trim().length === 0) continue;
		const record = parseLine(line);
		if (record === null) {
			malformedLines += 1;
			continue;
		}
		if (!scopeMatches(record.scope, requestedScope)) continue;
		visible.push({
			anchor: safeDisplay(record.anchor),
			id: safeDisplay(record.id),
			created: safeDisplay(record.created),
			session_id: safeDisplay(record.session_id),
			prev_anchor:
				record.prev_anchor === null ? null : safeDisplay(record.prev_anchor),
			path: safeDisplay(record.path),
			item_count: record.item_count,
			top_salience: record.top_salience,
			scope: record.scope,
		});
	}

	return {
		ok: true,
		data: {
			entries: visible.slice().reverse().slice(0, args.limit),
			total_in_index: visible.length,
			malformed_lines: malformedLines,
		},
	};
}
