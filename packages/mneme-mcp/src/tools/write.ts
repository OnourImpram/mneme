/**
 * mneme_write — Atomically append or replace a markdown section.
 *
 * Path containment is enforced by `assertWithinVault`. The atomic write
 * pattern is the same temp-file-plus-rename used by the indexer staging
 * layer. Frontmatter, if provided, is serialized as YAML and prepended
 * to fresh files only. Existing files keep their frontmatter unchanged.
 *
 * Phase J post-Codex-review fix (C4 sacred constraint): every string
 * value flowing into the vault (`args.content`, `args.section`, every
 * frontmatter scalar) is run through the private-tag redactor before
 * any atomicWriteText call. Redactions are reflected in the response
 * envelope so the caller can audit at the boundary.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve as resolvePath } from "node:path";
import { z } from "zod";
import { appendAuditRecord } from "../audit.js";
import { ERROR_CODES } from "../errors.js";
import { redact } from "../privacy.js";
import {
	assertWithinVault,
	atomicWriteText,
	VaultPathError,
} from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

function redactString(s: string): { redacted: string; count: number } {
	const r = redact(s);
	return { redacted: r.text, count: r.count };
}

function redactFrontmatter(fm: Record<string, string | number> | undefined): {
	value: Record<string, string | number> | undefined;
	count: number;
} {
	if (!fm) return { value: fm, count: 0 };
	const out: Record<string, string | number> = {};
	let count = 0;
	for (const [k, v] of Object.entries(fm)) {
		if (typeof v === "string") {
			const r = redact(v);
			out[k] = r.text;
			count += r.count;
		} else {
			out[k] = v;
		}
	}
	return { value: out, count };
}

export const WriteInputSchema = z.object({
	path: z
		.string()
		.min(1)
		.max(1024)
		.describe("Target path relative to the vault root."),
	section: z
		.string()
		.min(1)
		.max(2048)
		.describe("H2 heading text without the leading markdown marker."),
	content: z
		.string()
		.max(100000)
		.describe("Markdown body for the section."),
	/** When true, replaces an existing section with the same heading. */
	replace: z
		.boolean()
		.default(false)
		.describe("Replace an existing section with the same heading."),
	/** Optional YAML frontmatter applied to newly created files only. */
	frontmatter: z
		.record(z.string(), z.union([z.string(), z.number()]))
		.describe("YAML frontmatter applied only when a new file is created.")
		.optional(),
});

export type WriteInput = z.infer<typeof WriteInputSchema>;

export interface WriteOutput {
	path: string;
	bytes_written: number;
	operation: "added" | "replaced";
	created_new_file: boolean;
	redactions_applied: number;
}

export function writeTool(
	args: WriteInput,
	vault: VaultConfig,
): ToolResult<WriteOutput> {
	const targetAbs = resolvePath(join(vault.root, args.path));
	try {
		assertWithinVault(vault.root, targetAbs);
	} catch (err) {
		if (err instanceof VaultPathError) {
			return {
				ok: false,
				error: { code: ERROR_CODES.PATH_OUTSIDE_VAULT, message: err.message },
			};
		}
		throw err;
	}

	// C4 sacred constraint: redact every string entering the vault.
	const sectionRed = redactString(args.section);
	const contentRed = redactString(args.content);
	const fmRed = redactFrontmatter(args.frontmatter);
	const totalRedactions = sectionRed.count + contentRed.count + fmRed.count;

	const headingLine = `## ${sectionRed.redacted}`;
	let existing = "";
	let createdNew = false;
	if (existsSync(targetAbs)) {
		existing = readFileSync(targetAbs, "utf8");
	} else {
		createdNew = true;
		// M3: always stamp scope into new-file frontmatter so the indexer can
		// apply scope isolation on the next rebuild. Caller-supplied `scope`
		// key in frontmatter wins; otherwise inject config.defaultScope.
		const fmForNew: Record<string, string | number> = {
			...(fmRed.value ?? {}),
		};
		if (!("scope" in fmForNew)) {
			fmForNew.scope = vault.defaultScope();
		}
		existing = serializeFrontmatter(fmForNew);
	}

	// F2: reject content whose body contains a bare H2 heading, which would
	// corrupt the section-boundary scanner in replaceSection.
	validateSectionBody(contentRed.redacted);

	let finalContent: string;
	let operation: "added" | "replaced";
	if (args.replace && sectionExists(existing, sectionRed.redacted)) {
		finalContent = replaceSection(
			existing,
			sectionRed.redacted,
			contentRed.redacted,
		);
		operation = "replaced";
	} else {
		const sep = appendSeparator(existing);
		finalContent = `${existing}${sep}${headingLine}\n\n${contentRed.redacted}\n`;
		operation = "added";
	}

	// Ensure parent directory is also inside the vault before mkdir.
	const parent = dirname(targetAbs);
	try {
		assertWithinVault(vault.root, parent);
	} catch (err) {
		if (err instanceof VaultPathError) {
			return {
				ok: false,
				error: { code: ERROR_CODES.PATH_OUTSIDE_VAULT, message: err.message },
			};
		}
		throw err;
	}

	try {
		atomicWriteText(targetAbs, finalContent);
	} catch (err) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: err instanceof Error ? err.message : String(err),
			},
		};
	}

	const relativePath = args.path.replace(/\\/g, "/");

	// T4: append tamper-evident audit record whenever redaction occurred.
	// Non-fatal: audit failure is warned but never blocks the write result.
	if (totalRedactions > 0) {
		appendAuditRecord(vault.stateDir, relativePath, totalRedactions);
	}

	return {
		ok: true,
		data: {
			path: relativePath,
			bytes_written: Buffer.byteLength(finalContent, "utf8"),
			operation,
			created_new_file: createdNew,
			redactions_applied: totalRedactions,
		},
	};
}

/**
 * F2: Reject any section body that contains a bare H2 heading line.
 * A `## ` line in the body would be misdetected as a section boundary
 * by `replaceSection`, causing progressive file corruption on subsequent
 * writes. H3+ sub-headings (`### `, `#### `, …) are permitted.
 */
function validateSectionBody(body: string): void {
	for (const line of body.split("\n")) {
		if (/^## /.test(line)) {
			throw new Error(
				"section body may not contain a '## ' H2 heading; use H3+ for sub-headings",
			);
		}
	}
}

/**
 * F3: Compute the separator to insert between the existing file content
 * and the new section heading so exactly one blank line appears.
 *
 * - Empty file          → "" (heading starts at byte 0)
 * - Ends with "\n\n"   → "" (blank line already present)
 * - Ends with "\n"     → "\n" (one more newline = one blank line)
 * - No trailing newline → "\n\n" (add a full blank line)
 */
function appendSeparator(existing: string): string {
	if (existing.length === 0) return "";
	if (existing.endsWith("\n\n")) return "";
	if (existing.endsWith("\n")) return "\n";
	return "\n\n";
}

function sectionExists(text: string, heading: string): boolean {
	const target = `## ${heading}`;
	for (const line of text.split("\n")) {
		if (line.trimEnd() === target) return true;
	}
	return false;
}

/**
 * Replace the body of an H2 section identified by its heading text.
 *
 * Line-by-line splice avoids regex pitfalls around end-of-string and
 * multi-line flags that bit the first cut. Heading match is exact
 * (after right-trim), the body runs until the next `## ` heading
 * or the end of file.
 */
function replaceSection(text: string, heading: string, body: string): string {
	const target = `## ${heading}`;
	const lines = text.split("\n");
	let start = -1;
	for (let i = 0; i < lines.length; i++) {
		if (lines[i]?.trimEnd() === target) {
			start = i;
			break;
		}
	}
	if (start === -1) return text;
	let end = lines.length;
	for (let i = start + 1; i < lines.length; i++) {
		if (lines[i]?.startsWith("## ")) {
			end = i;
			break;
		}
	}
	const newSection = [target, "", body, ""];
	return [...lines.slice(0, start), ...newSection, ...lines.slice(end)].join(
		"\n",
	);
}

const FRONTMATTER_KEY_RE = /^[A-Za-z_][A-Za-z0-9_-]*$/;

function hasControlChar(s: string): boolean {
	for (let i = 0; i < s.length; i++) {
		const code = s.charCodeAt(i);
		if (code <= 0x1f || code === 0x7f) {
			return true;
		}
	}
	return false;
}

/**
 * Serialize a frontmatter record to YAML.
 *
 * Phase J post-Codex-review fix: keys are validated against an
 * identifier-shape regex (no colons, newlines, or document delimiters
 * allowed). String values are emitted inside double quotes with inner
 * quotes and backslashes escaped, and any value containing a control
 * character is rejected before write. Numbers serialize bare.
 */
function serializeFrontmatter(fm: Record<string, string | number>): string {
	const lines = ["---"];
	for (const [k, v] of Object.entries(fm)) {
		if (!FRONTMATTER_KEY_RE.test(k)) {
			throw new Error(
				`Unsafe frontmatter key '${k}'. Keys must match ${FRONTMATTER_KEY_RE}.`,
			);
		}
		if (typeof v === "number") {
			if (!Number.isFinite(v)) {
				throw new Error(`Non-finite frontmatter number for key '${k}'`);
			}
			lines.push(`${k}: ${v}`);
			continue;
		}
		if (hasControlChar(v)) {
			throw new Error(`Control character in frontmatter value for key '${k}'`);
		}
		const escaped = v.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
		lines.push(`${k}: "${escaped}"`);
	}
	lines.push("---", "");
	return `${lines.join("\n")}\n`;
}
