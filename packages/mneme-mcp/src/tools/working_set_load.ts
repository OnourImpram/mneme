/**
 * mneme_working_set_load, scoped checkpoint rehydration for the CCE.
 *
 * Checkpoint index and markdown content are untrusted inputs. Index and file
 * reads are bounded, paths are vault-contained, frontmatter scope is verified,
 * and returned memory text is redacted and fenced before it reaches a client.
 */

import {
	existsSync,
	readdirSync,
	readFileSync,
	statSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve as resolvePath } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize, wrapUntrusted } from "../injection.js";
import { redact } from "../privacy.js";
import {
	ConcreteScopeSchema,
	effectiveScope,
	legacyScopeMatches,
	ScopeSchema,
} from "../scope.js";
import { assertWithinVault } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

const MAX_INDEX_BYTES = 16 * 1024 * 1024;
const MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024;
const MAX_DIRECTORY_ENTRIES = 10_000;
const MAX_PARSED_ITEMS = 5_000;

export const WorkingSetLoadInputSchema = z.object({
	anchor: z
		.string()
		.min(1)
		.max(128)
		.regex(/^[A-Za-z0-9._:-]+$/u),
	top_k: z.number().int().positive().max(500).optional(),
	scope: ScopeSchema.optional(),
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
	scope: string;
}

export interface WorkingSetNotFound {
	anchor: string;
	found: false;
	reason: string;
}

const IndexRecordSchema = z.object({
	anchor: z.string().min(1).max(128),
	path: z.string().min(1).max(4096),
	scope: ConcreteScopeSchema.optional(),
});

function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

function boundedRead(path: string, limit: number, label: string): string {
	const stat = statSync(path);
	if (!stat.isFile()) throw new Error(`${label} is not a regular file`);
	if (stat.size > limit) {
		throw new Error(`${label} exceeds the ${limit}-byte safety limit`);
	}
	return readFileSync(path, "utf8");
}

function containedPath(rawPath: string, vault: VaultConfig): string | null {
	const candidate = isAbsolute(rawPath)
		? resolvePath(rawPath)
		: resolvePath(vault.root, rawPath);
	try {
		assertWithinVault(vault.root, candidate);
		return candidate;
	} catch {
		return null;
	}
}

function parseFrontmatter(text: string): {
	frontmatter: Record<string, string>;
	bodyStart: number;
	valid: boolean;
} {
	const lines = text.split(/\r?\n/u);
	const result: Record<string, string> = {};
	if (lines[0]?.trim() !== "---") {
		return { frontmatter: result, bodyStart: 0, valid: false };
	}

	let closeIdx = -1;
	for (let i = 1; i < Math.min(lines.length, 512); i += 1) {
		const line = lines[i] ?? "";
		if (line.trim() === "---") {
			closeIdx = i;
			break;
		}
		const match = line.match(/^([^:]{1,128}):\s*(.*)$/u);
		if (!match) continue;
		const key = match[1]?.trim() ?? "";
		let value = match[2]?.trim() ?? "";
		if (value.startsWith('"') && value.endsWith('"')) {
			try {
				const decoded = JSON.parse(value);
				if (typeof decoded === "string") value = decoded;
			} catch {
				continue;
			}
		} else if (value.startsWith("'") && value.endsWith("'")) {
			value = value.slice(1, -1).replace(/''/gu, "'");
		}
		if (key.length > 0 && value.length <= 4096) result[key] = value;
	}
	if (closeIdx === -1) {
		return { frontmatter: {}, bodyStart: 0, valid: false };
	}
	return { frontmatter: result, bodyStart: closeIdx + 1, valid: true };
}

function checkpointMatches(
	text: string,
	anchor: string,
	requestedScope: string,
): boolean {
	const { frontmatter, valid } = parseFrontmatter(text);
	if (!valid || frontmatter.anchor !== anchor) return false;
	return legacyScopeMatches(frontmatter.scope, requestedScope);
}

function resolveCheckpointPath(
	anchor: string,
	requestedScope: string,
	vault: VaultConfig,
): string | null {
	const idxPath = indexPath(vault);
	if (existsSync(idxPath)) {
		const raw = boundedRead(idxPath, MAX_INDEX_BYTES, "checkpoint index");
		for (const line of raw.split(/\r?\n/u).reverse()) {
			const trimmed = line.trim();
			if (trimmed.length === 0) continue;
			try {
				const parsed = IndexRecordSchema.safeParse(JSON.parse(trimmed));
				if (!parsed.success || parsed.data.anchor !== anchor) continue;
				if (!legacyScopeMatches(parsed.data.scope, requestedScope)) continue;
				const candidate = containedPath(parsed.data.path, vault);
				if (candidate === null || !existsSync(candidate)) continue;
				const text = boundedRead(
					candidate,
					MAX_CHECKPOINT_BYTES,
					"checkpoint file",
				);
				if (checkpointMatches(text, anchor, requestedScope)) return candidate;
			} catch (err) {
				if (err instanceof SyntaxError) continue;
				throw err;
			}
		}
	}

	const checkpointsDir = join(vault.root, "checkpoints");
	if (!existsSync(checkpointsDir)) return null;
	const files = readdirSync(checkpointsDir);
	if (files.length > MAX_DIRECTORY_ENTRIES) {
		throw new Error(
			`checkpoint directory exceeds the ${MAX_DIRECTORY_ENTRIES}-entry safety limit`,
		);
	}
	for (const filename of files) {
		if (!filename.endsWith(`-${anchor}.md`)) continue;
		const candidate = containedPath(join(checkpointsDir, filename), vault);
		if (candidate === null) continue;
		const text = boundedRead(
			candidate,
			MAX_CHECKPOINT_BYTES,
			"checkpoint file",
		);
		if (checkpointMatches(text, anchor, requestedScope)) return candidate;
	}
	return null;
}

function parseItems(bodyLines: string[]): WorkingSetItem[] {
	const items: WorkingSetItem[] = [];
	let currentSection = "";
	for (const line of bodyLines) {
		const sectionMatch = line.match(/^##\s+(.{1,2048})$/u);
		if (sectionMatch) {
			currentSection = sectionMatch[1]?.trim() ?? "";
			continue;
		}
		const bulletMatch = line.match(
			/^[-*]\s+\[salience\s+([0-9]+(?:\.[0-9]+)?)\]\s+(.+)$/u,
		);
		if (!bulletMatch) continue;
		const salience = Number.parseFloat(bulletMatch[1] ?? "0");
		if (!Number.isFinite(salience)) continue;
		items.push({
			section: currentSection,
			salience,
			text: bulletMatch[2]?.trim() ?? "",
		});
		if (items.length >= MAX_PARSED_ITEMS) break;
	}
	return items;
}

function notFound(anchor: string): ToolResult<WorkingSetNotFound> {
	return {
		ok: true,
		data: {
			anchor,
			found: false,
			reason:
				"No visible checkpoint was found in the requested scope. " +
				"Use mneme_checkpoint_list in the same scope to discover anchors.",
		},
	};
}

export function workingSetLoadTool(
	args: WorkingSetLoadInput,
	vault: VaultConfig,
): ToolResult<WorkingSetLoadOutput | WorkingSetNotFound> {
	const requestedScope = effectiveScope(args.scope, () => vault.defaultScope());
	let absPath: string | null;
	try {
		absPath = resolveCheckpointPath(args.anchor, requestedScope, vault);
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "Could not resolve checkpoint state safely.",
			},
		};
	}
	if (absPath === null) return notFound(args.anchor);

	let raw: string;
	try {
		raw = boundedRead(absPath, MAX_CHECKPOINT_BYTES, "checkpoint file");
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "Could not read checkpoint state safely.",
			},
		};
	}

	const { frontmatter, bodyStart, valid } = parseFrontmatter(raw);
	if (
		!valid ||
		frontmatter.anchor !== args.anchor ||
		!legacyScopeMatches(frontmatter.scope, requestedScope)
	) {
		return notFound(args.anchor);
	}

	const allItems = parseItems(raw.split(/\r?\n/u).slice(bodyStart)).sort(
		(a, b) => b.salience - a.salience,
	);
	const totalItems = allItems.length;
	const limited =
		args.top_k === undefined ? allItems : allItems.slice(0, args.top_k);
	const safeItems = limited.map((item) => ({
		section: neutralize(redact(item.section).text),
		salience: item.salience,
		text: wrapUntrusted(redact(item.text).text, "checkpoint-bullet"),
	}));
	const safeFrontmatter = Object.fromEntries(
		Object.entries(frontmatter).map(([key, value]) => [
			neutralize(redact(key).text),
			neutralize(redact(value).text),
		]),
	);

	return {
		ok: true,
		data: {
			anchor: args.anchor,
			path: relative(vault.root, absPath),
			frontmatter: safeFrontmatter,
			items: safeItems,
			total_items: totalItems,
			truncated: limited.length < totalItems,
			scope: frontmatter.scope ?? "default",
		},
	};
}
