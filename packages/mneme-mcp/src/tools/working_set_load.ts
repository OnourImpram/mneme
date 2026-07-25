/**
 * mneme_working_set_load, scoped checkpoint rehydration for the CCE.
 *
 * Checkpoint indexes and Markdown are untrusted derived inputs. Reads and
 * parsing are bounded. Index and frontmatter scope metadata must be concrete
 * and agree before any checkpoint content is returned.
 */

import {
	closeSync,
	existsSync,
	fstatSync,
	opendirSync,
	openSync,
	readSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve as resolvePath } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize, wrapUntrusted } from "../injection.js";
import { redact } from "../privacy.js";
import { assertWithinVault } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

const MAX_INDEX_BYTES = 16 * 1024 * 1024;
const MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024;
const MAX_DIRECTORY_ENTRIES = 10_000;
const MAX_FRONTMATTER_LINES = 512;
const MAX_FRONTMATTER_VALUE_LENGTH = 4096;
const MAX_PARSED_ITEMS = 5_000;
const MAX_SECTION_LENGTH = 2_048;
const MAX_ITEM_TEXT_LENGTH = 32_768;
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

export const WorkingSetLoadInputSchema = z.object({
	anchor: z
		.string()
		.min(1)
		.max(256)
		.regex(ANCHOR_PATTERN)
		.describe("Checkpoint anchor returned by mneme_checkpoint_list."),
	top_k: z
		.number()
		.int()
		.positive()
		.max(500)
		.describe("Return only the highest-salience items up to this limit.")
		.optional(),
	scope: RequestScopeSchema.describe(
		"Scope to load. Omit for the configured default scope. Pass the exact literal '*' only for an explicit cross-scope read.",
	).optional(),
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
	anchor: z.string().min(1).max(256).regex(ANCHOR_PATTERN),
	path: z.string().min(1).max(4096),
	scope: ConcreteScopeSchema,
});

interface ParsedFrontmatter {
	frontmatter: Record<string, string>;
	bodyStart: number;
	valid: boolean;
}

interface ResolvedCheckpoint {
	absPath: string;
	raw: string;
	scope: string;
}

function indexPath(vault: VaultConfig): string {
	return join(vault.stateDir, "checkpoints", "index.jsonl");
}

function boundedRead(path: string, limit: number, label: string): string {
	const descriptor = openSync(path, "r");
	try {
		const stat = fstatSync(descriptor);
		if (!stat.isFile()) throw new Error(`${label} is not a regular file`);
		if (stat.size > limit) throw new Error(`${label} exceeds the safety limit`);
		const bytes = Buffer.alloc(stat.size + 1);
		const bytesRead = readSync(descriptor, bytes, 0, bytes.length, 0);
		if (bytesRead > stat.size || bytesRead > limit) {
			throw new Error(`${label} grew beyond the safety limit`);
		}
		return bytes.subarray(0, bytesRead).toString("utf8");
	} finally {
		closeSync(descriptor);
	}
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

function parseFrontmatter(text: string): ParsedFrontmatter {
	const lines = text.split(/\r?\n/u);
	const result: Record<string, string> = {};
	const seenKeys = new Set<string>();
	if (lines[0]?.trim() !== "---") {
		return { frontmatter: result, bodyStart: 0, valid: false };
	}

	let closeIndex = -1;
	const limit = Math.min(lines.length, MAX_FRONTMATTER_LINES);
	for (let index = 1; index < limit; index += 1) {
		const line = lines[index] ?? "";
		if (line.trim() === "---") {
			closeIndex = index;
			break;
		}
		const match = line.match(/^([^:]{1,128}):\s*(.*)$/u);
		if (!match) continue;
		const key = match[1]?.trim() ?? "";
		if (key.length === 0 || seenKeys.has(key)) {
			return { frontmatter: {}, bodyStart: 0, valid: false };
		}
		let value = match[2]?.trim() ?? "";
		if (value.length > MAX_FRONTMATTER_VALUE_LENGTH) continue;
		if (value.startsWith('"') && value.endsWith('"')) {
			try {
				const decoded = JSON.parse(value);
				if (typeof decoded !== "string") continue;
				value = decoded;
			} catch {
				continue;
			}
		} else if (value.startsWith("'") && value.endsWith("'")) {
			value = value.slice(1, -1).replace(/''/gu, "'");
		}
		seenKeys.add(key);
		result[key] = value;
	}

	if (closeIndex === -1) {
		return { frontmatter: {}, bodyStart: 0, valid: false };
	}
	return { frontmatter: result, bodyStart: closeIndex + 1, valid: true };
}

function checkpointScope(
	text: string,
	anchor: string,
	requestedScope: string,
): { parsed: ParsedFrontmatter; scope: string } | null {
	const parsed = parseFrontmatter(text);
	if (!parsed.valid || parsed.frontmatter.anchor !== anchor) return null;
	const scope = ConcreteScopeSchema.safeParse(parsed.frontmatter.scope);
	if (!scope.success || !scopeMatches(scope.data, requestedScope)) return null;
	return { parsed, scope: scope.data };
}

function resolveCheckpoint(
	anchor: string,
	requestedScope: string,
	vault: VaultConfig,
): ResolvedCheckpoint | null {
	const idxPath = indexPath(vault);
	if (existsSync(idxPath)) {
		const raw = boundedRead(idxPath, MAX_INDEX_BYTES, "checkpoint index");
		let sawAnchor = false;
		for (const line of raw.split(/\r?\n/u).reverse()) {
			const trimmed = line.trim();
			if (trimmed.length === 0) continue;
			let value: unknown;
			try {
				value = JSON.parse(trimmed);
			} catch {
				continue;
			}
			if (
				typeof value === "object" &&
				value !== null &&
				(value as Record<string, unknown>).anchor === anchor
			) {
				sawAnchor = true;
			}
			const parsed = IndexRecordSchema.safeParse(value);
			if (!parsed.success || parsed.data.anchor !== anchor) continue;
			if (!scopeMatches(parsed.data.scope, requestedScope)) continue;
			const candidate = containedPath(parsed.data.path, vault);
			if (candidate === null || !existsSync(candidate)) continue;
			const checkpointRaw = boundedRead(
				candidate,
				MAX_CHECKPOINT_BYTES,
				"checkpoint file",
			);
			const metadata = checkpointScope(checkpointRaw, anchor, requestedScope);
			if (metadata?.scope !== parsed.data.scope) continue;
			return {
				absPath: candidate,
				raw: checkpointRaw,
				scope: metadata.scope,
			};
		}
		if (sawAnchor) return null;
	}

	const checkpointsDir = join(vault.root, "checkpoints");
	if (!existsSync(checkpointsDir)) return null;
	const directory = opendirSync(checkpointsDir);
	try {
		let entryCount = 0;
		for (
			let entry = directory.readSync();
			entry !== null;
			entry = directory.readSync()
		) {
			entryCount += 1;
			if (entryCount > MAX_DIRECTORY_ENTRIES) {
				throw new Error("checkpoint directory exceeds the entry safety limit");
			}
			if (!entry.isFile() || !entry.name.endsWith(`-${anchor}.md`)) continue;
			const candidate = containedPath(join(checkpointsDir, entry.name), vault);
			if (candidate === null) continue;
			const checkpointRaw = boundedRead(
				candidate,
				MAX_CHECKPOINT_BYTES,
				"checkpoint file",
			);
			const metadata = checkpointScope(checkpointRaw, anchor, requestedScope);
			if (metadata === null) continue;
			return {
				absPath: candidate,
				raw: checkpointRaw,
				scope: metadata.scope,
			};
		}
	} finally {
		directory.closeSync();
	}
	return null;
}

function parseItems(bodyLines: string[]): WorkingSetItem[] {
	const items: WorkingSetItem[] = [];
	let currentSection = "";
	for (const line of bodyLines) {
		const sectionMatch = line.match(/^##\s+(.+)$/u);
		if (sectionMatch) {
			const section = sectionMatch[1]?.trim() ?? "";
			currentSection =
				section.length <= MAX_SECTION_LENGTH ? section : currentSection;
			continue;
		}
		const bulletMatch = line.match(
			/^[-*]\s+\[salience\s+([0-9]+(?:\.[0-9]+)?)\]\s+(.+)$/u,
		);
		if (!bulletMatch) continue;
		const salience = Number.parseFloat(bulletMatch[1] ?? "0");
		const text = bulletMatch[2]?.trim() ?? "";
		if (!Number.isFinite(salience) || text.length > MAX_ITEM_TEXT_LENGTH)
			continue;
		items.push({ section: currentSection, salience, text });
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

function safeFrontmatter(
	frontmatter: Record<string, string>,
): Record<string, string> {
	return Object.fromEntries(
		Object.entries(frontmatter).map(([key, value]) => [
			neutralize(redact(key).text),
			neutralize(redact(value).text),
		]),
	);
}

export function workingSetLoadTool(
	args: WorkingSetLoadInput,
	vault: VaultConfig,
): ToolResult<WorkingSetLoadOutput | WorkingSetNotFound> {
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

	let resolved: ResolvedCheckpoint | null;
	try {
		resolved = resolveCheckpoint(args.anchor, requestedScope, vault);
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "Could not resolve checkpoint state safely.",
			},
		};
	}
	if (resolved === null) return notFound(args.anchor);

	const parsed = parseFrontmatter(resolved.raw);
	if (!parsed.valid) return notFound(args.anchor);
	const allItems = parseItems(
		resolved.raw.split(/\r?\n/u).slice(parsed.bodyStart),
	).sort((left, right) => right.salience - left.salience);
	const totalItems = allItems.length;
	const limited =
		args.top_k === undefined ? allItems : allItems.slice(0, args.top_k);
	const sanitized = limited.map((item) => ({
		section: neutralize(redact(item.section).text),
		salience: item.salience,
		text: wrapUntrusted(redact(item.text).text, "checkpoint-bullet"),
	}));

	return {
		ok: true,
		data: {
			anchor: args.anchor,
			path: relative(vault.root, resolved.absPath),
			frontmatter: safeFrontmatter(parsed.frontmatter),
			items: sanitized,
			total_items: totalItems,
			truncated: limited.length < totalItems,
			scope: resolved.scope,
		},
	};
}
