/**
 * mneme_checkpoint_list — List recent Context Continuity Engine checkpoints.
 *
 * Reads the append-only index at <vault>/.mneme/checkpoints/index.jsonl
 * and returns the most recent `limit` entries in reverse-chronological
 * order (newest first). Missing index file → empty list, not an error.
 *
 * Each line in the index is a JSON object:
 *   { anchor, id, created, session_id, prev_anchor, path, item_count, top_salience }
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

export const CheckpointListInputSchema = z.object({
	limit: z.number().int().positive().max(200).default(20),
});

export type CheckpointListInput = z.infer<typeof CheckpointListInputSchema>;

export interface CheckpointEntry {
	anchor: string;
	id: string;
	created: string;
	session_id: string;
	prev_anchor: string | null;
	path: string;
	item_count: number;
	top_salience: number;
}

export interface CheckpointListOutput {
	entries: CheckpointEntry[];
	total_in_index: number;
}

/** Absolute path to the checkpoint index within a vault. */
function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

/** Parse one JSONL line into a CheckpointEntry, returning null on bad input. */
function parseLine(line: string): CheckpointEntry | null {
	const trimmed = line.trim();
	if (trimmed.length === 0) return null;
	try {
		const obj = JSON.parse(trimmed) as Record<string, unknown>;
		// Require at minimum anchor and path; other fields get safe defaults.
		if (typeof obj.anchor !== "string" || typeof obj.path !== "string") {
			return null;
		}
		return {
			anchor: obj.anchor as string,
			id: typeof obj.id === "string" ? obj.id : "",
			created: typeof obj.created === "string" ? obj.created : "",
			session_id: typeof obj.session_id === "string" ? obj.session_id : "",
			prev_anchor: typeof obj.prev_anchor === "string" ? obj.prev_anchor : null,
			path: obj.path as string,
			item_count: typeof obj.item_count === "number" ? obj.item_count : 0,
			top_salience: typeof obj.top_salience === "number" ? obj.top_salience : 0,
		};
	} catch {
		return null;
	}
}

export function checkpointListTool(
	args: CheckpointListInput,
	vault: VaultConfig,
): ToolResult<CheckpointListOutput> {
	const idxPath = indexPath(vault);

	if (!existsSync(idxPath)) {
		return { ok: true, data: { entries: [], total_in_index: 0 } };
	}

	let raw: string;
	try {
		raw = readFileSync(idxPath, "utf8");
	} catch {
		// Unreadable index → treat as empty rather than error.
		return { ok: true, data: { entries: [], total_in_index: 0 } };
	}

	const lines = raw.split(/\r?\n/);
	const all: CheckpointEntry[] = [];
	for (const line of lines) {
		const entry = parseLine(line);
		if (entry !== null) {
			all.push(entry);
		}
	}

	// newest-first: the index is append-only so last lines are newest.
	const reversed = all.slice().reverse();
	const limited = reversed.slice(0, args.limit);

	return {
		ok: true,
		data: { entries: limited, total_in_index: all.length },
	};
}
