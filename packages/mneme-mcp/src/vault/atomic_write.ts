/**
 * Cross-platform atomic file write plus vault-root containment check.
 *
 * Mirrors `mneme_core.vault.atomic_write`. Two load-bearing guarantees:
 *
 *   1. `assertWithinVault` blocks any path that resolves outside the
 *      vault root, including paths that reach outside through symlinks
 *      anywhere in the parent chain. Both the root and the deepest
 *      existing ancestor of the target are realpath-resolved before
 *      the prefix check. This is the v1.0 path-traversal defense.
 *      Never relax this check.
 *
 *   2. Writes go to a sibling temp file in the same directory, fsync,
 *      then `renameSync` over the target. On Windows `rename` over an
 *      existing file fails. The Codex Pass 1 review found a deletion
 *      window where the original file is lost if the second rename
 *      fails after the pre-delete. The fix preserves the original
 *      file by moving it to a sibling backup first; the backup is
 *      dropped on success and restored on failure. POSIX `rename` is
 *      atomic-by-spec when source and destination live on the same
 *      filesystem.
 *
 * The temp file is removed on any failure so partial writes never
 * leak into the vault.
 */

import { randomBytes } from "node:crypto";
import {
	closeSync,
	constants,
	fsyncSync,
	lstatSync,
	mkdirSync,
	openSync,
	realpathSync,
	renameSync,
	rmSync,
	statSync,
	writeSync,
} from "node:fs";
import { dirname, resolve as resolvePath, sep } from "node:path";

export class VaultPathError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "VaultPathError";
	}
}

export interface AtomicWriteOptions {
	/** Revalidate containment immediately before every filesystem mutation. */
	vaultRoot?: string;
}

interface DirectoryIdentity {
	dev: number;
	ino: number;
	realpath: string;
}

function directoryIdentity(path: string): DirectoryIdentity {
	const stat = statSync(path);
	if (!stat.isDirectory()) {
		throw new VaultPathError(
			`Atomic-write parent "${path}" is not a directory.`,
		);
	}
	return { dev: stat.dev, ino: stat.ino, realpath: realpathSync(path) };
}

function assertDirectoryIdentity(
	path: string,
	expected: DirectoryIdentity,
	vaultRoot?: string,
): void {
	if (vaultRoot !== undefined) {
		assertWithinVault(vaultRoot, path);
	}
	const actual = directoryIdentity(path);
	if (
		actual.dev !== expected.dev ||
		actual.ino !== expected.ino ||
		actual.realpath !== expected.realpath
	) {
		throw new VaultPathError(
			`Atomic-write parent "${path}" changed during the operation. Operation refused.`,
		);
	}
}

/**
 * Resolve the deepest existing ancestor of `p`, returning its realpath.
 *
 * Used by `assertWithinVault` so a write target whose final segment
 * does not exist yet (the common case for newly created files) still
 * has its parent chain resolved through any symlinks. If no ancestor
 * exists, falls back to the absolute path.
 */
function deepestExistingRealpath(p: string): string {
	let cur = resolvePath(p);
	// Safety cap: filesystem paths never approach this depth, but cap
	// the loop so a bug cannot spin forever.
	for (let i = 0; i < 4096; i++) {
		try {
			return realpathSync(cur);
		} catch {
			const parent = dirname(cur);
			if (parent === cur) {
				return cur;
			}
			cur = parent;
		}
	}
	return cur;
}

/**
 * Throws `VaultPathError` if `targetPath` escapes `vaultRoot`.
 *
 * Codex Pass 1 review identified a symlink bypass: prefix-comparing
 * resolved (lexical) paths does not follow symlinks, so a directory
 * inside the vault that links to a path outside the vault would let
 * writes escape. Fix: realpath the vault root and the deepest
 * existing ancestor of the target before the prefix check. If the
 * final segment of the target is itself a pre-existing symlink, also
 * require that the symlink's realpath stays inside the vault.
 */
export function assertWithinVault(vaultRoot: string, targetPath: string): void {
	const root = resolvePath(vaultRoot);
	const target = resolvePath(targetPath);
	let rootReal: string;
	try {
		rootReal = realpathSync(root);
	} catch {
		rootReal = root;
	}
	const targetReal = deepestExistingRealpath(target);
	const rootWithSep = rootReal.endsWith(sep) ? rootReal : rootReal + sep;
	if (targetReal !== rootReal && !targetReal.startsWith(rootWithSep)) {
		throw new VaultPathError(
			`Path "${target}" resolves to "${targetReal}", outside vault root "${rootReal}". Operation refused.`,
		);
	}
	// Defense in depth: if the final segment already exists as a
	// symlink, also assert its realpath stays inside. The walk above
	// covers the parent chain; this covers the file-itself case.
	try {
		const st = lstatSync(target);
		if (st.isSymbolicLink()) {
			const linkReal = realpathSync(target);
			if (linkReal !== rootReal && !linkReal.startsWith(rootWithSep)) {
				throw new VaultPathError(
					`Path "${target}" is a symlink to "${linkReal}", outside vault root "${rootReal}". Operation refused.`,
				);
			}
		}
	} catch (err) {
		if (err instanceof VaultPathError) throw err;
		// ENOENT or other access errors: parent-chain check above is
		// authoritative; nothing further to assert about a non-existing
		// final segment.
	}
}

/**
 * Atomically write `content` to `targetPath` using a sibling temp file.
 * Creates parent directories as needed. Encoding is utf8.
 */
/**
 * Remove a file, retrying on transient Windows file-lock errors (EPERM/EBUSY).
 * Throws after all attempts are exhausted, allowing callers to surface the
 * failure rather than silently swallowing it.
 */
function rmSyncWithRetry(path: string, maxAttempts = 3, delayMs = 50): void {
	for (let i = 0; i < maxAttempts; i++) {
		try {
			rmSync(path, { force: true });
			return;
		} catch (err) {
			const code = (err as NodeJS.ErrnoException).code;
			if ((code === "EPERM" || code === "EBUSY") && i < maxAttempts - 1) {
				// Brief spin-wait for Windows file-lock to release.
				const end = Date.now() + delayMs;
				while (Date.now() < end) {
					/* spin */
				}
				continue;
			}
			throw err;
		}
	}
}

export function atomicWriteText(
	targetPath: string,
	content: string,
	options: AtomicWriteOptions = {},
): void {
	const target = resolvePath(targetPath);
	const parent = dirname(target);
	if (options.vaultRoot !== undefined) {
		// Check before mkdir so an escaping path cannot create directories as a
		// side effect, then check again after mkdir resolves the full parent chain.
		assertWithinVault(options.vaultRoot, target);
	}
	mkdirSync(parent, { recursive: true });
	if (options.vaultRoot !== undefined) {
		assertWithinVault(options.vaultRoot, target);
	}
	const parentIdentity = directoryIdentity(parent);

	const nonce = randomBytes(16).toString("hex");
	const tmpPath = `${target}.tmp-${process.pid}-${nonce}`;
	let fd: number | null = null;
	// Track whether the write was committed so cleanup failures on the
	// success path are surfaced rather than silently swallowed (S5).
	let writeOk = false;
	try {
		const noFollow = constants.O_NOFOLLOW ?? 0;
		fd = openSync(
			tmpPath,
			constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | noFollow,
			0o600,
		);
		// The temp file is still empty here. If a parent symlink or reparse
		// point was swapped during open, refuse before writing caller content.
		assertDirectoryIdentity(parent, parentIdentity, options.vaultRoot);
		writeSync(fd, content, 0, "utf8");
		fsyncSync(fd);
		closeSync(fd);
		fd = null;
		assertDirectoryIdentity(parent, parentIdentity, options.vaultRoot);
		try {
			renameSync(tmpPath, target);
			writeOk = true; // POSIX atomic rename succeeded
		} catch (err) {
			// Phase J post-Codex-review fix: never delete the target as part of
			// an atomic-write fallback. Pre-delete leaves a window where the
			// original file is gone if the second rename fails or the process
			// crashes. Preserve the target by moving it to a sibling backup
			// first; if the rename succeeds, unlink the backup; if it fails,
			// restore the backup before raising.
			const code = (err as NodeJS.ErrnoException).code;
			if (code === "EEXIST" || code === "EPERM" || code === "EACCES") {
				const backupPath = `${target}.bak-${process.pid}-${nonce}`;
				try {
					assertDirectoryIdentity(parent, parentIdentity, options.vaultRoot);
					renameSync(target, backupPath);
				} catch {
					// Target may have vanished between rename failure and backup;
					// proceed to the second rename attempt without a backup to
					// preserve the original-behavior compatibility for that edge.
				}
				try {
					assertDirectoryIdentity(parent, parentIdentity, options.vaultRoot);
					renameSync(tmpPath, target);
					writeOk = true; // Windows fallback rename succeeded
				} catch (err2) {
					// Restore the backup so the caller's original file survives.
					try {
						renameSync(backupPath, target);
					} catch {
						// If even the restore fails, both files may still be on
						// disk; surface the original failure for caller diagnosis.
					}
					throw err2;
				}
				// Write committed; cleanup backup and surface persistent failures.
				rmSyncWithRetry(backupPath);
			} else {
				throw err;
			}
		}
	} finally {
		if (fd !== null) {
			try {
				closeSync(fd);
			} catch {
				// best-effort
			}
		}
		// Remove the temp file (no-op when already renamed on success).
		// Surface the cleanup error only when the write was committed — a
		// cleanup failure after a successful write is a data-integrity concern.
		try {
			rmSyncWithRetry(tmpPath);
		} catch (cleanupErr) {
			// biome-ignore lint/correctness/noUnsafeFinally: throws only when writeOk is true (write committed, no prior exception propagating)
			if (writeOk) throw cleanupErr;
		}
	}
}
