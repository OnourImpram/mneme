/**
 * Tamper-evident HMAC audit log for mneme_write redaction events.
 *
 * Every write that performs at least one redaction appends one JSON record
 * to an append-only daily log at:
 *
 *   <vault.stateDir>/audit/YYYY-MM-DD.jsonl
 *
 * Records are chained: each record's `hmac` field is
 *
 *   HMAC-SHA256(key, prev_hash_hex + JSON_of_record_without_hmac)
 *
 * where `prev_hash_hex` is the `hmac` of the immediately preceding record
 * in the same file, or the all-zeros 64-char hex string for the first record.
 * This chain lets an auditor detect insertions, deletions, or reordering.
 *
 * The HMAC key lives at <vault.stateDir>/audit-hmac.key (32 random bytes).
 * On POSIX the file is created with mode 0o600 via O_CREAT|O_EXCL so it
 * is never visible at a wider mode even momentarily — identical to the
 * pattern in mneme_core/kg/client.py `write_credentials`.
 *
 * Concurrent appends to the same daily file are serialized by a lock file
 * at <vault.stateDir>/audit/YYYY-MM-DD.lock, using an O_EXCL spin-lock
 * that mirrors the Python `file_lock` helper.
 *
 * Audit failures are NON-FATAL: they surface as a console.warn and are
 * returned as a boolean so callers can surface a warning without blocking
 * the write response.
 *
 * Core Invariant 2 compliance: no network or LLM calls are made anywhere
 * in this module.
 */

import { createHmac, randomBytes } from "node:crypto";
import {
	closeSync,
	existsSync,
	mkdirSync,
	openSync,
	readFileSync,
	rmSync,
	statSync,
	writeSync,
} from "node:fs";
import { join } from "node:path";
import { atomicWriteText } from "./vault/atomic_write.js";

/** Number of random bytes in the HMAC key file. */
const KEY_BYTES = 32;

/** All-zeros prev_hash for the first record in a file. */
const ZERO_HASH = "0".repeat(64);

/** Spin-lock poll interval in ms. */
const LOCK_POLL_MS = 10;

/**
 * Spin-lock acquisition timeout in ms.
 *
 * Audit is best-effort non-fatal, so a short bound is correct: a stale
 * lock file (left by a crashed process) would otherwise block the event
 * loop for up to 500ms before stale-lock stealing reclaims it, which is
 * acceptable for an off-hot-path audit append.
 */
const LOCK_TIMEOUT_MS = 500;

/**
 * Age threshold in ms for stale-lock stealing.
 *
 * A lock file whose mtime is older than this value is presumed abandoned by a
 * crashed process and may be forcibly deleted. This is intentionally much
 * larger than LOCK_TIMEOUT_MS: a slow-but-alive writer taking several seconds
 * on a heavily loaded runner must NOT have its lock stolen mid-append, as that
 * would allow two processes to write concurrently and corrupt the JSONL chain.
 * 10 seconds is far beyond any realistic audit-append duration while still
 * being short enough to recover promptly from a crashed process.
 */
const LOCK_STALE_MS = 10_000;

export interface AuditRecord {
	timestamp_iso: string;
	relative_path: string;
	redactions_applied: number;
	prev_hash: string;
	hmac: string;
}

/**
 * Load the HMAC key from `keyPath`, creating it at mode 0o600 if absent.
 *
 * On POSIX uses O_CREAT|O_EXCL at mode 0o600 so the file is never
 * world- or group-readable even for an instant (mirrors kg/client.py
 * `write_credentials`). On Windows the mode bits have no effect on
 * NTFS ACLs; operators must restrict the state directory via icacls.
 */
function loadOrCreateKey(keyPath: string): Buffer {
	if (existsSync(keyPath)) {
		const buf = readFileSync(keyPath);
		if (buf.length !== KEY_BYTES) {
			throw new Error(
				`audit-hmac.key is ${buf.length} bytes; expected ${KEY_BYTES}. ` +
					`Delete ${keyPath} to regenerate.`,
			);
		}
		return buf;
	}

	const key = randomBytes(KEY_BYTES);

	// Create parent dir first.
	mkdirSync(join(keyPath, ".."), { recursive: true });

	if (process.platform !== "win32") {
		// O_CREAT | O_EXCL | O_WRONLY at mode 0o600 — atomic, no wider window.
		// If another process races and creates the file first, O_EXCL throws
		// EEXIST and we fall through to the readFileSync path below.
		let fd: number;
		try {
			fd = openSync(keyPath, "ax", 0o600); // 'ax' = O_WRONLY|O_CREAT|O_EXCL
		} catch (err) {
			const code = (err as NodeJS.ErrnoException).code;
			if (code === "EEXIST") {
				// Race: another process won; read their key.
				return readFileSync(keyPath);
			}
			throw err;
		}
		try {
			writeSync(fd, key);
		} finally {
			closeSync(fd);
		}
		return key;
	}
	// Windows: mode 0o600 has no effect on NTFS ACLs, so the HMAC key may be
	// readable by other local processes. The operator must restrict the state
	// directory via icacls (e.g.:
	//   icacls "<stateDir>" /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"
	// ) to achieve the equivalent of POSIX 0o600. Full icacls automation is
	// deferred; see SECURITY.md for the manual step.
	// Use a write-new-only flag pair to avoid clobbering a concurrent writer.
	try {
		const fd = openSync(keyPath, "ax");
		try {
			writeSync(fd, key);
		} finally {
			closeSync(fd);
		}
		return key;
	} catch (err) {
		const code = (err as NodeJS.ErrnoException).code;
		if (code === "EEXIST") {
			return readFileSync(keyPath);
		}
		throw err;
	}
}

/**
 * Acquire an exclusive O_EXCL lock file at `lockPath`.
 *
 * Spins up to LOCK_TIMEOUT_MS (500ms), then throws.
 * Returns a release function (unlinks the lock file).
 *
 * Stale-lock stealing: on each spin iteration, if the lock file's mtime
 * is older than LOCK_STALE_MS (10s), the owning process is presumed crashed
 * and the lock is deleted best-effort so acquisition can be retried
 * immediately. A fresh lock held by an active writer (recent mtime) is still
 * respected within the acquisition timeout bound, preserving serialization
 * for concurrent live appends.
 *
 * Two-constant design: LOCK_TIMEOUT_MS controls how long THIS caller spins
 * before giving up; LOCK_STALE_MS controls how old a lock must be before
 * we conclude the holder crashed. Keeping them separate prevents a slow-but-
 * alive writer from having its lock stolen before it finishes appending.
 */
function acquireLock(lockPath: string): () => void {
	const deadline = Date.now() + LOCK_TIMEOUT_MS;
	while (true) {
		try {
			// O_CREAT|O_EXCL: fails with EEXIST if lock file already exists.
			const fd = openSync(lockPath, "wx");
			closeSync(fd);
			return () => {
				try {
					rmSync(lockPath, { force: true });
				} catch {
					// best-effort release
				}
			};
		} catch (err) {
			const code = (err as NodeJS.ErrnoException).code;
			if (code !== "EEXIST") throw err;
			if (Date.now() > deadline) {
				throw new Error(
					`Could not acquire audit lock at ${lockPath} within ${LOCK_TIMEOUT_MS}ms`,
				);
			}
			// Stale-lock stealing: if the lock file's mtime is older than the
			// stale threshold, the owning process has crashed. Delete it so the
			// next iteration can reclaim the lock.
			try {
				const st = statSync(lockPath);
				if (Date.now() - st.mtimeMs > LOCK_STALE_MS) {
					rmSync(lockPath, { force: true });
					// Loop immediately to retry O_EXCL without sleeping.
					continue;
				}
			} catch {
				// Lock file disappeared between EEXIST and statSync — that is
				// fine; the next O_EXCL attempt will succeed.
				continue;
			}
			// Synchronous busy-wait: audit path is off the hot write path.
			const start = Date.now();
			while (Date.now() - start < LOCK_POLL_MS) {
				// spin
			}
		}
	}
}

/**
 * Extract the `hmac` field from the last non-empty line of a JSONL file.
 * Returns ZERO_HASH if the file is empty or does not exist.
 */
function readLastHmac(jsonlPath: string): string {
	if (!existsSync(jsonlPath)) return ZERO_HASH;
	const content = readFileSync(jsonlPath, "utf8");
	const lines = content.split("\n").filter((l) => l.trim().length > 0);
	if (lines.length === 0) return ZERO_HASH;
	const last = lines[lines.length - 1];
	try {
		const parsed = JSON.parse(last ?? "") as Record<string, unknown>;
		const h = parsed.hmac;
		if (typeof h === "string" && h.length === 64) return h;
	} catch {
		// malformed last line — treat as no prev
	}
	return ZERO_HASH;
}

/**
 * Compute HMAC-SHA256(key, prevHash + serializedRecord).
 * `serializedRecord` must NOT contain the `hmac` field.
 */
function computeHmac(
	key: Buffer,
	prevHash: string,
	serializedRecord: string,
): string {
	return createHmac("sha256", key)
		.update(prevHash + serializedRecord)
		.digest("hex");
}

/**
 * Append one tamper-evident audit record to today's JSONL log.
 *
 * @param stateDir  vault.stateDir (e.g. /vault/.mneme)
 * @param relativePath  vault-relative file path, forward slashes
 * @param redactionsApplied  total redaction count for this write
 * @returns true on success, false on non-fatal failure
 */
export function appendAuditRecord(
	stateDir: string,
	relativePath: string,
	redactionsApplied: number,
): boolean {
	try {
		const auditDir = join(stateDir, "audit");
		mkdirSync(auditDir, { recursive: true });

		const keyPath = join(stateDir, "audit-hmac.key");
		const key = loadOrCreateKey(keyPath);

		// toISOString() is always UTC, so the daily filename uses the UTC
		// calendar date by design — files are named YYYY-MM-DD in UTC.
		const today = new Date().toISOString().slice(0, 10);
		const jsonlPath = join(auditDir, `${today}.jsonl`);
		const lockPath = join(auditDir, `${today}.lock`);

		const release = acquireLock(lockPath);
		try {
			// CORRECTNESS INVARIANT: readLastHmac and the subsequent
			// atomicWriteText must both execute inside the acquireLock guard.
			// Moving readLastHmac outside the lock would be a bug: another
			// writer could append between the read and the write, producing a
			// chain break (duplicate or skipped prev_hash).
			const prevHash = readLastHmac(jsonlPath);

			// Build the record without the hmac field first.
			const recordWithoutHmac = {
				timestamp_iso: new Date().toISOString(),
				relative_path: relativePath,
				redactions_applied: redactionsApplied,
				prev_hash: prevHash,
			};

			const serialized = JSON.stringify(recordWithoutHmac);
			const hmac = computeHmac(key, prevHash, serialized);

			const fullRecord: AuditRecord = { ...recordWithoutHmac, hmac };
			const line = `${JSON.stringify(fullRecord)}\n`;

			// Atomic append: read existing content, append line, atomic write.
			const existing = existsSync(jsonlPath)
				? readFileSync(jsonlPath, "utf8")
				: "";
			atomicWriteText(jsonlPath, existing + line);
		} finally {
			release();
		}

		return true;
	} catch (err) {
		// Non-fatal: log warning but never block the write result.
		console.warn(
			`[mneme audit] Failed to append audit record: ${err instanceof Error ? err.message : String(err)}`,
		);
		return false;
	}
}
