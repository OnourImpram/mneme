/**
 * Cross-language tamper-evident HMAC audit chain.
 *
 * Python and TypeScript writers share the key, daily JSONL file, HMAC rule,
 * and O_EXCL lock. Both writers carry an explicit sequence and advance the
 * same keyed daily seal. Legacy sequence-free records remain readable, but
 * every new append seals the complete chain so tail deletion is detectable.
 *
 * Audit failures remain non-fatal at this compatibility boundary. Callers
 * receive false and can surface the accountability failure to the operator.
 * No network or LLM calls are made by this module.
 */

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import {
	closeSync,
	existsSync,
	fsyncSync,
	mkdirSync,
	openSync,
	readFileSync,
	rmSync,
	statSync,
	writeSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { performance } from "node:perf_hooks";
import { atomicWriteText } from "./vault/atomic_write.js";

const KEY_BYTES = 32;
const ZERO_HASH = "0".repeat(64);
const KEY_FILENAME = "audit-hmac.key";
const SEAL_VERSION = 1;
const LOCK_POLL_MS = 10;
const LOCK_TIMEOUT_MS = 5_000;
const LOCK_STALE_MS = 10_000;
const KEY_READ_TIMEOUT_MS = 500;
const SEAL_DOMAIN = Buffer.from("mneme-audit-seal-v1\0", "utf8");
const LOWER_HEX_HMAC = /^[0-9a-f]{64}$/;
const SLEEP_BUFFER = new Int32Array(new SharedArrayBuffer(4));

export interface AuditRecord {
	timestamp_iso: string;
	sequence: number;
	relative_path: string;
	redactions_applied: number;
	prev_hash: string;
	hmac: string;
}

interface ChainScan {
	records: number;
	heads: string[];
	explicitSequenceSeen: boolean;
	content: string;
}

function sleepSync(milliseconds: number): void {
	Atomics.wait(SLEEP_BUFFER, 0, 0, milliseconds);
}

function errorCode(error: unknown): string | undefined {
	return (error as NodeJS.ErrnoException).code;
}

function isContentionError(error: unknown): boolean {
	const code = errorCode(error);
	return code === "EEXIST" || code === "EACCES" || code === "EPERM";
}

function isRetryableKeyReadError(error: unknown): boolean {
	const code = errorCode(error);
	return code === "ENOENT" || code === "EACCES" || code === "EPERM";
}

function writeAll(fd: number, content: Buffer): void {
	let offset = 0;
	while (offset < content.length) {
		const written = writeSync(fd, content, offset, content.length - offset);
		if (written <= 0) {
			throw new Error("audit file write made no progress");
		}
		offset += written;
	}
}

function readKey(keyPath: string): Buffer {
	const deadline = performance.now() + KEY_READ_TIMEOUT_MS;
	let observedLength: number | undefined;
	let lastReadError: unknown;

	while (true) {
		try {
			const key = readFileSync(keyPath);
			observedLength = key.length;
			if (key.length === KEY_BYTES) return key;
			lastReadError = undefined;
		} catch (error) {
			if (!isRetryableKeyReadError(error)) throw error;
			lastReadError = error;
		}

		if (performance.now() >= deadline) {
			if (observedLength !== undefined) {
				throw new Error(
					`${KEY_FILENAME} is ${observedLength} bytes; expected ${KEY_BYTES}. ` +
						`Delete ${keyPath} to regenerate.`,
				);
			}
			throw lastReadError instanceof Error
				? lastReadError
				: new Error(`Unable to read ${KEY_FILENAME}.`);
		}
		sleepSync(LOCK_POLL_MS);
	}
}

/** Load the shared key, creating it exclusively with mode 0o600. */
function loadOrCreateKey(stateDir: string): Buffer {
	const keyPath = join(stateDir, KEY_FILENAME);
	if (existsSync(keyPath)) return readKey(keyPath);

	mkdirSync(stateDir, { recursive: true });
	const key = randomBytes(KEY_BYTES);
	let fd: number;
	try {
		fd = openSync(keyPath, "ax", 0o600);
	} catch (error) {
		if (!isContentionError(error)) throw error;
		return readKey(keyPath);
	}

	try {
		writeAll(fd, key);
		fsyncSync(fd);
	} finally {
		closeSync(fd);
	}
	return key;
}

function buffersEqual(left: Buffer, right: Buffer): boolean {
	return left.length === right.length && timingSafeEqual(left, right);
}

function stringsEqual(left: string, right: string): boolean {
	return buffersEqual(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function removeOwnedLock(lockPath: string, token: Buffer): void {
	try {
		const current = readFileSync(lockPath);
		if (buffersEqual(current, token)) {
			rmSync(lockPath, { force: true });
		}
	} catch {
		// The lock disappeared or became unreadable. Never remove it blindly.
	}
}

/** Acquire the daily O_EXCL lock shared with the Python writer. */
function acquireLock(lockPath: string): () => void {
	mkdirSync(dirname(lockPath), { recursive: true });
	const deadline = performance.now() + LOCK_TIMEOUT_MS;
	const token = Buffer.from(
		`${process.pid}:${randomBytes(8).toString("hex")}`,
		"ascii",
	);

	while (true) {
		let fd: number | undefined;
		try {
			fd = openSync(lockPath, "wx", 0o600);
			try {
				writeAll(fd, token);
			} catch (error) {
				closeSync(fd);
				fd = undefined;
				rmSync(lockPath, { force: true });
				throw error;
			}
			closeSync(fd);
			return () => removeOwnedLock(lockPath, token);
		} catch (error) {
			if (fd !== undefined) {
				try {
					closeSync(fd);
				} catch {
					// Preserve the acquisition error.
				}
			}
			if (!isContentionError(error)) throw error;

			// Check the deadline before deciding to retry, not only on the
			// contended path. Three of the retries below used to skip this
			// check and loop straight back into openSync, so a lock that another
			// process kept creating and deleting could spin this loop without
			// bound and without ever reaching its own timeout.
			if (performance.now() >= deadline) {
				throw new Error(
					`Could not acquire audit lock at ${lockPath} within ${LOCK_TIMEOUT_MS}ms`,
				);
			}

			if (existsSync(lockPath)) {
				try {
					const stat = statSync(lockPath);
					if (Date.now() - stat.mtimeMs > LOCK_STALE_MS) {
						rmSync(lockPath, { force: true });
					}
				} catch (statError) {
					if (errorCode(statError) !== "ENOENT") {
						// Access errors are contention on Windows. Wait until timeout.
					}
				}
			}

			// Sleep on every retry, including the ones that follow a vanished or
			// cleared lock. Those paths used to retry with no delay at all, which
			// on a loaded machine burns a core until the deadline and starves the
			// very process holding the lock. LOCK_POLL_MS is 10 ms, so paying it
			// on the uncontended path costs nothing measurable.
			sleepSync(LOCK_POLL_MS);
		}
	}
}

function computeHmac(
	key: Buffer,
	prevHash: string,
	serializedRecord: string,
): string {
	return createHmac("sha256", key)
		.update(prevHash + serializedRecord)
		.digest("hex");
}

function computeSealHmac(key: Buffer, serializedSeal: string): string {
	return createHmac("sha256", key)
		.update(SEAL_DOMAIN)
		.update(serializedSeal)
		.digest("hex");
}

/** Validate every persisted record using Python's canonical scan contract. */
function scanChain(jsonlPath: string, key: Buffer): ChainScan {
	if (!existsSync(jsonlPath)) {
		return {
			records: 0,
			heads: [],
			explicitSequenceSeen: false,
			content: "",
		};
	}

	const content = readFileSync(jsonlPath, "utf8");
	const heads: string[] = [];
	let previous = ZERO_HASH;
	let records = 0;
	let explicitSequenceSeen = false;

	for (const [index, rawLine] of content.split(/\r\n|\n|\r/).entries()) {
		const raw = rawLine.trim();
		if (!raw) continue;
		records += 1;

		let parsed: unknown;
		try {
			parsed = JSON.parse(raw);
		} catch {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: unparseable record`,
			);
		}
		if (
			parsed === null ||
			typeof parsed !== "object" ||
			Array.isArray(parsed)
		) {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: record is not an object`,
			);
		}
		const record = parsed as Record<string, unknown>;

		const sequence = record.sequence;
		if (sequence !== undefined && sequence !== null) {
			explicitSequenceSeen = true;
			if (!Number.isInteger(sequence) || sequence !== records) {
				throw new Error(
					`existing audit chain is invalid at line ${index + 1}: sequence mismatch`,
				);
			}
		}

		const recordedHmac = record.hmac;
		if (
			typeof recordedHmac !== "string" ||
			!LOWER_HEX_HMAC.test(recordedHmac)
		) {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: invalid hmac`,
			);
		}
		if (record.prev_hash !== previous) {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: prev_hash mismatch`,
			);
		}

		const marker = `,"hmac":${JSON.stringify(recordedHmac)}`;
		const canonicalTail = `${marker}}`;
		if (!raw.endsWith(canonicalTail)) {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: hmac field not in canonical position`,
			);
		}
		const serialized = `${raw.slice(0, -canonicalTail.length)}}`;
		const expected = computeHmac(key, previous, serialized);
		if (!stringsEqual(expected, recordedHmac)) {
			throw new Error(
				`existing audit chain is invalid at line ${index + 1}: hmac mismatch`,
			);
		}

		previous = recordedHmac;
		heads.push(recordedHmac);
	}

	return { records, heads, explicitSequenceSeen, content };
}

/** Verify the shared cross-language daily seal. */
function verifySeal(
	sealPath: string,
	day: string,
	key: Buffer,
	chain: ChainScan,
): void {
	let sealIsFile = false;
	if (existsSync(sealPath)) {
		sealIsFile = statSync(sealPath).isFile();
	}
	if (!sealIsFile) {
		if (chain.explicitSequenceSeen) {
			throw new Error(
				"existing audit seal is invalid: seal missing for Python-sequenced audit chain",
			);
		}
		return;
	}

	let parsed: unknown;
	try {
		parsed = JSON.parse(readFileSync(sealPath, "utf8"));
	} catch (error) {
		throw new Error(
			`existing audit seal is invalid: seal unreadable: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("existing audit seal is invalid: seal is not an object");
	}
	const seal = parsed as Record<string, unknown>;
	const version = seal.version;
	const sealDay = seal.day;
	const sequence = seal.sequence;
	const headHmac = seal.head_hmac;
	const sealedAt = seal.sealed_at;
	const sealHmac = seal.seal_hmac;

	if (version !== SEAL_VERSION || sealDay !== day) {
		throw new Error("existing audit seal is invalid: seal metadata mismatch");
	}
	if (!Number.isInteger(sequence) || (sequence as number) < 1) {
		throw new Error("existing audit seal is invalid: seal sequence is invalid");
	}
	if (typeof headHmac !== "string" || headHmac.length !== 64) {
		throw new Error("existing audit seal is invalid: seal head is invalid");
	}
	if (typeof sealedAt !== "string" || typeof sealHmac !== "string") {
		throw new Error("existing audit seal is invalid: seal fields are invalid");
	}

	const body = {
		version,
		day: sealDay,
		sequence,
		head_hmac: headHmac,
		sealed_at: sealedAt,
	};
	const expected = computeSealHmac(key, JSON.stringify(body));
	if (!stringsEqual(expected, sealHmac)) {
		throw new Error("existing audit seal is invalid: seal hmac mismatch");
	}

	const sealedSequence = sequence as number;
	if (sealedSequence > chain.heads.length) {
		throw new Error(
			`existing audit seal is invalid: tail truncation detected: seal requires ${sealedSequence} records, chain has ${chain.heads.length}`,
		);
	}
	if (chain.heads[sealedSequence - 1] !== headHmac) {
		throw new Error("existing audit seal is invalid: sealed head mismatch");
	}
}

function writeSeal(
	sealPath: string,
	stateDir: string,
	day: string,
	sequence: number,
	headHmac: string,
	key: Buffer,
): void {
	const body = {
		version: SEAL_VERSION,
		day,
		sequence,
		head_hmac: headHmac,
		sealed_at: new Date().toISOString(),
	};
	const sealHmac = computeSealHmac(key, JSON.stringify(body));
	atomicWriteText(
		sealPath,
		`${JSON.stringify({ ...body, seal_hmac: sealHmac })}\n`,
		{ vaultRoot: stateDir },
	);
}

function restoreSnapshot(
	path: string,
	stateDir: string,
	existed: boolean,
	content: string,
): void {
	if (existed) {
		atomicWriteText(path, content, { vaultRoot: stateDir });
		return;
	}
	rmSync(path, { force: true });
}

/**
 * Append one TypeScript-shaped record and advance today's verified seal.
 */
export function appendAuditRecord(
	stateDir: string,
	relativePath: string,
	redactionsApplied: number,
): boolean {
	try {
		const auditDir = join(stateDir, "audit");
		mkdirSync(auditDir, { recursive: true });
		const key = loadOrCreateKey(stateDir);
		const day = new Date().toISOString().slice(0, 10);
		const jsonlPath = join(auditDir, `${day}.jsonl`);
		const sealPath = join(auditDir, `${day}.seal.json`);
		const lockPath = join(auditDir, `${day}.lock`);

		const release = acquireLock(lockPath);
		try {
			const chain = scanChain(jsonlPath, key);
			verifySeal(sealPath, day, key, chain);
			const sequence = chain.records + 1;
			const prevHash = chain.heads.at(-1) ?? ZERO_HASH;
			const recordWithoutHmac = {
				timestamp_iso: new Date().toISOString(),
				sequence,
				relative_path: relativePath,
				redactions_applied: redactionsApplied,
				prev_hash: prevHash,
			};
			const serialized = JSON.stringify(recordWithoutHmac);
			const hmac = computeHmac(key, prevHash, serialized);
			const fullRecord: AuditRecord = { ...recordWithoutHmac, hmac };
			const separator =
				chain.content.length === 0 || /[\r\n]$/.test(chain.content) ? "" : "\n";
			const chainExisted = existsSync(jsonlPath);
			const sealExisted = existsSync(sealPath);
			const existingSeal = sealExisted ? readFileSync(sealPath, "utf8") : "";
			try {
				atomicWriteText(
					jsonlPath,
					`${chain.content}${separator}${JSON.stringify(fullRecord)}\n`,
					{ vaultRoot: stateDir },
				);
				writeSeal(sealPath, stateDir, day, sequence, hmac, key);
			} catch (appendError) {
				try {
					restoreSnapshot(jsonlPath, stateDir, chainExisted, chain.content);
					restoreSnapshot(sealPath, stateDir, sealExisted, existingSeal);
				} catch (restoreError) {
					throw new Error(
						`audit append failed and snapshot restoration failed: ${restoreError instanceof Error ? restoreError.message : String(restoreError)}`,
						{ cause: appendError },
					);
				}
				throw appendError;
			}
		} finally {
			release();
		}
		return true;
	} catch (error) {
		console.warn(
			`[mneme audit] Failed to append audit record: ${error instanceof Error ? error.message : String(error)}`,
		);
		return false;
	}
}
