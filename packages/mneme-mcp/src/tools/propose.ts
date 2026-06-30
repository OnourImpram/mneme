/**
 * mneme_propose — Queue a memory-edit proposal for a policy drain.
 *
 * The accountable-autonomy contract (conflict-resolution #4): the MCP
 * server NEVER applies an agent-initiated edit directly. It stages the
 * proposal as one JSONL record under
 * `<state>/proposals/pending.jsonl`; the Python engine
 * (`mneme_core.memory_apply.drain_proposals`) applies the queue under
 * the operator's policy.json — autonomously for allowed low-risk edit
 * classes, refused-and-archived otherwise. Durable categories
 * (identity, preference, clinical, legal, financial) are queued for
 * the human approval flow and are never auto-applied.
 *
 * The record shape mirrors `mneme_core.memory_apply.queue_proposal`
 * exactly, and the proposal_id is the same RFC-4122 uuid5 the Python
 * side derives (same namespace, same NUL-joined seed), so identical
 * proposals from either language share one identity.
 *
 * C4 sacred constraint: content is redacted before it touches disk.
 * No LLM, no network.
 */

import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { redact } from "../privacy.js";
import { assertWithinVault, VaultPathError } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

/** Same namespace bytes the Python engine uses (uuid.UUID("6ba7b810-...")). */
const UUID5_NAMESPACE = "6ba7b8109dad11d180b400c04fd430c8";

export const ProposeInputSchema = z.object({
	action: z.enum(["create", "update", "delete"] as const),
	path: z.string().min(1).max(1024),
	content: z.string().max(100000).default(""),
	category: z
		.enum([
			"ephemeral",
			"identity",
			"preference",
			"clinical",
			"legal",
			"financial",
		] as const)
		.default("ephemeral"),
	edit_class: z
		.enum([
			"dedup-merge",
			"typo-fix",
			"tag-normalize",
			"supersede-link",
			"stale-archive",
		] as const)
		.optional(),
	/**
	 * Scope to stamp on the proposal record. Omit to use config.defaultScope().
	 * Stored in the JSONL record for the Python drain to apply on write.
	 */
	scope: z.string().optional(),
});

export type ProposeInput = z.infer<typeof ProposeInputSchema>;

export interface ProposeOutput {
	proposal_id: string;
	status: "queued";
	category: string;
	edit_class: string | null;
	auto_eligible: boolean;
	queue: string;
	redactions_applied: number;
	note: string;
}

/** RFC-4122 v5 UUID over SHA-1, byte-compatible with Python's uuid.uuid5. */
function uuid5(namespaceHex: string, name: string): string {
	const ns = Buffer.from(namespaceHex, "hex");
	const digest = createHash("sha1")
		.update(Buffer.concat([ns, Buffer.from(name, "utf-8")]))
		.digest();
	const bytes = Buffer.from(digest.subarray(0, 16));
	bytes[6] = ((bytes[6] as number) & 0x0f) | 0x50; // version 5
	bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80; // RFC-4122 variant
	const hex = bytes.toString("hex");
	return (
		`${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-` +
		`${hex.slice(16, 20)}-${hex.slice(20)}`
	);
}

function policyDeclaresClass(vault: VaultConfig, editClass: string): boolean {
	const policyPath = join(vault.stateDir, "policy.json");
	if (!existsSync(policyPath)) return false;
	try {
		const parsed = JSON.parse(readFileSync(policyPath, "utf-8")) as Record<
			string,
			unknown
		>;
		const allowed = parsed.auto_approve;
		return Array.isArray(allowed) && allowed.includes(editClass);
	} catch {
		return false;
	}
}

export function proposeTool(
	args: ProposeInput,
	vault: VaultConfig,
): ToolResult<ProposeOutput> {
	try {
		assertWithinVault(vault.root, join(vault.root, args.path));
	} catch (err) {
		if (err instanceof VaultPathError) {
			return {
				ok: false,
				error: {
					code: ERROR_CODES.PATH_OUTSIDE_VAULT,
					message: `Proposal target escapes the vault: ${args.path}`,
				},
			};
		}
		throw err;
	}

	const { text: redacted, count: redactions } = redact(args.content);
	const category = args.category.toUpperCase();
	const trust = "agent";
	const seed = `${args.action}\x00${args.path}\x00${category}\x00${trust}\x00${redacted}`;
	const proposalId = uuid5(UUID5_NAMESPACE, seed);

	// Resolve scope from caller arg or vault default. Not part of the uuid5
	// seed so the proposal_id stays byte-compatible with the Python engine.
	const scope = args.scope ?? vault.defaultScope();

	const record = {
		proposal_id: proposalId,
		action: args.action,
		target_path: args.path,
		content: args.action === "delete" ? "" : redacted,
		category,
		scope,
		status: "PENDING",
		trust,
		edit_class: args.edit_class ?? null,
		queued_at: new Date().toISOString(),
	};

	const queuePath = join(vault.stateDir, "proposals", "pending.jsonl");
	try {
		mkdirSync(dirname(queuePath), { recursive: true });
		appendFileSync(queuePath, `${JSON.stringify(record)}\n`, "utf-8");
	} catch (err) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: `Could not queue proposal: ${
					err instanceof Error ? err.message : String(err)
				}`,
			},
		};
	}

	const autoEligible =
		category === "EPHEMERAL" &&
		args.edit_class !== undefined &&
		policyDeclaresClass(vault, args.edit_class);

	return {
		ok: true,
		data: {
			proposal_id: proposalId,
			status: "queued",
			category,
			edit_class: args.edit_class ?? null,
			auto_eligible: autoEligible,
			queue: "proposals/pending.jsonl",
			redactions_applied: redactions,
			note: autoEligible
				? "Eligible for autonomous apply at the next policy drain."
				: "Will be held for the human approval flow at the next drain.",
		},
	};
}
