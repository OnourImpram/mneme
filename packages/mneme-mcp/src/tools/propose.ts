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

import { createHash, randomBytes } from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fstatSync,
	fsyncSync,
	lstatSync,
	mkdirSync,
	openSync,
	readFileSync,
	renameSync,
	unlinkSync,
	writeSync,
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
const MAX_QUEUE_LINES = 10_000;
const MAX_QUEUE_RECORD_BYTES = 1024 * 1024;
const QUEUE_LOCK_TIMEOUT_MS = 1_000;
const QUEUE_LOCK_STALE_MS = 10_000;

function errorCode(error: unknown): string | undefined {
	return (error as NodeJS.ErrnoException).code;
}

function isContentionError(error: unknown): boolean {
	const code = errorCode(error);
	return code === "EEXIST" || code === "EACCES" || code === "EPERM";
}

function sleepSync(milliseconds: number): void {
	Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function releaseOwnedLock(lockPath: string, token: string): void {
	try {
		if (readFileSync(lockPath, "utf-8") === token) unlinkSync(lockPath);
	} catch {
		// A stale-lock recovery may already have removed this exact lease.
	}
}

function acquireQueueLock(queuePath: string, vaultRoot: string): () => void {
	const lockPath = `${queuePath}.lock`;
	const deadline = Date.now() + QUEUE_LOCK_TIMEOUT_MS;
	const token = `${process.pid}:${randomBytes(16).toString("hex")}`;
	const noFollow = constants.O_NOFOLLOW ?? 0;

	while (true) {
		let fd: number | null = null;
		try {
			assertWithinVault(vaultRoot, lockPath);
			fd = openSync(
				lockPath,
				constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | noFollow,
				0o600,
			);
			writeSync(fd, token, 0, "utf-8");
			fsyncSync(fd);
			closeSync(fd);
			fd = null;
			return () => releaseOwnedLock(lockPath, token);
		} catch (error) {
			if (fd !== null) closeSync(fd);
			if (!isContentionError(error)) throw error;
			try {
				const lockStat = lstatSync(lockPath);
				if (lockStat.isSymbolicLink()) {
					throw new Error("proposal queue lock is not a regular file");
				}
				if (Date.now() - lockStat.mtimeMs > QUEUE_LOCK_STALE_MS) {
					const quarantine = `${lockPath}.stale-${randomBytes(16).toString("hex")}`;
					renameSync(lockPath, quarantine);
					unlinkSync(quarantine);
					continue;
				}
			} catch (staleError) {
				const staleCode = errorCode(staleError);
				if (staleCode !== "ENOENT") throw staleError;
				continue;
			}
			if (Date.now() >= deadline) throw new Error("proposal queue is busy");
			sleepSync(10);
		}
	}
}

function appendQueueRecord(
	queuePath: string,
	payload: Buffer,
	vaultRoot: string,
): void {
	if (payload.byteLength > MAX_QUEUE_RECORD_BYTES) {
		throw new Error("proposal queue record exceeds the configured size limit");
	}
	const release = acquireQueueLock(queuePath, vaultRoot);
	try {
		assertWithinVault(vaultRoot, queuePath);
		const noFollow = constants.O_NOFOLLOW ?? 0;
		let readFd: number | null = null;
		try {
			readFd = openSync(queuePath, constants.O_RDONLY | noFollow);
			assertWithinVault(vaultRoot, queuePath);
			const descriptorStat = fstatSync(readFd);
			const pathStat = lstatSync(queuePath);
			if (
				!descriptorStat.isFile() ||
				!pathStat.isFile() ||
				pathStat.isSymbolicLink() ||
				descriptorStat.dev !== pathStat.dev ||
				descriptorStat.ino !== pathStat.ino
			) {
				throw new Error("proposal queue is not a stable regular file");
			}
			if (descriptorStat.size + payload.byteLength > MAX_QUEUE_BYTES) {
				throw new Error("proposal queue exceeds the configured size limit");
			}
			const existing = readFileSync(readFd);
			let lineCount = 0;
			for (const byte of existing) if (byte === 0x0a) lineCount += 1;
			if (lineCount >= MAX_QUEUE_LINES) {
				throw new Error("proposal queue exceeds the configured line limit");
			}
		} catch (error) {
			if (errorCode(error) !== "ENOENT") throw error;
		} finally {
			if (readFd !== null) closeSync(readFd);
		}

		const fd = openSync(
			queuePath,
			constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | noFollow,
			0o600,
		);
		try {
			assertWithinVault(vaultRoot, queuePath);
			const queueStat = fstatSync(fd);
			const queuePathStat = lstatSync(queuePath);
			if (
				!queueStat.isFile() ||
				!queuePathStat.isFile() ||
				queuePathStat.isSymbolicLink() ||
				queueStat.dev !== queuePathStat.dev ||
				queueStat.ino !== queuePathStat.ino
			) {
				throw new Error("proposal queue is not a stable regular file");
			}
			if (queueStat.size + payload.byteLength > MAX_QUEUE_BYTES) {
				throw new Error("proposal queue exceeds the configured size limit");
			}
			let offset = 0;
			while (offset < payload.byteLength) {
				const written = writeSync(
					fd,
					payload,
					offset,
					payload.byteLength - offset,
				);
				if (written <= 0)
					throw new Error("proposal queue write made no progress");
				offset += written;
			}
			fsyncSync(fd);
		} finally {
			closeSync(fd);
		}
	} finally {
		release();
	}
}

export const ProposeInputSchema = z.object({
	action: z
		.enum(["create", "update", "delete"] as const)
		.describe("Requested memory edit operation."),
	path: z
		.string()
		.min(1)
		.max(1024)
		.describe("Target path relative to the vault root."),
	content: z
		.string()
		.max(100000)
		.default("")
		.describe("Proposed full file content. Ignored for delete operations."),
	category: z
		.enum([
			"ephemeral",
			"identity",
			"preference",
			"clinical",
			"legal",
			"financial",
		] as const)
		.default("ephemeral")
		.describe("Sensitivity category used by the approval policy."),
	edit_class: z
		.enum([
			"dedup-merge",
			"typo-fix",
			"tag-normalize",
			"supersede-link",
			"stale-archive",
		] as const)
		.describe("Low-risk mechanical edit class for autonomous eligibility.")
		.optional(),
	/**
	 * Scope to stamp on the proposal record. Omit to use config.defaultScope().
	 * Stored in the JSONL record for the Python drain to apply on write.
	 */
	scope: ConcreteScopeSchema.describe(
		"Scope stamped on the proposal. Omit for the configured default scope.",
	).optional(),
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
		assertWithinVault(vault.root, queuePath);
		mkdirSync(dirname(queuePath), { recursive: true });
		assertWithinVault(vault.root, queuePath);
		appendQueueRecord(
			queuePath,
			Buffer.from(`${JSON.stringify(record)}\n`, "utf-8"),
			vault.root,
		);
	} catch {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.IO_ERROR,
				message: "Could not queue proposal safely.",
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
