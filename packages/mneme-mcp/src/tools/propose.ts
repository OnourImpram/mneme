/**
 * mneme_propose — Queue a memory-edit proposal for a policy drain.
 *
 * The MCP server never applies an agent-initiated edit directly. It stages a
 * redacted, scope-bound proposal under `<state>/proposals/pending.jsonl`; the
 * Python policy drain applies or refuses the claimed queue snapshot. Queue
 * appends are bounded and serialized with the same O_EXCL lock used by the
 * Python implementation. No LLM or network request occurs on this path.
 */

import { createHash } from "node:crypto";
import {
	appendFileSync,
	closeSync,
	existsSync,
	lstatSync,
	mkdirSync,
	openSync,
	readFileSync,
	rmSync,
	statSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { redact } from "../privacy.js";
import { ConcreteScopeSchema, concreteScopeOrNull } from "../scope.js";
import { assertWithinVault, VaultPathError } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";
import type { ToolResult } from "./common.js";

/** Same namespace bytes the Python engine uses (uuid.UUID("6ba7b810-...")). */
const UUID5_NAMESPACE = "6ba7b8109dad11d180b400c04fd430c8";
const MAX_QUEUE_BYTES = 16 * 1024 * 1024;
const MAX_QUEUE_RECORD_BYTES = 1024 * 1024;
const QUEUE_LOCK_TIMEOUT_MS = 1_000;
const QUEUE_LOCK_STALE_MS = 10_000;
const QUEUE_LOCK_POLL_MS = 10;

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
	/** Concrete scope to bind to the queued durable proposal. */
	scope: ConcreteScopeSchema.optional(),
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
	scope: string;
	note: string;
}

/** RFC-4122 v5 UUID over SHA-1, byte-compatible with Python's uuid.uuid5. */
function uuid5(namespaceHex: string, name: string): string {
	const ns = Buffer.from(namespaceHex, "hex");
	const digest = createHash("sha1")
		.update(Buffer.concat([ns, Buffer.from(name, "utf-8")]))
		.digest();
	const bytes = Buffer.from(digest.subarray(0, 16));
	bytes[6] = ((bytes[6] as number) & 0x0f) | 0x50;
	bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80;
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

function acquireQueueLock(queuePath: string): () => void {
	const lockPath = `${queuePath}.lock`;
	const deadline = Date.now() + QUEUE_LOCK_TIMEOUT_MS;
	while (true) {
		try {
			const fd = openSync(lockPath, "wx", 0o600);
			try {
				appendFileSync(fd, String(process.pid), "utf-8");
			} finally {
				closeSync(fd);
			}
			return () => rmSync(lockPath, { force: true });
		} catch (err) {
			const code = (err as NodeJS.ErrnoException).code;
			if (code !== "EEXIST") throw err;
			try {
				if (Date.now() - statSync(lockPath).mtimeMs > QUEUE_LOCK_STALE_MS) {
					rmSync(lockPath, { force: true });
					continue;
				}
			} catch {
				continue;
			}
			if (Date.now() >= deadline) throw new Error("proposal queue is busy");
			const pollEnd = Date.now() + QUEUE_LOCK_POLL_MS;
			while (Date.now() < pollEnd) {
				// Bounded synchronous wait. Proposal staging is not a critical hook path.
			}
		}
	}
}

function appendQueueRecord(queuePath: string, record: Record<string, unknown>): void {
	const encoded = `${JSON.stringify(record)}\n`;
	const recordBytes = Buffer.byteLength(encoded, "utf-8");
	if (recordBytes > MAX_QUEUE_RECORD_BYTES) {
		throw new Error("proposal queue record exceeds the safe size bound");
	}
	mkdirSync(dirname(queuePath), { recursive: true });
	assertWithinVault(dirname(dirname(queuePath)), queuePath);
	const release = acquireQueueLock(queuePath);
	try {
		if (existsSync(queuePath)) {
			const queueStat = lstatSync(queuePath);
			if (!queueStat.isFile() || queueStat.isSymbolicLink()) {
				throw new Error("proposal queue is not a regular file");
			}
			if (queueStat.size + recordBytes > MAX_QUEUE_BYTES) {
				throw new Error("proposal queue exceeds the safe size bound");
			}
		}
		appendFileSync(queuePath, encoded, { encoding: "utf-8", mode: 0o600 });
	} finally {
		release();
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
					message: "Proposal target escapes the vault.",
				},
			};
		}
		throw err;
	}

	const scope = concreteScopeOrNull(args.scope ?? vault.defaultScope());
	if (scope === null) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INVALID_ARGUMENT,
				message: "Proposal scope must be a concrete valid identifier.",
			},
		};
	}
	const { text: redacted, count: redactions } = redact(args.content);
	const category = args.category.toUpperCase();
	const trust = "agent";
	let seed = `${args.action}\x00${args.path}\x00${category}\x00${trust}\x00${redacted}`;
	// Preserve default-scope IDs while preventing non-default tenants from
	// aliasing the same proposal identity.
	if (scope !== "default") seed = `${seed}\x00${scope}`;
	const proposalId = uuid5(UUID5_NAMESPACE, seed);

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
		appendQueueRecord(queuePath, record);
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "The proposal could not be queued safely.",
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
			scope,
			note: autoEligible
				? "Eligible for autonomous apply at the next policy drain."
				: "Will be held for the human approval flow at the next drain.",
		},
	};
}
