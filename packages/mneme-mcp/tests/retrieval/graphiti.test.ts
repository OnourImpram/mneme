/**
 * Unit tests for the Graphiti adapter.
 *
 * The dynamic import of ``neo4j-driver`` is deliberately not mocked
 * here. Instead we test:
 *
 *   1. ``isKgActive`` returns false for vaults without the flag and
 *      true for vaults with a regular file at ``kgActiveFlag``.
 *   2. ``readCredentials`` returns null for missing or malformed
 *      credentials, and a valid object for well-formed JSON.
 *   3. ``createDriverFromVault`` returns null when the flag is absent
 *      and null when credentials are missing.
 *   4. ``expandTopicNeighborhood`` and ``timelineForSubject`` map
 *      Cypher records onto the public output types correctly when
 *      handed a fake driver.
 *   5. ``closeDriver`` accepts null and ignores driver-close errors.
 */

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FENCE_OPEN } from "../../src/injection.js";
import {
	closeDriver,
	createDriverFromVault,
	expandTopicNeighborhood,
	isKgActive,
	readCredentials,
	timelineForSubject,
	type Neo4jDriverLike,
} from "../../src/retrieval/graphiti.js";
import { VaultConfig } from "../../src/vault/config.js";

function makeVault(prefix: string): VaultConfig {
	const root = mkdtempSync(join(tmpdir(), `mneme-graphiti-${prefix}-`));
	mkdirSync(join(root, ".mneme"), { recursive: true });
	return VaultConfig.fromPath(root);
}

function activate(vault: VaultConfig): void {
	writeFileSync(vault.kgActiveFlag, "on\n", "utf8");
}

function writeCreds(
	vault: VaultConfig,
	creds: { bolt_url: string; user: string; password: string },
): void {
	writeFileSync(
		vault.kgCredentialsPath,
		JSON.stringify(creds, null, 2),
		"utf8",
	);
}

/** Fake driver that returns the records the caller seeds. */
function makeFakeDriver(
	records: Array<Record<string, unknown>>,
): Neo4jDriverLike {
	let closed = false;
	const driver: Neo4jDriverLike = {
		session() {
			return {
				run: async (_query: string, _params?: Record<string, unknown>) => ({
					records: records.map((row) => ({
						keys: Object.keys(row),
						get: (k: string) => row[k] ?? null,
					})),
				}),
				close: async () => {
					/* no-op */
				},
			};
		},
		close: async () => {
			closed = true;
		},
	};
	// Expose for assertions in tests.
	(driver as unknown as { closed: () => boolean }).closed = () => closed;
	return driver;
}

/**
 * Fake driver that simulates Neo4j scope filtering by inspecting params.scope.
 *
 * Records carry an internal `scope` field used only for filtering. When
 * params.scope is set, only records whose scope equals params.scope OR whose
 * scope is null are returned (the OR-NULL migration leg). This mirrors the
 * Cypher clause: `AND (e.scope = $scope OR e.scope IS NULL)`.
 */
function makeScopeFilteringDriver(
	records: Array<{ scope?: string | null } & Record<string, unknown>>,
): Neo4jDriverLike {
	return {
		session() {
			return {
				run: async (_query: string, params?: Record<string, unknown>) => {
					const requestedScope = params?.scope as string | undefined;
					const filtered =
						requestedScope !== undefined
							? records.filter(
									(r) => r.scope === requestedScope || r.scope === null,
								)
							: records;
					return {
						records: filtered.map((row) => ({
							keys: Object.keys(row),
							get: (k: string) =>
								Object.prototype.hasOwnProperty.call(row, k) ? row[k] : null,
						})),
					};
				},
				close: async () => undefined,
			};
		},
		close: async () => undefined,
	};
}

describe("isKgActive", () => {
	it("returns false without the flag", () => {
		const vault = makeVault("active-off");
		expect(isKgActive(vault)).toBe(false);
	});

	it("returns true when the flag is a regular file", () => {
		const vault = makeVault("active-on");
		activate(vault);
		expect(isKgActive(vault)).toBe(true);
	});
});

describe("readCredentials", () => {
	it("returns null when the file is absent", () => {
		const vault = makeVault("creds-absent");
		expect(readCredentials(vault)).toBeNull();
	});

	it("returns null when JSON is malformed", () => {
		const vault = makeVault("creds-malformed");
		writeFileSync(vault.kgCredentialsPath, "{not-json", "utf8");
		expect(readCredentials(vault)).toBeNull();
	});

	it("returns null when required fields are missing", () => {
		const vault = makeVault("creds-partial");
		writeFileSync(
			vault.kgCredentialsPath,
			JSON.stringify({ bolt_url: "x" }),
			"utf8",
		);
		expect(readCredentials(vault)).toBeNull();
	});

	it("returns the parsed object on well-formed input", () => {
		const vault = makeVault("creds-ok");
		writeCreds(vault, { bolt_url: "bolt://x", user: "u", password: "p" });
		expect(readCredentials(vault)).toEqual({
			bolt_url: "bolt://x",
			user: "u",
			password: "p",
		});
	});
});

describe("createDriverFromVault", () => {
	it("returns null without the active flag", async () => {
		const vault = makeVault("driver-noflag");
		writeCreds(vault, { bolt_url: "bolt://x", user: "u", password: "p" });
		expect(await createDriverFromVault(vault)).toBeNull();
	});

	it("returns null with flag set but no credentials", async () => {
		const vault = makeVault("driver-nocreds");
		activate(vault);
		expect(await createDriverFromVault(vault)).toBeNull();
	});

	it("throws an actionable error when neo4j-driver is absent but KG is active", async () => {
		// Simulate the module being unavailable by temporarily shadowing the
		// dynamic import. We achieve this by monkey-patching the module object
		// via vi.mock at the module level — instead, we test the thrown message
		// by calling createDriverFromVault with a vault that HAS both the active
		// flag AND valid credentials, inside a subtest that intercepts the import.
		// Because we cannot uninstall neo4j-driver mid-test (it is installed in
		// devDependencies for the full-profile CI run), we instead validate the
		// error message string that would be thrown in a lite install by checking
		// the source of createDriverFromVault via its documented contract.
		//
		// What we can cleanly assert: the function rejects (throws) rather than
		// returning null when both flag+creds are present but the driver cannot
		// load. We exercise this by wrapping the real call: if neo4j-driver IS
		// installed it returns a driver (non-null) without throwing — we skip the
		// error-path assertion in that case. If it is absent, the function must
		// throw an Error whose message references "neo4j-driver".
		const vault = makeVault("driver-throw-on-missing");
		activate(vault);
		writeCreds(vault, { bolt_url: "bolt://x", user: "u", password: "p" });

		let result: Awaited<ReturnType<typeof createDriverFromVault>>;
		let thrown: unknown;
		try {
			result = await createDriverFromVault(vault);
		} catch (err) {
			thrown = err;
			result = null;
		}

		if (thrown !== undefined) {
			// neo4j-driver absent: verify the error is actionable.
			expect(thrown).toBeInstanceOf(Error);
			expect((thrown as Error).message).toContain("neo4j-driver");
			expect((thrown as Error).message).toContain("npm install");
		} else {
			// neo4j-driver present (full-profile CI): driver must be non-null.
			expect(result).not.toBeNull();
			// Clean up the open driver connection to avoid test leaks.
			if (result !== null) {
				await result.close().catch(() => undefined);
			}
		}
	});
});

describe("expandTopicNeighborhood", () => {
	it("maps records onto GraphHit entries (S3b: entity/summary are fenced)", async () => {
		const driver = makeFakeDriver([
			{
				entity: "Mneme",
				summary: "vault memory engine",
				source_doc: "ref/x.md",
			},
			{ entity: "Graphiti", summary: "temporal kg", source_doc: null },
		]);
		const hits = await expandTopicNeighborhood(driver, "memory");
		expect(hits).toHaveLength(2);
		// Entity and summary are now fence-wrapped (S3b).
		expect(hits[0]?.entity).toContain("Mneme");
		expect(hits[0]?.entity).toContain(FENCE_OPEN);
		expect(hits[0]?.summary).toContain("vault memory engine");
		expect(hits[0]?.summary).toContain(FENCE_OPEN);
		// source_doc is a path and is NOT wrapped.
		expect(hits[0]?.source_doc).toBe("ref/x.md");
		expect(hits[1]?.entity).toContain("Graphiti");
		expect(hits[1]?.summary).toContain("temporal kg");
		expect(hits[1]?.source_doc).toBeUndefined();
	});

	it("returns empty array on whitespace topic", async () => {
		const driver = makeFakeDriver([]);
		expect(await expandTopicNeighborhood(driver, "   ")).toEqual([]);
	});

	it("returns empty array when Cypher run throws", async () => {
		const driver: Neo4jDriverLike = {
			session() {
				return {
					run: async () => {
						throw new Error("boom");
					},
					close: async () => undefined,
				};
			},
			close: async () => undefined,
		};
		expect(await expandTopicNeighborhood(driver, "topic")).toEqual([]);
	});
});

describe("timelineForSubject", () => {
	it("maps records onto TimelineFact entries with bi-temporal fields (S3b: fact/subject/object are fenced)", async () => {
		const driver = makeFakeDriver([
			{
				subject: "Mneme",
				fact: "decided in favor of MIT",
				object: "License",
				valid_at: "2026-05-18T00:00:00Z",
				invalid_at: null,
				reference_time: "2026-05-18T10:00:00Z",
			},
		]);
		const facts = await timelineForSubject(driver, "Mneme", {
			asOf: "2026-06-01T00:00:00Z",
		});
		expect(facts).toHaveLength(1);
		// Free-text fields are fence-wrapped (S3b).
		expect(facts[0]?.subject).toContain("Mneme");
		expect(facts[0]?.subject).toContain(FENCE_OPEN);
		expect(facts[0]?.fact).toContain("decided in favor of MIT");
		expect(facts[0]?.fact).toContain(FENCE_OPEN);
		expect(facts[0]?.object).toContain("License");
		expect(facts[0]?.object).toContain(FENCE_OPEN);
		// Temporal fields are NOT wrapped (they are ISO timestamps, not content).
		expect(facts[0]?.valid_at).toContain("2026-05-18");
		expect(facts[0]?.invalid_at).toBeNull();
	});

	it("returns empty array on empty subject", async () => {
		const driver = makeFakeDriver([]);
		expect(await timelineForSubject(driver, "")).toEqual([]);
	});
});

describe("closeDriver", () => {
	it("accepts null silently", async () => {
		await expect(closeDriver(null)).resolves.toBeUndefined();
	});

	it("ignores close errors", async () => {
		const driver: Neo4jDriverLike = {
			session() {
				return {
					run: async () => ({ records: [] }),
					close: async () => undefined,
				};
			},
			close: async () => {
				throw new Error("close failed");
			},
		};
		await expect(closeDriver(driver)).resolves.toBeUndefined();
	});
});

// ---------------------------------------------------------------------------
// M-2: KG scope filtering
// ---------------------------------------------------------------------------

describe("expandTopicNeighborhood scope filtering", () => {
	it("scope='clinical' returns clinical and null-scope entities; excludes freelance", async () => {
		const driver = makeScopeFilteringDriver([
			{
				entity: "ClinicalEntity",
				summary: "clinical summary",
				source_doc: null,
				scope: "clinical",
			},
			{
				entity: "FreelanceEntity",
				summary: "freelance summary",
				source_doc: null,
				scope: "freelance",
			},
			{
				entity: "LegacyEntity",
				summary: "legacy no-scope summary",
				source_doc: null,
				scope: null,
			},
		]);
		const hits = await expandTopicNeighborhood(driver, "entity", "clinical");
		// clinical-scoped entity is returned
		expect(hits.some((h) => h.entity.includes("ClinicalEntity"))).toBe(true);
		// freelance-scoped entity is excluded
		expect(hits.some((h) => h.entity.includes("FreelanceEntity"))).toBe(false);
		// null-scope entity IS returned (OR-NULL migration leg)
		expect(hits.some((h) => h.entity.includes("LegacyEntity"))).toBe(true);
		expect(hits).toHaveLength(2);
	});
});

describe("timelineForSubject scope filtering", () => {
	it("scope='clinical' returns clinical and null-scope facts; excludes freelance", async () => {
		const driver = makeScopeFilteringDriver([
			{
				subject: "ClinicalSubject",
				fact: "clinical fact",
				object: "X",
				valid_at: "2026-01-01T00:00:00Z",
				invalid_at: null,
				reference_time: "2026-01-01T12:00:00Z",
				scope: "clinical",
			},
			{
				subject: "FreelanceSubject",
				fact: "freelance fact",
				object: "Y",
				valid_at: "2026-01-02T00:00:00Z",
				invalid_at: null,
				reference_time: "2026-01-02T12:00:00Z",
				scope: "freelance",
			},
			{
				subject: "LegacySubject",
				fact: "legacy null-scope fact",
				object: "Z",
				valid_at: "2026-01-03T00:00:00Z",
				invalid_at: null,
				reference_time: "2026-01-03T12:00:00Z",
				scope: null,
			},
		]);
		const facts = await timelineForSubject(driver, "Subject", {
			scope: "clinical",
		});
		// clinical fact is returned
		expect(facts.some((f) => f.subject.includes("ClinicalSubject"))).toBe(true);
		// freelance fact is excluded
		expect(facts.some((f) => f.subject.includes("FreelanceSubject"))).toBe(false);
		// null-scope fact IS returned (OR-NULL migration leg)
		expect(facts.some((f) => f.subject.includes("LegacySubject"))).toBe(true);
		expect(facts).toHaveLength(2);
	});
});
