import { spawn } from "node:child_process";
import { createHmac } from "node:crypto";
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	utimesSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { appendAuditRecord } from "../src/audit.js";

const ZERO_HASH = "0".repeat(64);
const SEAL_DOMAIN = Buffer.from("mneme-audit-seal-v1\0", "utf8");
const PACKAGE_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

interface AuditPaths {
	auditDir: string;
	jsonlPath: string;
	keyPath: string;
	lockPath: string;
	sealPath: string;
	day: string;
}

function freshStateDir(prefix: string): string {
	return mkdtempSync(join(tmpdir(), `mneme-audit-fault-${prefix}-`));
}

function auditPaths(stateDir: string): AuditPaths {
	const day = new Date().toISOString().slice(0, 10);
	const auditDir = join(stateDir, "audit");
	return {
		auditDir,
		day,
		jsonlPath: join(auditDir, `${day}.jsonl`),
		keyPath: join(stateDir, "audit-hmac.key"),
		lockPath: join(auditDir, `${day}.lock`),
		sealPath: join(auditDir, `${day}.seal.json`),
	};
}

function readRecords(jsonlPath: string): Array<Record<string, unknown>> {
	return readFileSync(jsonlPath, "utf8")
		.split(/\r\n|\n|\r/)
		.filter((line) => line.trim().length > 0)
		.map((line) => JSON.parse(line) as Record<string, unknown>);
}

function signedRecord(
	key: Buffer,
	previous: string,
	body: Record<string, unknown>,
): Record<string, unknown> {
	const serialized = JSON.stringify(body);
	const hmac = createHmac("sha256", key)
		.update(previous + serialized)
		.digest("hex");
	return { ...body, hmac };
}

function assertValidChain(jsonlPath: string, key: Buffer): void {
	let previous = ZERO_HASH;
	for (const record of readRecords(jsonlPath)) {
		const recordedHmac = record.hmac;
		expect(record.prev_hash).toBe(previous);
		expect(recordedHmac).toMatch(/^[0-9a-f]{64}$/);
		const withoutHmac = { ...record };
		delete withoutHmac.hmac;
		const expected = createHmac("sha256", key)
			.update(previous + JSON.stringify(withoutHmac))
			.digest("hex");
		expect(recordedHmac).toBe(expected);
		previous = recordedHmac as string;
	}
}

function writePythonSealedHead(stateDir: string): AuditPaths {
	const paths = auditPaths(stateDir);
	mkdirSync(paths.auditDir, { recursive: true });
	const key = Buffer.alloc(32, 7);
	writeFileSync(paths.keyPath, key);
	const first = signedRecord(key, ZERO_HASH, {
		timestamp_iso: "2026-07-18T00:00:00+00:00",
		sequence: 1,
		kind: "python",
		relative_path: "notes/python.md",
		prev_hash: ZERO_HASH,
	});
	writeFileSync(paths.jsonlPath, `${JSON.stringify(first)}\n`, "utf8");
	const sealBody = {
		version: 1,
		day: paths.day,
		sequence: 1,
		head_hmac: first.hmac,
		sealed_at: "2026-07-18T00:00:01+00:00",
	};
	const sealHmac = createHmac("sha256", key)
		.update(SEAL_DOMAIN)
		.update(JSON.stringify(sealBody))
		.digest("hex");
	writeFileSync(
		paths.sealPath,
		`${JSON.stringify({ ...sealBody, seal_hmac: sealHmac })}\n`,
		"utf8",
	);
	return paths;
}

function runChildAppend(stateDir: string, index: number): Promise<void> {
	const auditUrl = pathToFileURL(join(PACKAGE_ROOT, "src", "audit.ts")).href;
	const source = [
		`import { appendAuditRecord } from ${JSON.stringify(auditUrl)};`,
		`const ok = appendAuditRecord(${JSON.stringify(stateDir)}, ${JSON.stringify(`notes/${index}.md`)}, 1);`,
		"if (!ok) process.exitCode = 1;",
	].join("\n");

	return new Promise((resolve, reject) => {
		const child = spawn(
			process.execPath,
			["--import", "tsx", "--input-type=module", "-e", source],
			{ cwd: PACKAGE_ROOT, stdio: ["ignore", "ignore", "pipe"] },
		);
		let stderr = "";
		child.stderr.setEncoding("utf8");
		child.stderr.on("data", (chunk: string) => {
			stderr += chunk;
		});
		child.once("error", reject);
		child.once("close", (code) => {
			if (code === 0) {
				resolve();
			} else {
				reject(new Error(`audit child ${index} exited ${code}: ${stderr}`));
			}
		});
	});
}

afterEach(() => {
	vi.restoreAllMocks();
	vi.doUnmock("node:fs");
});

describe("audit append integrity and fault handling", () => {
	it("rejects a malformed persisted HMAC key without replacing it", () => {
		const stateDir = freshStateDir("bad-key");
		const { keyPath } = auditPaths(stateDir);
		const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		writeFileSync(keyPath, Buffer.alloc(7, 1));

		expect(appendAuditRecord(stateDir, "notes/private.md", 1)).toBe(false);
		expect(readFileSync(keyPath)).toEqual(Buffer.alloc(7, 1));
		expect(warn).toHaveBeenCalledWith(expect.stringContaining("expected 32"));
	});

	it("returns false when the state path cannot contain an audit directory", () => {
		const parent = freshStateDir("state-file");
		const stateFile = join(parent, "state-file");
		const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		writeFileSync(stateFile, "not a directory", "utf8");

		expect(appendAuditRecord(stateFile, "notes/private.md", 1)).toBe(false);
		expect(warn).toHaveBeenCalled();
	});

	it("accepts an empty chain and starts from ZERO_HASH", () => {
		const stateDir = freshStateDir("empty");
		const paths = auditPaths(stateDir);
		mkdirSync(paths.auditDir, { recursive: true });
		writeFileSync(paths.keyPath, Buffer.alloc(32, 9));
		writeFileSync(paths.jsonlPath, "", "utf8");

		expect(appendAuditRecord(stateDir, "notes/private.md", 2)).toBe(true);
		expect(readRecords(paths.jsonlPath)[0]).toEqual(
			expect.objectContaining({
				relative_path: "notes/private.md",
				redactions_applied: 2,
				prev_hash: ZERO_HASH,
			}),
		);
		assertValidChain(paths.jsonlPath, Buffer.alloc(32, 9));
	});

	it("fails closed for malformed, forged, and discontinuous existing chains", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		const key = Buffer.alloc(32, 5);
		const validFirst = signedRecord(key, ZERO_HASH, {
			timestamp_iso: "2026-07-18T00:00:00.000Z",
			relative_path: "notes/first.md",
			redactions_applied: 1,
			prev_hash: ZERO_HASH,
		});
		const badSecond = signedRecord(key, ZERO_HASH, {
			timestamp_iso: "2026-07-18T00:00:01.000Z",
			relative_path: "notes/second.md",
			redactions_applied: 1,
			prev_hash: ZERO_HASH,
		});
		const cases = [
			"{not-json}\n",
			`${JSON.stringify({ hmac: "short" })}\n`,
			`${JSON.stringify({ ...validFirst, hmac: "a".repeat(64) })}\n`,
			`${JSON.stringify(validFirst)}\n${JSON.stringify(badSecond)}\n`,
		];

		for (const [index, existing] of cases.entries()) {
			const stateDir = freshStateDir(`bad-chain-${index}`);
			const paths = auditPaths(stateDir);
			mkdirSync(paths.auditDir, { recursive: true });
			writeFileSync(paths.keyPath, key);
			writeFileSync(paths.jsonlPath, existing, "utf8");

			expect(appendAuditRecord(stateDir, "notes/must-not-append.md", 1)).toBe(
				false,
			);
			expect(readFileSync(paths.jsonlPath, "utf8")).toBe(existing);
		}
		expect(warn).toHaveBeenCalledTimes(cases.length);
	});

	it("preserves a valid no-newline chain and appends with a separator", () => {
		const stateDir = freshStateDir("no-newline");
		const paths = auditPaths(stateDir);
		const key = Buffer.alloc(32, 3);
		mkdirSync(paths.auditDir, { recursive: true });
		writeFileSync(paths.keyPath, key);
		const first = signedRecord(key, ZERO_HASH, {
			timestamp_iso: "2026-07-18T00:00:00.000Z",
			relative_path: "notes/first.md",
			redactions_applied: 1,
			prev_hash: ZERO_HASH,
		});
		writeFileSync(paths.jsonlPath, JSON.stringify(first), "utf8");

		expect(appendAuditRecord(stateDir, "notes/second.md", 2)).toBe(true);
		expect(readRecords(paths.jsonlPath)).toHaveLength(2);
		assertValidChain(paths.jsonlPath, key);
	});

	it("advances a valid Python seal with a sequenced TypeScript record", () => {
		const stateDir = freshStateDir("python-seal");
		const paths = writePythonSealedHead(stateDir);

		expect(appendAuditRecord(stateDir, "notes/typescript.md", 2)).toBe(true);

		const records = readRecords(paths.jsonlPath);
		expect(records).toHaveLength(2);
		expect(records[0]?.sequence).toBe(1);
		expect(records[1]?.sequence).toBe(2);
		expect(records[1]?.prev_hash).toBe(records[0]?.hmac);
		const seal = JSON.parse(readFileSync(paths.sealPath, "utf8")) as Record<
			string,
			unknown
		>;
		expect(seal.sequence).toBe(2);
		expect(seal.head_hmac).toBe(records[1]?.hmac);
		assertValidChain(paths.jsonlPath, readFileSync(paths.keyPath));
	});

	it("detects deletion of the latest TypeScript record", () => {
		const stateDir = freshStateDir("typescript-tail-truncation");
		const paths = auditPaths(stateDir);
		expect(appendAuditRecord(stateDir, "notes/first.md", 1)).toBe(true);
		expect(appendAuditRecord(stateDir, "notes/second.md", 1)).toBe(true);
		const lines = readFileSync(paths.jsonlPath, "utf8").trim().split("\n");
		writeFileSync(paths.jsonlPath, `${lines[0]}\n`, "utf8");

		expect(appendAuditRecord(stateDir, "notes/must-not-append.md", 1)).toBe(
			false,
		);
		expect(readRecords(paths.jsonlPath)).toHaveLength(1);
	});

	it("rejects a missing seal on a Python-sequenced chain", () => {
		const stateDir = freshStateDir("missing-seal");
		const paths = writePythonSealedHead(stateDir);
		const originalChain = readFileSync(paths.jsonlPath, "utf8");
		rmSync(paths.sealPath);

		expect(appendAuditRecord(stateDir, "notes/must-not-append.md", 1)).toBe(
			false,
		);
		expect(readFileSync(paths.jsonlPath, "utf8")).toBe(originalChain);
	});

	it("rejects tail truncation below the sealed Python head", () => {
		const stateDir = freshStateDir("truncated-seal");
		const paths = writePythonSealedHead(stateDir);
		writeFileSync(paths.jsonlPath, "", "utf8");

		expect(appendAuditRecord(stateDir, "notes/must-not-append.md", 1)).toBe(
			false,
		);
		expect(readFileSync(paths.jsonlPath, "utf8")).toBe("");
	});

	it("times out on a fresh foreign lock without deleting it", () => {
		const stateDir = freshStateDir("fresh-lock");
		const paths = auditPaths(stateDir);
		mkdirSync(paths.auditDir, { recursive: true });
		writeFileSync(paths.lockPath, "foreign-owner", "utf8");

		expect(appendAuditRecord(stateDir, "notes/locked.md", 1)).toBe(false);
		expect(readFileSync(paths.lockPath, "utf8")).toBe("foreign-owner");
		expect(existsSync(paths.jsonlPath)).toBe(false);
	}, 10_000);

	it("recovers a stale crash lock and removes only its own replacement", () => {
		const stateDir = freshStateDir("stale-lock");
		const paths = auditPaths(stateDir);
		mkdirSync(paths.auditDir, { recursive: true });
		writeFileSync(paths.lockPath, "crashed-owner", "utf8");
		const stale = new Date(Date.now() - 15_000);
		utimesSync(paths.lockPath, stale, stale);

		expect(appendAuditRecord(stateDir, "notes/recovered.md", 1)).toBe(true);
		expect(existsSync(paths.lockPath)).toBe(false);
		assertValidChain(paths.jsonlPath, readFileSync(paths.keyPath));
	});

	it("treats transient Windows EACCES as lock contention and retries", async () => {
		const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
		let injected = false;
		vi.doMock("node:fs", () => ({
			...actual,
			openSync: (...args: Parameters<typeof actual.openSync>) => {
				if (!injected && String(args[0]).endsWith(".lock")) {
					injected = true;
					const error = new Error(
						"injected Windows contention",
					) as NodeJS.ErrnoException;
					error.code = "EACCES";
					throw error;
				}
				return Reflect.apply(actual.openSync, actual, args);
			},
		}));
		const mocked = await import("../src/audit.js?eacces-contention");
		const stateDir = freshStateDir("eacces");

		expect(mocked.appendAuditRecord(stateDir, "notes/retried.md", 1)).toBe(
			true,
		);
		expect(injected).toBe(true);
	});

	it("does not unlink a lock whose ownership token changed", async () => {
		const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
		let replaced = false;
		vi.doMock("node:fs", () => ({
			...actual,
			readFileSync: (...args: Parameters<typeof actual.readFileSync>) => {
				if (!replaced && String(args[0]).endsWith(".lock")) {
					replaced = true;
					actual.writeFileSync(args[0], "replacement-owner", "utf8");
				}
				return Reflect.apply(actual.readFileSync, actual, args);
			},
		}));
		const mocked = await import("../src/audit.js?token-ownership");
		const stateDir = freshStateDir("token-owner");
		const paths = auditPaths(stateDir);

		expect(mocked.appendAuditRecord(stateDir, "notes/owned.md", 1)).toBe(true);
		expect(replaced).toBe(true);
		expect(readFileSync(paths.lockPath, "utf8")).toBe("replacement-owner");
	});

	it("serializes concurrent process appends without loss or chain breaks", async () => {
		const stateDir = freshStateDir("process-concurrency");
		await Promise.all(
			Array.from({ length: 12 }, (_, index) => runChildAppend(stateDir, index)),
		);
		const paths = auditPaths(stateDir);
		const records = readRecords(paths.jsonlPath);
		expect(records).toHaveLength(12);
		expect(new Set(records.map((record) => record.relative_path)).size).toBe(
			12,
		);
		assertValidChain(paths.jsonlPath, readFileSync(paths.keyPath));
		expect(existsSync(paths.lockPath)).toBe(false);
	}, 20_000);
});
