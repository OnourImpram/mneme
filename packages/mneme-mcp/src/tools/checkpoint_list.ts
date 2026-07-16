/**
 * mneme_checkpoint_list, scoped discovery for Context Continuity Engine checkpoints.
 *
 * The append-only JSONL index is treated as untrusted derived state. Reads are
 * bounded, malformed records are counted, and legacy records without a scope
 * are visible only from the concrete `default` scope.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize } from "../injection.js";
import { redact } from "../privacy.js";
import {
	ConcreteScopeSchema,
	effectiveScope,
	legacyScopeMatches,
	ScopeSchema,
} from "../scope.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

const MAX_INDEX_BYTES = 16 * 1024 * 1024;

export const CheckpointListInputSchema = z.object({
	limit: z.number().int().positive().max(200).default(20),
	scope: ScopeSchema.optional(),
});

export type CheckpointListInput = z.infer<typeof CheckpointListInputSchema>;

const CheckpointIndexRecordSchema = z.object({
	anchor: z.string().min(1).max(128).regex(/^[A-Za-z0-9._:-]+$/u),
	id: z.string().max(256).default(""),
	created: z.string().max(128).default(""),
	session_id: z.string().max(256).default(""),
	prev_anchor: z.string().max(128).nullable().default(null),
	path: z.string().min(1).max(4096),
	item_count: z.number().int().nonnegative().max(1_000_000).default(0),
	top_salience: z.number().finite().default(0),
	scope: ConcreteScopeSchema.optional(),
});

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
	/** Number of valid records visible in the requested scope before `limit`. */
	total_in_index: number;
	/** Invalid JSON or invalid record shapes skipped during this bounded read. */
	malformed_lines: number;
}

function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

function safeDisplay(value: string): string {
	return neutralize(redact(value).text);
}

function parseLine(line: string): z.infer<typeof CheckpointIndexRecordSchema> | null {
	const trimmed = line.trim();
	if (trimmed.length === 0) return null;
	try {
		const parsed = CheckpointIndexRecordSchema.safeParse(JSON.parse(trimmed));
		return parsed.success ? parsed.data : null;
	} catch {
		return null;
	}
}

export function checkpointListTool(
	args: CheckpointListInput,
	vault: VaultConfig,
): ToolResult<CheckpointListOutput> {
	const requestedScope = effectiveScope(args.scope, () => vault.defaultScope());
	const path = indexPath(vault);

	if (!existsSync(path)) {
		return {
			ok: true,
			data: { entries: [], total_in_index: 0, malformed_lines: 0 },
		};
	}

	let raw: string;
	try {
		const stat = statSync(path);
		if (!stat.isFile()) {
			throw new Error("checkpoint index is not a regular file");
		}
		if (stat.size > MAX_INDEX_BYTES) {
			throw new Error(
				`checkpoint index exceeds the ${MAX_INDEX_BYTES}-byte safety limit`,
			);
		}
		raw = readFileSync(path, "utf8");
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
		if (!legacyScopeMatches(record.scope, requestedScope)) continue;
		visible.push({
			anchor: record.anchor,
			id: safeDisplay(record.id),
			created: safeDisplay(record.created),
			session_id: safeDisplay(record.session_id),
			prev_anchor: record.prev_anchor,
			path: safeDisplay(record.path),
			item_count: record.item_count,
			top_salience: record.top_salience,
			scope: record.scope ?? "default",
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
