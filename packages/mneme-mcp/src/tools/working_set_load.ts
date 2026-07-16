/**
 * mneme_working_set_load — Load a checkpoint's working set for JIT context.
 *
 * Given an anchor string, this tool:
 *   1. Looks up the anchor in <vault>/.mneme/checkpoints/index.jsonl to get
 *      the checkpoint markdown path (relative to the vault root).
 *   2. Falls back to a glob of <vault>/checkpoints/*-<anchor>.md if the
 *      index is absent or has no matching entry.
 *   3. Parses the checkpoint markdown (YAML frontmatter + salience bullets).
 *   4. Returns the working-set items, optionally limited to the top_k by
 *      descending salience score.
 *
 * Unknown anchor → a clear "not_found" result, not a throw.
 * Missing file after lookup → a clear "not_found" result, not a throw.
 *
 * Checkpoint markdown format (written by mneme-core Phase 1-3):
 *
 *   ---
 *   type: checkpoint
 *   anchor: abc123
 *   session_id: s-2026-06-14
 *   created: 2026-06-14T10:00:00Z
 *   ---
 *
 *   ## Section Name
 *   - [salience 0.92] Item text here
 *   - [salience 0.75] Another item
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import { z } from "zod";
import { wrapUntrusted } from "../injection.js";
import { redact } from "../privacy.js";
import { assertWithinVault } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

export const WorkingSetLoadInputSchema = z.object({
	anchor: z
		.string()
		.min(1)
		.max(256)
		.describe("Checkpoint anchor returned by mneme_checkpoint_list."),
	top_k: z
		.number()
		.int()
		.positive()
		.max(500)
		.describe("Return only the highest-salience items up to this limit.")
		.optional(),
});

export type WorkingSetLoadInput = z.infer<typeof WorkingSetLoadInputSchema>;

export interface WorkingSetItem {
	section: string;
	salience: number;
	text: string;
}

export interface WorkingSetLoadOutput {
	anchor: string;
	path: string;
	frontmatter: Record<string, string>;
	items: WorkingSetItem[];
	total_items: number;
	truncated: boolean;
}

export interface WorkingSetNotFound {
	anchor: string;
	found: false;
	reason: string;
}

/** Absolute path to the checkpoint index within a vault. */
function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

/**
 * Resolve the absolute filesystem path to the checkpoint markdown file.
 * Returns null when the anchor cannot be located by any strategy.
 */
function resolveCheckpointPath(
	anchor: string,
	vault: VaultConfig,
): string | null {
	// Strategy 1: index lookup.
	const idxPath = indexPath(vault);
	if (existsSync(idxPath)) {
		try {
			const raw = readFileSync(idxPath, "utf8");
			for (const line of raw.split(/\r?\n/).reverse()) {
				const trimmed = line.trim();
				if (trimmed.length === 0) continue;
				try {
					const obj = JSON.parse(trimmed) as Record<string, unknown>;
					if (
						typeof obj.anchor === "string" &&
						typeof obj.path === "string" &&
						obj.anchor === anchor
					) {
						const resolved = resolvePath(join(vault.root, obj.path as string));
						try {
							assertWithinVault(vault.root, resolved);
						} catch {
							// Path traversal or containment error: treat as not found.
							continue;
						}
						if (existsSync(resolved)) return resolved;
					}
				} catch {
					// Skip malformed lines.
				}
			}
		} catch {
			// Fall through to strategy 2.
		}
	}

	// Strategy 2: glob <vault>/checkpoints/*-<anchor>.md
	const checkpointsDir = join(vault.root, "checkpoints");
	if (existsSync(checkpointsDir)) {
		try {
			const files = readdirSync(checkpointsDir);
			for (const f of files) {
				if (f.endsWith(`-${anchor}.md`)) {
					return join(checkpointsDir, f);
				}
			}
		} catch {
			// Fall through.
		}
	}

	return null;
}

/**
 * Parse the YAML-like frontmatter block between the first pair of `---` lines.
 * Returns key-value pairs as plain strings. Handles quoted and unquoted values.
 */
function parseFrontmatter(text: string): {
	frontmatter: Record<string, string>;
	bodyStart: number;
} {
	const lines = text.split(/\r?\n/);
	const result: Record<string, string> = {};

	if (lines[0]?.trim() !== "---") {
		return { frontmatter: result, bodyStart: 0 };
	}

	let closeIdx = -1;
	for (let i = 1; i < lines.length; i++) {
		if (lines[i]?.trim() === "---") {
			closeIdx = i;
			break;
		}
		const m = lines[i]?.match(/^([^:]+):\s*(.*)$/);
		if (m) {
			const key = m[1]?.trim() ?? "";
			let val = m[2]?.trim() ?? "";
			// Strip optional surrounding quotes.
			if (
				(val.startsWith('"') && val.endsWith('"')) ||
				(val.startsWith("'") && val.endsWith("'"))
			) {
				val = val.slice(1, -1);
			}
			if (key.length > 0) result[key] = val;
		}
	}

	return {
		frontmatter: result,
		bodyStart: closeIdx === -1 ? 0 : closeIdx + 1,
	};
}

/**
 * Parse salience bullets from the body lines.
 * Recognises lines of the form:  - [salience 0.92] Item text
 * Section heading is inferred from the most recent `## Heading` line.
 */
function parseItems(bodyLines: string[]): WorkingSetItem[] {
	const items: WorkingSetItem[] = [];
	let currentSection = "";

	for (const line of bodyLines) {
		const sectionMatch = line.match(/^##\s+(.+)$/);
		if (sectionMatch) {
			currentSection = sectionMatch[1]?.trim() ?? "";
			continue;
		}

		const bulletMatch = line.match(/^[-*]\s+\[salience\s+([\d.]+)\]\s+(.+)$/);
		if (bulletMatch) {
			const salience = Number.parseFloat(bulletMatch[1] ?? "0");
			const text = bulletMatch[2]?.trim() ?? "";
			items.push({ section: currentSection, salience, text });
		}
	}

	return items;
}

export function workingSetLoadTool(
	args: WorkingSetLoadInput,
	vault: VaultConfig,
): ToolResult<WorkingSetLoadOutput | WorkingSetNotFound> {
	const absPath = resolveCheckpointPath(args.anchor, vault);

	if (absPath === null) {
		return {
			ok: true,
			data: {
				anchor: args.anchor,
				found: false,
				reason: `No checkpoint found for anchor '${args.anchor}'. Check mneme_checkpoint_list for available anchors.`,
			},
		};
	}

	let raw: string;
	try {
		raw = readFileSync(absPath, "utf8");
	} catch (err) {
		return {
			ok: true,
			data: {
				anchor: args.anchor,
				found: false,
				reason: `Checkpoint file exists at ${absPath} but could not be read: ${err instanceof Error ? err.message : String(err)}`,
			},
		};
	}

	const { frontmatter, bodyStart } = parseFrontmatter(raw);
	const bodyLines = raw.split(/\r?\n/).slice(bodyStart);
	const allItems = parseItems(bodyLines);

	// Sort descending by salience.
	allItems.sort((a, b) => b.salience - a.salience);

	const totalItems = allItems.length;
	const limited =
		args.top_k !== undefined ? allItems.slice(0, args.top_k) : allItems;
	const truncated = limited.length < totalItems;

	// S3: sanitize each bullet — redact private blocks then fence the result
	// so the caller cannot mistake checkpoint content for trusted context.
	const sanitized = limited.map((item) => ({
		...item,
		text: wrapUntrusted(redact(item.text).text, "checkpoint-bullet"),
	}));

	// Express the path relative to the vault root for portability.
	const relPath = absPath.startsWith(vault.root)
		? absPath.slice(vault.root.length).replace(/^[\\/]/, "")
		: absPath;

	return {
		ok: true,
		data: {
			anchor: args.anchor,
			path: relPath,
			frontmatter,
			items: sanitized,
			total_items: totalItems,
			truncated,
		},
	};
}
