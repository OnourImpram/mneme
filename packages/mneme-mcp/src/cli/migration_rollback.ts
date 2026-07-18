/** Transactional migration staging and fail-closed rollback support. */

import {
	createHash,
	createHmac,
	randomBytes,
	timingSafeEqual,
} from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fstatSync,
	fsyncSync,
	linkSync,
	lstatSync,
	mkdirSync,
	openSync,
	readdirSync,
	readFileSync,
	readSync,
	realpathSync,
	renameSync,
	unlinkSync,
	writeSync,
} from "node:fs";
import {
	basename,
	dirname,
	join,
	relative,
	resolve as resolvePath,
	sep,
} from "node:path";
import { performance } from "node:perf_hooks";
import { redact } from "../privacy.js";
import { assertWithinVault, atomicWriteText } from "../vault/atomic_write.js";
import type { VaultConfig } from "../vault/config.js";

const MANIFEST_SCHEMA = "mneme-migration-rollback/2";
const KEY_BYTES = 32;
const KEY_FILENAME = "migration-integrity.key";
const MAX_MANIFEST_BYTES = 16 * 1024 * 1024;
const MAX_SNAPSHOT_FILES = 100_000;
const MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024;
const LOCK_TIMEOUT_MS = 5_000;
const LOCK_POLL_MS = 20;
const MALFORMED_LOCK_STALE_MS = 10 * 60 * 1_000;
const RUN_ID_RE = /^[0-9TZ-]+-[0-9a-f]{16}$/;
const LOWER_SHA256 = /^[0-9a-f]{64}$/;
const EXPORT_SEGMENTS = ["imported", "claude-mem"] as const;
const SLEEP_BUFFER = new Int32Array(new SharedArrayBuffer(4));

export type MigrationArchiveMode = "preserve" | "copy" | "move";
type ManifestStatus =
	| "prepared"
	| "commit-ready"
	| "committing"
	| "applied"
	| "rollback-swapping"
	| "rolled-back"
	| "aborted";

interface FileFingerprint {
	path: string;
	sha256: string;
	size: number;
}

interface RollbackManifest {
	schema: typeof MANIFEST_SCHEMA;
	run_id: string;
	status: ManifestStatus;
	vault_root: string;
	export_root: string;
	staging_root: string;
	quarantine_root: string;
	snapshot_root: string;
	restore_root: string;
	rollback_after_root: string;
	source_db: string;
	source_quarantine_path: string;
	source_sha256: string | null;
	archive_mode: MigrationArchiveMode;
	archive_path: string | null;
	source_moved: boolean;
	before_root_existed: boolean;
	after_root_existed: boolean | null;
	before_files: FileFingerprint[];
	after_files: FileFingerprint[] | null;
	prepared_at: string;
	applied_at: string | null;
	rolled_back_at: string | null;
	abort_reason: string | null;
	integrity_hmac: string;
}

interface DirectoryIdentity {
	dev: number;
	ino: number;
	realpath: string;
}

interface TreeFingerprint {
	exists: boolean;
	files: FileFingerprint[];
}

export interface MigrationRollbackHandle {
	manifestPath: string;
	stagingRoot: string;
	exportRoot: string;
}

export interface MigrationRollbackResult {
	status: "ok" | "error";
	manifestPath: string;
	restoredFiles: number;
	sourceRestored: boolean;
	alreadyRolledBack: boolean;
	recoveredInterruptedRun: boolean;
	errors: string[];
}

export interface RollbackMigrationOptions {
	vault: VaultConfig;
	manifestPath: string;
	confirmSourceRestore?: boolean;
	sourceRestorePath?: string;
	/** Test-only crash injection. Production callers must omit this. */
	faultAt?: "after-rollback-live-quarantine" | "after-rollback-restore-publish";
}

type CommitFaultPoint =
	| "after-source-quarantine"
	| "after-live-quarantine"
	| "after-stage-publish";

function errorMessage(error: unknown): string {
	const raw = error instanceof Error ? error.message : String(error);
	return redact(raw)
		.text.replace(/\b([a-z][a-z0-9+.-]*:\/\/)([^@\s/]+)@/gi, "$1[REDACTED]@")
		.slice(0, 4096);
}

function sanitizeManifestDiagnostic(
	manifest: RollbackManifest,
	value: string,
): string {
	let sanitized = errorMessage(value);
	const paths = [
		manifest.source_quarantine_path,
		manifest.rollback_after_root,
		manifest.quarantine_root,
		manifest.snapshot_root,
		manifest.restore_root,
		manifest.staging_root,
		manifest.export_root,
		manifest.archive_path ?? "",
		manifest.source_db,
		manifest.vault_root,
	].sort((left, right) => right.length - left.length);
	for (const path of paths) {
		if (path.length > 0) sanitized = sanitized.split(path).join("[PATH]");
	}
	// Canonical source paths can have a different stable lexical alias, such as
	// macOS /var and /private/var. Redact remaining absolute path tokens without
	// matching URL path components.
	sanitized = sanitized
		.replace(/\b[A-Za-z]:[\\/][^\s"'<>]*/g, "[PATH]")
		.replace(/(^|[\s"'(])\/(?!\/)[^\s"'<>]*/g, "$1[PATH]");
	return sanitized.slice(0, 4096);
}

function sleepSync(milliseconds: number): void {
	Atomics.wait(SLEEP_BUFFER, 0, 0, milliseconds);
}

function injectFault(expected: string | undefined, actual: string): void {
	if (expected === actual)
		throw new Error(`simulated process crash at ${actual}`);
}

function errorCode(error: unknown): string | undefined {
	return (error as NodeJS.ErrnoException).code;
}

function comparePortablePaths(left: string, right: string): number {
	if (left === right) return 0;
	return left < right ? -1 : 1;
}

function sameResolvedPath(left: string, right: string): boolean {
	const a = resolvePath(left);
	const b = resolvePath(right);
	return process.platform === "win32"
		? a.toLowerCase() === b.toLowerCase()
		: a === b;
}

function buffersEqual(left: Buffer, right: Buffer): boolean {
	return left.length === right.length && timingSafeEqual(left, right);
}

function writeAll(fd: number, content: Buffer): void {
	let offset = 0;
	while (offset < content.length) {
		const written = writeSync(fd, content, offset, content.length - offset);
		if (written <= 0) throw new Error("filesystem write made no progress");
		offset += written;
	}
}

function readBoundedRegularFile(path: string, maxBytes: number): Buffer {
	const before = lstatSync(path);
	if (!before.isFile() || before.isSymbolicLink() || before.size > maxBytes) {
		throw new Error("path is not a bounded regular file");
	}
	const noFollow = constants.O_NOFOLLOW ?? 0;
	const fd = openSync(path, constants.O_RDONLY | noFollow);
	try {
		const opened = fstatSync(fd);
		if (
			!opened.isFile() ||
			opened.dev !== before.dev ||
			opened.ino !== before.ino ||
			opened.size !== before.size
		) {
			throw new Error("regular file changed before it was opened");
		}
		const content = readFileSync(fd);
		if (content.length > maxBytes)
			throw new Error("regular file exceeds limit");
		const after = fstatSync(fd);
		const afterPath = lstatSync(path);
		if (
			after.dev !== opened.dev ||
			after.ino !== opened.ino ||
			after.size !== opened.size ||
			after.mtimeMs !== opened.mtimeMs ||
			after.ctimeMs !== opened.ctimeMs ||
			afterPath.dev !== opened.dev ||
			afterPath.ino !== opened.ino ||
			afterPath.isSymbolicLink()
		) {
			throw new Error("regular file changed while it was read");
		}
		return content;
	} finally {
		closeSync(fd);
	}
}

function hashRegularFile(
	path: string,
	maxBytes: number,
	vaultRoot?: string,
): { sha256: string; size: number } {
	if (vaultRoot !== undefined) assertWithinVault(vaultRoot, path);
	const before = lstatSync(path);
	if (!before.isFile() || before.isSymbolicLink() || before.size > maxBytes) {
		throw new Error("path is not a bounded regular file");
	}
	const fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
	const hash = createHash("sha256");
	let bytes = 0;
	try {
		const opened = fstatSync(fd);
		if (
			!opened.isFile() ||
			opened.dev !== before.dev ||
			opened.ino !== before.ino ||
			opened.size !== before.size
		) {
			throw new Error("regular file changed before hashing");
		}
		const chunk = Buffer.allocUnsafe(64 * 1024);
		while (true) {
			const count = readSync(fd, chunk, 0, chunk.length, null);
			if (count === 0) break;
			hash.update(chunk.subarray(0, count));
			bytes += count;
		}
		const after = fstatSync(fd);
		const afterPath = lstatSync(path);
		if (
			after.dev !== opened.dev ||
			after.ino !== opened.ino ||
			after.size !== opened.size ||
			after.mtimeMs !== opened.mtimeMs ||
			after.ctimeMs !== opened.ctimeMs ||
			afterPath.dev !== opened.dev ||
			afterPath.ino !== opened.ino ||
			afterPath.isSymbolicLink() ||
			bytes !== opened.size
		) {
			throw new Error("regular file changed while hashing");
		}
		return { sha256: hash.digest("hex"), size: bytes };
	} finally {
		closeSync(fd);
	}
}

function copyToExternalPathNoClobber(
	source: string,
	target: string,
	expectedSha256: string,
	vaultRoot: string,
): void {
	assertWithinVault(vaultRoot, source);
	const sourceBefore = hashRegularFile(source, MAX_SNAPSHOT_BYTES, vaultRoot);
	if (sourceBefore.sha256 !== expectedSha256) {
		throw new Error(
			"archived source hash does not match the rollback manifest",
		);
	}
	const ancestors = externalAncestorIdentities(dirname(target));
	const temporary = join(
		dirname(target),
		`.${basename(target)}.mneme-restore-${randomBytes(16).toString("hex")}`,
	);
	const sourceFd = openSync(
		source,
		constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
	);
	let temporaryFd: number | null = null;
	try {
		temporaryFd = openSync(
			temporary,
			constants.O_WRONLY |
				constants.O_CREAT |
				constants.O_EXCL |
				(constants.O_NOFOLLOW ?? 0),
			0o600,
		);
		const chunk = Buffer.allocUnsafe(64 * 1024);
		while (true) {
			const count = readSync(sourceFd, chunk, 0, chunk.length, null);
			if (count === 0) break;
			writeAll(temporaryFd, chunk.subarray(0, count));
		}
		fsyncSync(temporaryFd);
	} finally {
		closeSync(sourceFd);
		if (temporaryFd !== null) closeSync(temporaryFd);
	}
	const copied = hashRegularFile(temporary, MAX_SNAPSHOT_BYTES);
	if (copied.sha256 !== expectedSha256 || copied.size !== sourceBefore.size) {
		throw new Error("restored source hash verification failed");
	}
	const temporaryIdentity = lstatSync(temporary);
	assertExternalAncestorIdentities(ancestors);
	try {
		linkSync(temporary, target);
		assertExternalAncestorIdentities(ancestors);
	} finally {
		try {
			const current = lstatSync(temporary);
			if (
				current.dev === temporaryIdentity.dev &&
				current.ino === temporaryIdentity.ino &&
				!current.isSymbolicLink()
			) {
				unlinkSync(temporary);
			}
		} catch {
			// The owned temporary file was already removed.
		}
	}
}

function directoryIdentity(
	path: string,
	vaultRoot?: string,
): DirectoryIdentity {
	if (vaultRoot !== undefined) assertWithinVault(vaultRoot, path);
	const stat = lstatSync(path);
	if (!stat.isDirectory() || stat.isSymbolicLink()) {
		throw new Error(`directory is not stable: ${path}`);
	}
	return { dev: stat.dev, ino: stat.ino, realpath: realpathSync(path) };
}

function assertDirectoryIdentity(
	path: string,
	expected: DirectoryIdentity,
	vaultRoot?: string,
): void {
	const actual = directoryIdentity(path, vaultRoot);
	if (
		actual.dev !== expected.dev ||
		actual.ino !== expected.ino ||
		!sameResolvedPath(actual.realpath, expected.realpath)
	) {
		throw new Error(`directory identity changed: ${path}`);
	}
}

function canonicalExternalDirectory(path: string): string {
	const resolved = resolvePath(path);
	const before = lstatSync(resolved);
	if (!before.isDirectory() || before.isSymbolicLink()) {
		throw new Error(
			`source path ancestor is not a stable directory: ${resolved}`,
		);
	}
	const canonical = realpathSync(resolved);
	const after = lstatSync(canonical);
	if (
		!after.isDirectory() ||
		after.isSymbolicLink() ||
		after.dev !== before.dev ||
		after.ino !== before.ino
	) {
		throw new Error(`source path ancestor identity changed: ${resolved}`);
	}
	return canonical;
}

function canonicalExternalRegularFile(path: string): string {
	const resolved = resolvePath(path);
	const before = lstatSync(resolved);
	if (!before.isFile() || before.isSymbolicLink()) {
		throw new Error("migration source must be a non-symlink regular file");
	}
	const canonical = realpathSync(resolved);
	const after = lstatSync(canonical);
	if (
		!after.isFile() ||
		after.isSymbolicLink() ||
		after.dev !== before.dev ||
		after.ino !== before.ino
	) {
		throw new Error(
			"migration source identity changed during canonicalization",
		);
	}
	return canonical;
}

function canonicalExternalTarget(path: string): string {
	const resolved = resolvePath(path);
	return join(
		canonicalExternalDirectory(dirname(resolved)),
		basename(resolved),
	);
}

function externalAncestorIdentities(
	path: string,
): Map<string, DirectoryIdentity> {
	const identities = new Map<string, DirectoryIdentity>();
	// Operate through the canonical parent chain after validating the caller's
	// final directory inode. macOS exposes /var as a stable system alias for
	// /private/var, including for os.tmpdir(); retaining the lexical alias would
	// make every destructive migration move fail before its guarded rename.
	let current = canonicalExternalDirectory(path);
	for (let index = 0; index < 512; index += 1) {
		const stat = lstatSync(current);
		if (!stat.isDirectory() || stat.isSymbolicLink()) {
			throw new Error(
				`source path ancestor is not a stable directory: ${current}`,
			);
		}
		const real = realpathSync(current);
		if (!sameResolvedPath(real, current)) {
			throw new Error(
				`source path ancestor resolves through a link: ${current}`,
			);
		}
		identities.set(current, { dev: stat.dev, ino: stat.ino, realpath: real });
		const parent = dirname(current);
		if (parent === current) return identities;
		current = parent;
	}
	throw new Error("source path ancestor chain exceeds safety limit");
}

function assertExternalAncestorIdentities(
	identities: Map<string, DirectoryIdentity>,
): void {
	for (const [path, expected] of identities) {
		assertDirectoryIdentity(path, expected);
	}
}

function fingerprintRegularFile(
	path: string,
	vaultRoot: string,
): FileFingerprint {
	assertWithinVault(vaultRoot, path);
	const before = lstatSync(path);
	if (!before.isFile() || before.isSymbolicLink()) {
		throw new Error("migration snapshot refuses non-regular files");
	}
	const noFollow = constants.O_NOFOLLOW ?? 0;
	const fd = openSync(path, constants.O_RDONLY | noFollow);
	const hash = createHash("sha256");
	let bytes = 0;
	try {
		const opened = fstatSync(fd);
		if (
			!opened.isFile() ||
			opened.dev !== before.dev ||
			opened.ino !== before.ino ||
			opened.size !== before.size
		) {
			throw new Error("migration snapshot file changed before open");
		}
		const chunk = Buffer.allocUnsafe(64 * 1024);
		while (true) {
			const count = readSync(fd, chunk, 0, chunk.length, null);
			if (count === 0) break;
			hash.update(chunk.subarray(0, count));
			bytes += count;
		}
		const after = fstatSync(fd);
		const afterPath = lstatSync(path);
		if (
			after.dev !== opened.dev ||
			after.ino !== opened.ino ||
			after.size !== opened.size ||
			after.mtimeMs !== opened.mtimeMs ||
			after.ctimeMs !== opened.ctimeMs ||
			afterPath.dev !== opened.dev ||
			afterPath.ino !== opened.ino ||
			afterPath.isSymbolicLink() ||
			bytes !== opened.size
		) {
			throw new Error("migration snapshot file changed while hashing");
		}
		return { path: "", sha256: hash.digest("hex"), size: bytes };
	} finally {
		closeSync(fd);
	}
}

function toPortableRelative(root: string, path: string): string {
	const rel = relative(root, path).split(sep).join("/");
	if (
		rel.length === 0 ||
		rel.startsWith("../") ||
		rel === ".." ||
		rel.split("/").some((part) => part === "" || part === "." || part === "..")
	) {
		throw new Error("rollback snapshot contains an unsafe relative path");
	}
	return rel;
}

function childPath(
	root: string,
	portableRelative: string,
	vaultRoot: string,
): string {
	if (
		portableRelative.length === 0 ||
		portableRelative
			.split("/")
			.some((part) => part === "" || part === "." || part === "..")
	) {
		throw new Error("rollback manifest contains an unsafe file path");
	}
	const target = resolvePath(join(root, ...portableRelative.split("/")));
	assertWithinVault(vaultRoot, target);
	const rel = relative(resolvePath(root), target);
	if (rel.startsWith("..") || resolvePath(join(root, rel)) !== target) {
		throw new Error("rollback file path escapes its recorded tree");
	}
	return target;
}

function fingerprintTree(root: string, vaultRoot: string): TreeFingerprint {
	assertWithinVault(vaultRoot, root);
	if (!existsSync(root)) return { exists: false, files: [] };
	const rootIdentity = directoryIdentity(root, vaultRoot);
	const pending = [root];
	const files: FileFingerprint[] = [];
	let totalBytes = 0;
	while (pending.length > 0) {
		const directory = pending.pop();
		if (directory === undefined) break;
		const identity = directoryIdentity(directory, vaultRoot);
		const entries = readdirSync(directory, { withFileTypes: true }).sort(
			(a, b) => comparePortablePaths(a.name, b.name),
		);
		for (const entry of entries) {
			const path = join(directory, entry.name);
			assertWithinVault(vaultRoot, path);
			const stat = lstatSync(path);
			if (entry.isSymbolicLink() || stat.isSymbolicLink()) {
				throw new Error(
					"migration snapshot refuses symbolic links or reparse points",
				);
			}
			if (entry.isDirectory() && stat.isDirectory()) {
				pending.push(path);
				continue;
			}
			if (!entry.isFile() || !stat.isFile()) {
				throw new Error("migration snapshot refuses non-regular files");
			}
			const fingerprint = fingerprintRegularFile(path, vaultRoot);
			totalBytes += fingerprint.size;
			if (
				files.length + 1 > MAX_SNAPSHOT_FILES ||
				totalBytes > MAX_SNAPSHOT_BYTES
			) {
				throw new Error("migration snapshot exceeds its safety limit");
			}
			files.push({
				...fingerprint,
				path: toPortableRelative(root, path),
			});
		}
		assertDirectoryIdentity(directory, identity, vaultRoot);
	}
	assertDirectoryIdentity(root, rootIdentity, vaultRoot);
	files.sort((left, right) => comparePortablePaths(left.path, right.path));
	return { exists: true, files };
}

function fingerprintsEqual(
	left: FileFingerprint[],
	right: FileFingerprint[],
): boolean {
	return JSON.stringify(left) === JSON.stringify(right);
}

function treeMatches(
	actual: TreeFingerprint,
	exists: boolean,
	files: FileFingerprint[],
): boolean {
	return actual.exists === exists && fingerprintsEqual(actual.files, files);
}

function copyFingerprintSet(
	sourceRoot: string,
	targetRoot: string,
	files: FileFingerprint[],
	vaultRoot: string,
): void {
	assertWithinVault(vaultRoot, sourceRoot);
	assertWithinVault(vaultRoot, targetRoot);
	mkdirSync(targetRoot, { recursive: true });
	for (const expected of files) {
		const source = childPath(sourceRoot, expected.path, vaultRoot);
		const target = childPath(targetRoot, expected.path, vaultRoot);
		const actual = fingerprintRegularFile(source, vaultRoot);
		if (actual.sha256 !== expected.sha256 || actual.size !== expected.size) {
			throw new Error("rollback copy source no longer matches its fingerprint");
		}
		mkdirSync(dirname(target), { recursive: true });
		assertWithinVault(vaultRoot, target);
		const sourceFd = openSync(
			source,
			constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
		);
		let targetFd: number | null = null;
		try {
			targetFd = openSync(
				target,
				constants.O_WRONLY |
					constants.O_CREAT |
					constants.O_EXCL |
					(constants.O_NOFOLLOW ?? 0),
				0o600,
			);
			const chunk = Buffer.allocUnsafe(64 * 1024);
			while (true) {
				const count = readSync(sourceFd, chunk, 0, chunk.length, null);
				if (count === 0) break;
				writeAll(targetFd, chunk.subarray(0, count));
			}
			fsyncSync(targetFd);
		} finally {
			closeSync(sourceFd);
			if (targetFd !== null) closeSync(targetFd);
		}
		const copied = fingerprintRegularFile(target, vaultRoot);
		if (copied.sha256 !== expected.sha256 || copied.size !== expected.size) {
			throw new Error("rollback copy hash verification failed");
		}
	}
}

function loadOrCreateIntegrityKey(vaultRoot: string): Buffer {
	const stateDir = join(vaultRoot, ".mneme");
	const keyPath = join(stateDir, KEY_FILENAME);
	assertWithinVault(vaultRoot, keyPath);
	mkdirSync(stateDir, { recursive: true });
	try {
		const fd = openSync(
			keyPath,
			constants.O_WRONLY |
				constants.O_CREAT |
				constants.O_EXCL |
				(constants.O_NOFOLLOW ?? 0),
			0o600,
		);
		try {
			writeAll(fd, randomBytes(KEY_BYTES));
			fsyncSync(fd);
		} finally {
			closeSync(fd);
		}
	} catch (error) {
		if (errorCode(error) !== "EEXIST") throw error;
	}
	const key = readBoundedRegularFile(keyPath, KEY_BYTES);
	if (key.length !== KEY_BYTES) {
		throw new Error("migration integrity key has an invalid length");
	}
	return key;
}

function manifestBody(
	manifest: RollbackManifest,
): Omit<RollbackManifest, "integrity_hmac"> {
	return {
		schema: manifest.schema,
		run_id: manifest.run_id,
		status: manifest.status,
		vault_root: manifest.vault_root,
		export_root: manifest.export_root,
		staging_root: manifest.staging_root,
		quarantine_root: manifest.quarantine_root,
		snapshot_root: manifest.snapshot_root,
		restore_root: manifest.restore_root,
		rollback_after_root: manifest.rollback_after_root,
		source_db: manifest.source_db,
		source_quarantine_path: manifest.source_quarantine_path,
		source_sha256: manifest.source_sha256,
		archive_mode: manifest.archive_mode,
		archive_path: manifest.archive_path,
		source_moved: manifest.source_moved,
		before_root_existed: manifest.before_root_existed,
		after_root_existed: manifest.after_root_existed,
		before_files: manifest.before_files,
		after_files: manifest.after_files,
		prepared_at: manifest.prepared_at,
		applied_at: manifest.applied_at,
		rolled_back_at: manifest.rolled_back_at,
		abort_reason: manifest.abort_reason,
	};
}

function computeManifestHmac(key: Buffer, manifest: RollbackManifest): string {
	return createHmac("sha256", key)
		.update("mneme-migration-rollback-v2\0")
		.update(JSON.stringify(manifestBody(manifest)))
		.digest("hex");
}

function writeRollbackManifest(path: string, manifest: RollbackManifest): void {
	if (manifest.abort_reason !== null) {
		manifest.abort_reason = sanitizeManifestDiagnostic(
			manifest,
			manifest.abort_reason,
		);
	}
	const key = loadOrCreateIntegrityKey(manifest.vault_root);
	manifest.integrity_hmac = computeManifestHmac(key, manifest);
	const payload = {
		...manifestBody(manifest),
		integrity_hmac: manifest.integrity_hmac,
	};
	atomicWriteText(path, `${JSON.stringify(payload, null, 2)}\n`, {
		vaultRoot: manifest.vault_root,
	});
}

function parseFingerprintList(
	value: unknown,
	field: string,
): FileFingerprint[] {
	if (!Array.isArray(value)) throw new Error(`${field} is not an array`);
	if (value.length > MAX_SNAPSHOT_FILES)
		throw new Error(`${field} is too large`);
	let totalBytes = 0;
	const parsed = value.map((item) => {
		if (typeof item !== "object" || item === null) {
			throw new Error(`${field} contains a non-object`);
		}
		const record = item as Record<string, unknown>;
		if (
			typeof record.path !== "string" ||
			typeof record.sha256 !== "string" ||
			!LOWER_SHA256.test(record.sha256) ||
			typeof record.size !== "number" ||
			!Number.isSafeInteger(record.size) ||
			record.size < 0
		) {
			throw new Error(`${field} contains an invalid fingerprint`);
		}
		totalBytes += record.size;
		if (totalBytes > MAX_SNAPSHOT_BYTES)
			throw new Error(`${field} is too large`);
		return { path: record.path, sha256: record.sha256, size: record.size };
	});
	for (let index = 1; index < parsed.length; index += 1) {
		if (
			comparePortablePaths(
				parsed[index - 1]?.path ?? "",
				parsed[index]?.path ?? "",
			) >= 0
		) {
			throw new Error(`${field} is not strictly path sorted`);
		}
	}
	return parsed;
}

function expectedManifestPaths(
	vaultRoot: string,
	manifestPath: string,
	runId: string,
) {
	const runRoot = resolvePath(dirname(manifestPath));
	const importedRoot = resolvePath(join(vaultRoot, "imported"));
	return {
		exportRoot: resolvePath(join(vaultRoot, ...EXPORT_SEGMENTS)),
		stagingRoot: resolvePath(join(importedRoot, `.claude-mem-stage-${runId}`)),
		quarantineRoot: resolvePath(
			join(importedRoot, `.claude-mem-after-${runId}`),
		),
		snapshotRoot: resolvePath(join(runRoot, "snapshot")),
		restoreRoot: resolvePath(
			join(importedRoot, `.claude-mem-restore-${runId}`),
		),
		rollbackAfterRoot: resolvePath(
			join(importedRoot, `.claude-mem-rollback-after-${runId}`),
		),
	};
}

function readRollbackManifest(
	vault: VaultConfig,
	manifestPath: string,
): RollbackManifest {
	const path = resolvePath(manifestPath);
	const migrationsRoot = resolvePath(join(vault.root, ".mneme", "migrations"));
	assertWithinVault(vault.root, path);
	const rel = relative(migrationsRoot, path);
	if (
		rel.startsWith("..") ||
		!sameResolvedPath(join(migrationsRoot, rel), path)
	) {
		throw new Error("rollback manifest is outside the vault migration journal");
	}
	const value = JSON.parse(
		readBoundedRegularFile(path, MAX_MANIFEST_BYTES).toString("utf8"),
	) as unknown;
	if (typeof value !== "object" || value === null) {
		throw new Error("rollback manifest is not an object");
	}
	const record = value as Record<string, unknown>;
	const validStatuses: ManifestStatus[] = [
		"prepared",
		"commit-ready",
		"committing",
		"applied",
		"rollback-swapping",
		"rolled-back",
		"aborted",
	];
	if (
		record.schema !== MANIFEST_SCHEMA ||
		typeof record.run_id !== "string" ||
		!RUN_ID_RE.test(record.run_id) ||
		!validStatuses.includes(record.status as ManifestStatus) ||
		typeof record.vault_root !== "string" ||
		typeof record.export_root !== "string" ||
		typeof record.staging_root !== "string" ||
		typeof record.quarantine_root !== "string" ||
		typeof record.snapshot_root !== "string" ||
		typeof record.restore_root !== "string" ||
		typeof record.rollback_after_root !== "string" ||
		typeof record.source_db !== "string" ||
		typeof record.source_quarantine_path !== "string" ||
		(record.source_sha256 !== null &&
			(typeof record.source_sha256 !== "string" ||
				!LOWER_SHA256.test(record.source_sha256))) ||
		(record.archive_mode !== "preserve" &&
			record.archive_mode !== "copy" &&
			record.archive_mode !== "move") ||
		(record.archive_path !== null && typeof record.archive_path !== "string") ||
		typeof record.source_moved !== "boolean" ||
		typeof record.before_root_existed !== "boolean" ||
		(record.after_root_existed !== null &&
			typeof record.after_root_existed !== "boolean") ||
		typeof record.prepared_at !== "string" ||
		(record.applied_at !== null && typeof record.applied_at !== "string") ||
		(record.rolled_back_at !== null &&
			typeof record.rolled_back_at !== "string") ||
		(record.abort_reason !== null && typeof record.abort_reason !== "string") ||
		typeof record.integrity_hmac !== "string" ||
		!LOWER_SHA256.test(record.integrity_hmac)
	) {
		throw new Error("rollback manifest fields are invalid");
	}
	const runId = record.run_id;
	if (basename(dirname(path)) !== runId || basename(path) !== "rollback.json") {
		throw new Error("rollback manifest run identity does not match its path");
	}
	const expectedVault = resolvePath(vault.root);
	const expected = expectedManifestPaths(expectedVault, path, runId);
	if (
		!sameResolvedPath(record.vault_root, expectedVault) ||
		!sameResolvedPath(record.export_root, expected.exportRoot) ||
		!sameResolvedPath(record.staging_root, expected.stagingRoot) ||
		!sameResolvedPath(record.quarantine_root, expected.quarantineRoot) ||
		!sameResolvedPath(record.snapshot_root, expected.snapshotRoot) ||
		!sameResolvedPath(record.restore_root, expected.restoreRoot) ||
		!sameResolvedPath(record.rollback_after_root, expected.rollbackAfterRoot)
	) {
		throw new Error("rollback manifest paths do not match this vault and run");
	}
	for (const candidate of Object.values(expected)) {
		assertWithinVault(vault.root, candidate);
	}
	const expectedArchive = resolvePath(
		join(expected.exportRoot, "_archive", "claude-mem.db"),
	);
	if (
		(record.archive_mode === "preserve" && record.archive_path !== null) ||
		(record.archive_mode !== "preserve" &&
			(record.archive_path === null ||
				!sameResolvedPath(record.archive_path, expectedArchive))) ||
		(record.archive_mode !== "move" &&
			(record.source_sha256 !== null || record.source_moved)) ||
		(record.archive_mode === "move" && record.source_sha256 === null)
	) {
		throw new Error("rollback manifest archive invariants are invalid");
	}
	const expectedSourceQuarantine = resolvePath(
		join(
			dirname(record.source_db),
			`.${basename(record.source_db)}.mneme-migration-${runId}`,
		),
	);
	if (
		!sameResolvedPath(record.source_quarantine_path, expectedSourceQuarantine)
	) {
		throw new Error("rollback manifest source quarantine path is invalid");
	}
	const manifest: RollbackManifest = {
		...(record as unknown as RollbackManifest),
		before_files: parseFingerprintList(record.before_files, "before_files"),
		after_files:
			record.after_files === null
				? null
				: parseFingerprintList(record.after_files, "after_files"),
	};
	const key = loadOrCreateIntegrityKey(vault.root);
	const expectedHmac = Buffer.from(computeManifestHmac(key, manifest), "ascii");
	const recordedHmac = Buffer.from(manifest.integrity_hmac, "ascii");
	if (!buffersEqual(expectedHmac, recordedHmac)) {
		throw new Error("rollback manifest integrity HMAC mismatch");
	}
	return manifest;
}

function processIsAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch (error) {
		return errorCode(error) === "EPERM";
	}
}

function removeOwnedFile(path: string, token: Buffer): void {
	try {
		const before = lstatSync(path);
		const observed = readBoundedRegularFile(path, 4096);
		const current = lstatSync(path);
		if (
			before.dev === current.dev &&
			before.ino === current.ino &&
			buffersEqual(observed, token) &&
			buffersEqual(readBoundedRegularFile(path, 4096), token)
		) {
			unlinkSync(path);
		}
	} catch {
		// Never remove a lock that no longer proves ownership.
	}
}

function acquireMigrationLock(vault: VaultConfig): () => void {
	const migrationsRoot = join(vault.root, ".mneme", "migrations");
	const lockPath = join(migrationsRoot, ".operation.lock");
	assertWithinVault(vault.root, lockPath);
	mkdirSync(migrationsRoot, { recursive: true });
	const token = Buffer.from(
		JSON.stringify({
			pid: process.pid,
			nonce: randomBytes(16).toString("hex"),
		}),
		"utf8",
	);
	const deadline = performance.now() + LOCK_TIMEOUT_MS;
	while (true) {
		try {
			const fd = openSync(
				lockPath,
				constants.O_WRONLY |
					constants.O_CREAT |
					constants.O_EXCL |
					(constants.O_NOFOLLOW ?? 0),
				0o600,
			);
			try {
				writeAll(fd, token);
				fsyncSync(fd);
			} finally {
				closeSync(fd);
			}
			return () => removeOwnedFile(lockPath, token);
		} catch (error) {
			if (!["EEXIST", "EACCES", "EPERM"].includes(errorCode(error) ?? "")) {
				throw error;
			}
			try {
				const stat = lstatSync(lockPath);
				const lockBytes = readBoundedRegularFile(lockPath, 4096);
				const raw = lockBytes.toString("utf8");
				const parsed = JSON.parse(raw) as { pid?: unknown };
				if (
					typeof parsed.pid === "number" &&
					Number.isSafeInteger(parsed.pid) &&
					parsed.pid > 0 &&
					!processIsAlive(parsed.pid)
				) {
					const current = lstatSync(lockPath);
					const currentBytes = readBoundedRegularFile(lockPath, 4096);
					if (
						current.dev === stat.dev &&
						current.ino === stat.ino &&
						buffersEqual(currentBytes, lockBytes)
					) {
						unlinkSync(lockPath);
					}
					continue;
				}
				if (Date.now() - stat.mtimeMs > MALFORMED_LOCK_STALE_MS) {
					throw new Error("stale migration lock requires operator inspection");
				}
			} catch (lockError) {
				if (errorCode(lockError) === "ENOENT") continue;
				if (
					lockError instanceof Error &&
					lockError.message ===
						"stale migration lock requires operator inspection"
				) {
					throw lockError;
				}
			}
			if (performance.now() >= deadline) {
				throw new Error("could not acquire the migration operation lock");
			}
			sleepSync(LOCK_POLL_MS);
		}
	}
}

export function withMigrationLock<T>(
	vault: VaultConfig,
	operation: () => T,
): T {
	const release = acquireMigrationLock(vault);
	try {
		return operation();
	} finally {
		release();
	}
}

function archiveExistingTree(
	path: string,
	destination: string,
	vaultRoot: string,
): void {
	if (!existsSync(path)) return;
	assertWithinVault(vaultRoot, path);
	assertWithinVault(vaultRoot, destination);
	if (existsSync(destination))
		throw new Error("journal destination already exists");
	directoryIdentity(path, vaultRoot);
	renameSync(path, destination);
}

export function prepareMigrationRollback(
	vault: VaultConfig,
	exportRoot: string,
	sourceDb: string,
	archiveMode: MigrationArchiveMode,
): MigrationRollbackHandle {
	const resolvedExport = resolvePath(exportRoot);
	assertWithinVault(vault.root, resolvedExport);
	const before = fingerprintTree(resolvedExport, vault.root);
	const runId = `${new Date().toISOString().replace(/[:.]/g, "-")}-${randomBytes(8).toString("hex")}`;
	const runRoot = resolvePath(join(vault.root, ".mneme", "migrations", runId));
	const manifestPath = join(runRoot, "rollback.json");
	const expected = expectedManifestPaths(vault.root, manifestPath, runId);
	for (const path of [runRoot, ...Object.values(expected)]) {
		assertWithinVault(vault.root, path);
	}
	if (
		existsSync(runRoot) ||
		existsSync(expected.stagingRoot) ||
		existsSync(expected.quarantineRoot) ||
		existsSync(expected.restoreRoot) ||
		existsSync(expected.rollbackAfterRoot)
	) {
		throw new Error("migration journal path already exists");
	}
	mkdirSync(runRoot, { recursive: true });
	const resolvedSource = canonicalExternalRegularFile(sourceDb);
	const sourceHash =
		archiveMode === "move"
			? hashRegularFile(resolvedSource, MAX_SNAPSHOT_BYTES).sha256
			: null;
	const archivePath =
		archiveMode === "preserve"
			? null
			: resolvePath(join(resolvedExport, "_archive", "claude-mem.db"));
	const manifest: RollbackManifest = {
		schema: MANIFEST_SCHEMA,
		run_id: runId,
		status: "prepared",
		vault_root: resolvePath(vault.root),
		export_root: resolvedExport,
		staging_root: expected.stagingRoot,
		quarantine_root: expected.quarantineRoot,
		snapshot_root: expected.snapshotRoot,
		restore_root: expected.restoreRoot,
		rollback_after_root: expected.rollbackAfterRoot,
		source_db: resolvedSource,
		source_quarantine_path: resolvePath(
			join(
				dirname(resolvedSource),
				`.${basename(resolvedSource)}.mneme-migration-${runId}`,
			),
		),
		source_sha256: sourceHash,
		archive_mode: archiveMode,
		archive_path: archivePath,
		source_moved: false,
		before_root_existed: before.exists,
		after_root_existed: null,
		before_files: before.files,
		after_files: null,
		prepared_at: new Date().toISOString(),
		applied_at: null,
		rolled_back_at: null,
		abort_reason: null,
		integrity_hmac: "0".repeat(64),
	};
	writeRollbackManifest(manifestPath, manifest);
	mkdirSync(expected.stagingRoot, { recursive: true });
	if (before.exists) {
		copyFingerprintSet(
			resolvedExport,
			expected.stagingRoot,
			before.files,
			vault.root,
		);
	}
	return {
		manifestPath,
		stagingRoot: expected.stagingRoot,
		exportRoot: resolvedExport,
	};
}

export function abortMigrationRollback(
	vault: VaultConfig,
	handle: MigrationRollbackHandle,
	reason: string,
): void {
	const manifest = readRollbackManifest(vault, handle.manifestPath);
	if (manifest.status === "aborted" || manifest.status === "rolled-back")
		return;
	if (manifest.status !== "prepared" && manifest.status !== "commit-ready") {
		throw new Error("migration can no longer be aborted before commit");
	}
	const current = fingerprintTree(manifest.export_root, manifest.vault_root);
	if (
		!treeMatches(current, manifest.before_root_existed, manifest.before_files)
	) {
		throw new Error(
			"migration abort refused because the original tree changed",
		);
	}
	archiveExistingTree(
		manifest.staging_root,
		join(dirname(handle.manifestPath), "aborted-stage"),
		manifest.vault_root,
	);
	manifest.status = "aborted";
	manifest.abort_reason = reason.slice(0, 4096);
	manifest.rolled_back_at = new Date().toISOString();
	writeRollbackManifest(handle.manifestPath, manifest);
}

function quarantineSourceForMove(manifest: RollbackManifest): void {
	if (manifest.archive_mode !== "move") return;
	if (manifest.source_sha256 === null) {
		throw new Error("move migration lacks source hash evidence");
	}
	if (existsSync(manifest.source_quarantine_path)) {
		if (existsSync(manifest.source_db)) {
			throw new Error("both source and source quarantine exist");
		}
		return;
	}
	assertSourceHasNoSqliteSidecars(manifest.source_db);
	const ancestors = externalAncestorIdentities(dirname(manifest.source_db));
	const sourceHash = hashRegularFile(
		manifest.source_db,
		MAX_SNAPSHOT_BYTES,
	).sha256;
	if (sourceHash !== manifest.source_sha256) {
		throw new Error("source DB changed after migration staging");
	}
	assertExternalAncestorIdentities(ancestors);
	renameSync(manifest.source_db, manifest.source_quarantine_path);
	try {
		assertExternalAncestorIdentities(ancestors);
		assertSourceHasNoSqliteSidecars(manifest.source_db);
	} catch (error) {
		if (!existsSync(manifest.source_db)) {
			renameSync(manifest.source_quarantine_path, manifest.source_db);
		}
		throw error;
	}
}

function assertSourceHasNoSqliteSidecars(sourceDb: string): void {
	for (const suffix of ["-wal", "-shm", "-journal"]) {
		if (existsSync(`${sourceDb}${suffix}`)) {
			throw new Error(
				"source SQLite database has an active WAL or journal sidecar; destructive move refused",
			);
		}
	}
}

function discardSourceQuarantine(manifest: RollbackManifest): void {
	if (!existsSync(manifest.source_quarantine_path)) return;
	if (manifest.source_sha256 === null) {
		throw new Error("source quarantine lacks hash evidence");
	}
	if (
		hashRegularFile(manifest.source_quarantine_path, MAX_SNAPSHOT_BYTES)
			.sha256 !== manifest.source_sha256
	) {
		throw new Error("source quarantine changed before cleanup");
	}
	unlinkSync(manifest.source_quarantine_path);
}

function abortCommitAfterDetectedRace(
	manifest: RollbackManifest,
	manifestPath: string,
	afterFiles: FileFingerprint[],
	stageWasPublished: boolean,
	reason: string,
): void {
	if (stageWasPublished) {
		const published = fingerprintTree(
			manifest.export_root,
			manifest.vault_root,
		);
		if (!treeMatches(published, true, afterFiles)) {
			throw new Error(
				"migration race recovery refused because the published tree also changed",
			);
		}
		if (existsSync(manifest.staging_root)) {
			throw new Error(
				"migration race recovery found an unexpected staging tree",
			);
		}
		renameSync(manifest.export_root, manifest.staging_root);
	}
	if (existsSync(manifest.export_root)) {
		throw new Error(
			"migration race recovery refused to overwrite a newly created live tree",
		);
	}
	if (manifest.before_root_existed) {
		if (!existsSync(manifest.quarantine_root)) {
			throw new Error("migration race recovery lost the quarantined live tree");
		}
		renameSync(manifest.quarantine_root, manifest.export_root);
	}
	restoreQuarantinedSource(manifest);
	archiveExistingTree(
		manifest.staging_root,
		join(dirname(manifestPath), "aborted-stage"),
		manifest.vault_root,
	);
	manifest.status = "aborted";
	manifest.source_moved = false;
	manifest.abort_reason = reason;
	manifest.rolled_back_at = new Date().toISOString();
	writeRollbackManifest(manifestPath, manifest);
}

export function finalizeMigrationRollback(
	vault: VaultConfig,
	handle: MigrationRollbackHandle,
	options: {
		sourceMoved: boolean;
		archivePath?: string;
		/** Test-only crash injection. Production callers must omit this. */
		faultAt?: CommitFaultPoint;
		/** Test-only race injection after live-root quarantine. */
		onAfterLiveQuarantineForTest?: () => void;
	},
): void {
	const manifest = readRollbackManifest(vault, handle.manifestPath);
	if (manifest.status !== "prepared") {
		throw new Error("rollback manifest is not in prepared state");
	}
	if (options.sourceMoved && manifest.archive_mode !== "move") {
		throw new Error("migration source move intent does not match the manifest");
	}
	if (
		options.archivePath !== undefined &&
		(manifest.archive_path === null ||
			!sameResolvedPath(options.archivePath, manifest.archive_path))
	) {
		throw new Error("migration archive path does not match the manifest");
	}
	if (
		(manifest.archive_mode === "copy" || options.sourceMoved) &&
		options.archivePath === undefined
	) {
		throw new Error("migration archive evidence is unavailable");
	}
	const after = fingerprintTree(manifest.staging_root, manifest.vault_root);
	if (!after.exists) throw new Error("migration staging tree is missing");
	if (options.sourceMoved) {
		const stagedArchive = join(
			manifest.staging_root,
			"_archive",
			"claude-mem.db",
		);
		if (
			manifest.source_sha256 === null ||
			hashRegularFile(stagedArchive, MAX_SNAPSHOT_BYTES, manifest.vault_root)
				.sha256 !== manifest.source_sha256
		) {
			throw new Error(
				"staged archive hash does not match the source DB; move refused",
			);
		}
	}
	manifest.status = "commit-ready";
	manifest.after_root_existed = true;
	manifest.after_files = after.files;
	writeRollbackManifest(handle.manifestPath, manifest);

	const current = fingerprintTree(manifest.export_root, manifest.vault_root);
	if (
		!treeMatches(current, manifest.before_root_existed, manifest.before_files)
	) {
		throw new Error("migration commit refused because the live tree changed");
	}
	manifest.status = "committing";
	writeRollbackManifest(handle.manifestPath, manifest);
	if (options.sourceMoved) {
		quarantineSourceForMove(manifest);
		manifest.source_moved = true;
		writeRollbackManifest(handle.manifestPath, manifest);
		injectFault(options.faultAt, "after-source-quarantine");
	}

	const currentAgain = fingerprintTree(
		manifest.export_root,
		manifest.vault_root,
	);
	if (
		!treeMatches(
			currentAgain,
			manifest.before_root_existed,
			manifest.before_files,
		)
	) {
		throw new Error("migration commit lost its compare-and-swap precondition");
	}
	if (currentAgain.exists) {
		renameSync(manifest.export_root, manifest.quarantine_root);
	}
	options.onAfterLiveQuarantineForTest?.();
	injectFault(options.faultAt, "after-live-quarantine");
	const quarantined = fingerprintTree(
		manifest.quarantine_root,
		manifest.vault_root,
	);
	if (
		!treeMatches(
			quarantined,
			manifest.before_root_existed,
			manifest.before_files,
		)
	) {
		abortCommitAfterDetectedRace(
			manifest,
			handle.manifestPath,
			after.files,
			false,
			"live tree changed while it was quarantined before migration publication",
		);
		throw new Error("migration commit aborted after a quarantined-tree race");
	}
	if (existsSync(manifest.export_root)) {
		throw new Error(
			"migration commit refused because a new live tree appeared during swap",
		);
	}
	renameSync(manifest.staging_root, manifest.export_root);
	injectFault(options.faultAt, "after-stage-publish");
	const committed = fingerprintTree(manifest.export_root, manifest.vault_root);
	if (!treeMatches(committed, true, after.files)) {
		throw new Error("committed migration tree failed hash verification");
	}
	const quarantineAfterPublish = fingerprintTree(
		manifest.quarantine_root,
		manifest.vault_root,
	);
	if (
		!treeMatches(
			quarantineAfterPublish,
			manifest.before_root_existed,
			manifest.before_files,
		)
	) {
		abortCommitAfterDetectedRace(
			manifest,
			handle.manifestPath,
			after.files,
			true,
			"live tree changed while migration staging was being published",
		);
		throw new Error("migration commit aborted after a publish-time race");
	}
	if (manifest.before_root_existed) {
		renameSync(manifest.quarantine_root, manifest.snapshot_root);
		const snapshot = fingerprintTree(
			manifest.snapshot_root,
			manifest.vault_root,
		);
		if (!treeMatches(snapshot, true, manifest.before_files)) {
			renameSync(manifest.snapshot_root, manifest.quarantine_root);
			abortCommitAfterDetectedRace(
				manifest,
				handle.manifestPath,
				after.files,
				true,
				"pre-migration snapshot changed before commit finalization",
			);
			throw new Error("migration commit aborted after a snapshot race");
		}
	}
	manifest.status = "applied";
	manifest.applied_at = new Date().toISOString();
	writeRollbackManifest(handle.manifestPath, manifest);
	if (manifest.source_moved) discardSourceQuarantine(manifest);
}

function restoreQuarantinedSource(manifest: RollbackManifest): boolean {
	if (!existsSync(manifest.source_quarantine_path)) return false;
	if (
		manifest.source_sha256 === null ||
		hashRegularFile(manifest.source_quarantine_path, MAX_SNAPSHOT_BYTES)
			.sha256 !== manifest.source_sha256
	) {
		throw new Error("source quarantine failed hash verification");
	}
	if (existsSync(manifest.source_db)) {
		throw new Error(
			"cannot restore quarantined source because its path exists",
		);
	}
	const ancestors = externalAncestorIdentities(dirname(manifest.source_db));
	assertExternalAncestorIdentities(ancestors);
	linkSync(manifest.source_quarantine_path, manifest.source_db);
	assertExternalAncestorIdentities(ancestors);
	unlinkSync(manifest.source_quarantine_path);
	return true;
}

function recoverInterruptedCommit(
	manifest: RollbackManifest,
	manifestPath: string,
): boolean {
	if (
		manifest.status !== "prepared" &&
		manifest.status !== "commit-ready" &&
		manifest.status !== "committing"
	) {
		return false;
	}
	const live = fingerprintTree(manifest.export_root, manifest.vault_root);
	const stage = fingerprintTree(manifest.staging_root, manifest.vault_root);
	const quarantine = fingerprintTree(
		manifest.quarantine_root,
		manifest.vault_root,
	);
	const snapshot = fingerprintTree(manifest.snapshot_root, manifest.vault_root);
	const after = manifest.after_files;
	if (
		after !== null &&
		treeMatches(live, true, after) &&
		!stage.exists &&
		(!manifest.before_root_existed ||
			treeMatches(quarantine, true, manifest.before_files) ||
			treeMatches(snapshot, true, manifest.before_files))
	) {
		manifest.status = "applied";
		manifest.applied_at = manifest.applied_at ?? new Date().toISOString();
		manifest.source_moved =
			manifest.source_moved || existsSync(manifest.source_quarantine_path);
		writeRollbackManifest(manifestPath, manifest);
		if (manifest.before_root_existed && quarantine.exists && !snapshot.exists) {
			renameSync(manifest.quarantine_root, manifest.snapshot_root);
		}
		return true;
	}
	if (
		treeMatches(live, manifest.before_root_existed, manifest.before_files) &&
		!quarantine.exists
	) {
		archiveExistingTree(
			manifest.staging_root,
			join(dirname(manifestPath), "aborted-stage"),
			manifest.vault_root,
		);
		restoreQuarantinedSource(manifest);
		manifest.status = "aborted";
		manifest.abort_reason =
			"recovered interrupted migration before live-tree swap";
		manifest.rolled_back_at = new Date().toISOString();
		writeRollbackManifest(manifestPath, manifest);
		return true;
	}
	if (
		!live.exists &&
		treeMatches(quarantine, manifest.before_root_existed, manifest.before_files)
	) {
		if (manifest.before_root_existed) {
			renameSync(manifest.quarantine_root, manifest.export_root);
		}
		archiveExistingTree(
			manifest.staging_root,
			join(dirname(manifestPath), "aborted-stage"),
			manifest.vault_root,
		);
		restoreQuarantinedSource(manifest);
		manifest.status = "aborted";
		manifest.abort_reason =
			"recovered interrupted migration after old-tree quarantine";
		manifest.rolled_back_at = new Date().toISOString();
		writeRollbackManifest(manifestPath, manifest);
		return true;
	}
	throw new Error(
		"interrupted migration state is ambiguous and was left untouched",
	);
}

function restoreMovedSource(
	manifest: RollbackManifest,
	confirmSourceRestore: boolean,
	sourceRestorePath: string | undefined,
): boolean {
	if (!manifest.source_moved) return false;
	if (!confirmSourceRestore || sourceRestorePath === undefined) {
		throw new Error(
			"rollback of archive=move requires --confirm-source-restore and --source with the original absolute path",
		);
	}
	if (
		!sameResolvedPath(
			canonicalExternalTarget(sourceRestorePath),
			manifest.source_db,
		)
	) {
		throw new Error(
			"explicit source restore path does not match the signed manifest",
		);
	}
	if (manifest.source_sha256 === null || manifest.archive_path === null) {
		throw new Error("rollback manifest lacks source restoration evidence");
	}
	if (existsSync(manifest.source_db)) {
		if (
			hashRegularFile(manifest.source_db, MAX_SNAPSHOT_BYTES).sha256 !==
			manifest.source_sha256
		) {
			throw new Error("source path was recreated with different content");
		}
		return false;
	}
	if (restoreQuarantinedSource(manifest)) return true;
	const archived = hashRegularFile(
		manifest.archive_path,
		MAX_SNAPSHOT_BYTES,
		manifest.vault_root,
	);
	if (archived.sha256 !== manifest.source_sha256) {
		throw new Error(
			"archived source hash does not match the rollback manifest",
		);
	}
	copyToExternalPathNoClobber(
		manifest.archive_path,
		manifest.source_db,
		manifest.source_sha256,
		manifest.vault_root,
	);
	const restored = hashRegularFile(manifest.source_db, MAX_SNAPSHOT_BYTES);
	if (restored.sha256 !== manifest.source_sha256) {
		throw new Error("restored source hash verification failed");
	}
	return true;
}

function prepareRestoreTree(
	manifest: RollbackManifest,
	manifestPath: string,
): void {
	if (!manifest.before_root_existed) return;
	if (existsSync(manifest.restore_root)) {
		const interrupted = join(
			dirname(manifestPath),
			`interrupted-restore-${randomBytes(8).toString("hex")}`,
		);
		archiveExistingTree(
			manifest.restore_root,
			interrupted,
			manifest.vault_root,
		);
	}
	mkdirSync(manifest.restore_root, { recursive: true });
	copyFingerprintSet(
		manifest.snapshot_root,
		manifest.restore_root,
		manifest.before_files,
		manifest.vault_root,
	);
	const restored = fingerprintTree(manifest.restore_root, manifest.vault_root);
	if (!treeMatches(restored, true, manifest.before_files)) {
		throw new Error("rollback restore tree failed hash verification");
	}
}

function continueRollbackSwap(
	manifest: RollbackManifest,
	manifestPath: string,
	faultAt?: RollbackMigrationOptions["faultAt"],
): void {
	if (manifest.after_files === null || manifest.after_root_existed === null) {
		throw new Error("rollback manifest lacks applied-tree evidence");
	}
	let live = fingerprintTree(manifest.export_root, manifest.vault_root);
	let after = fingerprintTree(
		manifest.rollback_after_root,
		manifest.vault_root,
	);
	if (
		treeMatches(live, manifest.before_root_existed, manifest.before_files) &&
		treeMatches(after, manifest.after_root_existed, manifest.after_files)
	) {
		manifest.status = "rolled-back";
		manifest.rolled_back_at = new Date().toISOString();
		writeRollbackManifest(manifestPath, manifest);
		archiveExistingTree(
			manifest.rollback_after_root,
			join(dirname(manifestPath), "applied-snapshot"),
			manifest.vault_root,
		);
		return;
	}
	if (
		treeMatches(live, manifest.after_root_existed, manifest.after_files) &&
		!after.exists
	) {
		prepareRestoreTree(manifest, manifestPath);
		const liveAgain = fingerprintTree(
			manifest.export_root,
			manifest.vault_root,
		);
		if (
			!treeMatches(liveAgain, manifest.after_root_existed, manifest.after_files)
		) {
			throw new Error("rollback lost its compare-and-swap precondition");
		}
		renameSync(manifest.export_root, manifest.rollback_after_root);
		injectFault(faultAt, "after-rollback-live-quarantine");
		live = { exists: false, files: [] };
		after = fingerprintTree(manifest.rollback_after_root, manifest.vault_root);
	}
	if (
		!live.exists &&
		treeMatches(after, manifest.after_root_existed, manifest.after_files)
	) {
		if (manifest.before_root_existed) {
			const restore = fingerprintTree(
				manifest.restore_root,
				manifest.vault_root,
			);
			if (!treeMatches(restore, true, manifest.before_files)) {
				prepareRestoreTree(manifest, manifestPath);
			}
			renameSync(manifest.restore_root, manifest.export_root);
			injectFault(faultAt, "after-rollback-restore-publish");
		}
		const finalTree = fingerprintTree(
			manifest.export_root,
			manifest.vault_root,
		);
		if (
			!treeMatches(
				finalTree,
				manifest.before_root_existed,
				manifest.before_files,
			)
		) {
			throw new Error("rollback final tree failed hash verification");
		}
		manifest.status = "rolled-back";
		manifest.rolled_back_at = new Date().toISOString();
		writeRollbackManifest(manifestPath, manifest);
		archiveExistingTree(
			manifest.rollback_after_root,
			join(dirname(manifestPath), "applied-snapshot"),
			manifest.vault_root,
		);
		return;
	}
	throw new Error(
		"interrupted rollback state is ambiguous and was left untouched",
	);
}

function rollbackMigrationLocked(
	options: RollbackMigrationOptions,
): MigrationRollbackResult {
	const result: MigrationRollbackResult = {
		status: "error",
		manifestPath: resolvePath(options.manifestPath),
		restoredFiles: 0,
		sourceRestored: false,
		alreadyRolledBack: false,
		recoveredInterruptedRun: false,
		errors: [],
	};
	try {
		let manifest = readRollbackManifest(options.vault, result.manifestPath);
		if (
			manifest.status === "prepared" ||
			manifest.status === "commit-ready" ||
			manifest.status === "committing"
		) {
			result.recoveredInterruptedRun = recoverInterruptedCommit(
				manifest,
				result.manifestPath,
			);
			manifest = readRollbackManifest(options.vault, result.manifestPath);
		}
		if (manifest.status === "aborted") {
			result.status = "ok";
			result.alreadyRolledBack = true;
			return result;
		}
		if (manifest.status === "rolled-back") {
			result.status = "ok";
			result.alreadyRolledBack = true;
			return result;
		}
		if (
			manifest.status !== "applied" &&
			manifest.status !== "rollback-swapping"
		) {
			throw new Error("migration rollback is not in an applied state");
		}
		if (manifest.status === "applied") {
			if (manifest.before_root_existed && !existsSync(manifest.snapshot_root)) {
				const quarantined = fingerprintTree(
					manifest.quarantine_root,
					manifest.vault_root,
				);
				if (!treeMatches(quarantined, true, manifest.before_files)) {
					throw new Error("applied migration lost its pre-migration snapshot");
				}
				renameSync(manifest.quarantine_root, manifest.snapshot_root);
			}
			const current = fingerprintTree(
				manifest.export_root,
				manifest.vault_root,
			);
			if (
				manifest.after_files === null ||
				manifest.after_root_existed === null ||
				!treeMatches(current, manifest.after_root_existed, manifest.after_files)
			) {
				throw new Error(
					"rollback refused because migrated files changed after the run",
				);
			}
			result.sourceRestored = restoreMovedSource(
				manifest,
				options.confirmSourceRestore === true,
				options.sourceRestorePath,
			);
			manifest.status = "rollback-swapping";
			writeRollbackManifest(result.manifestPath, manifest);
		}
		continueRollbackSwap(manifest, result.manifestPath, options.faultAt);
		result.status = "ok";
		result.restoredFiles = manifest.before_files.length;
		return result;
	} catch (error) {
		result.errors.push(errorMessage(error));
		return result;
	}
}

export function rollbackMigration(
	options: RollbackMigrationOptions,
): MigrationRollbackResult {
	try {
		return withMigrationLock(options.vault, () =>
			rollbackMigrationLocked(options),
		);
	} catch (error) {
		return {
			status: "error",
			manifestPath: resolvePath(options.manifestPath),
			restoredFiles: 0,
			sourceRestored: false,
			alreadyRolledBack: false,
			recoveredInterruptedRun: false,
			errors: [errorMessage(error)],
		};
	}
}
