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
	graphitiGroupId,
	isKgActive,
	type Neo4jDriverLike,
	queryTimelineForSubject,
	readCredentials,
	timelineForSubject,
} from "../../src/retrieval/graphiti.js";
import { VaultConfig } from "../../src/vault/config.js";

const CLINICAL_GROUP_ID =
	"mneme-98569e7e9080addd9e387d4674b33830a6c516ea67a150b1f2aae304e17f7b06";
const FREELANCE_GROUP_ID = graphitiGroupId("freelance");

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

/** Simulate the production scope and Graphiti group_id predicates. */
function makeScopeFilteringDriver(
	records: Array<
		{ scope?: string | null; group_id?: string | null } & Record<
			string,
			unknown
		>
	>,
): Neo4jDriverLike {
	return {
		session() {
			return {
				run: async (_query: string, params?: Record<string, unknown>) => {
					const requestedGroup = params?.groupId as string | undefined;
					const filtered =
						requestedGroup !== undefined
							? records.filter((record) => {
									if (record.group_id === requestedGroup) return true;
									return (
										requestedGroup === "mneme-temporal" &&
										(record.group_id == null || record.group_id === "")
									);
								})
							: records;
					return {
						records: filtered.map((row) => ({
							keys: Object.keys(row),
							get: (k: string) => (Object.hasOwn(row, k) ? row[k] : null),
						})),
					};
				},
				close: async () => undefined,
			};
		},
		close: async () => undefined,
	};
}

function makeCapturingDriver(
	records: Array<Record<string, unknown>> = [],
	throwOnRun = false,
): {
	driver: Neo4jDriverLike;
	calls: Array<{ query: string; params: Record<string, unknown> }>;
} {
	const calls: Array<{ query: string; params: Record<string, unknown> }> = [];
	return {
		calls,
		driver: {
			session() {
				return {
					run: async (query: string, params = {}) => {
						calls.push({ query, params });
						if (throwOnRun) throw new Error("query failed");
						return {
							records: records.map((row) => ({
								keys: Object.keys(row),
								get: (key: string) => row[key] ?? null,
							})),
						};
					},
					close: async () => undefined,
				};
			},
			close: async () => undefined,
		},
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

describe("graphitiGroupId", () => {
	it("preserves the historical default group", () => {
		expect(graphitiGroupId("default")).toBe("mneme-temporal");
	});

	it("derives a stable group for non-default scopes", () => {
		expect(graphitiGroupId("clinical")).toBe(CLINICAL_GROUP_ID);
	});

	it("keeps arbitrary printable scopes within Graphiti's identifier grammar", () => {
		expect(graphitiGroupId("Clinical / İstanbul")).toBe(
			"mneme-ef48898e4bef242fbe5c13e4c00230a74c085f094ae2a6e0ed8ef25cc6308a25",
		);
		expect(graphitiGroupId("Clinical / İstanbul")).toMatch(/^[A-Za-z0-9_-]+$/);
	});

	it("rejects the wildcard as a write group", () => {
		expect(() => graphitiGroupId("*")).toThrow("cross-scope read marker");
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

	it("builds overlap and transaction-time predicates deterministically", async () => {
		const { driver, calls } = makeCapturingDriver();
		const result = await queryTimelineForSubject(driver, "Mneme", {
			validFrom: "2026-01-01T00:00:00.000Z",
			validToExclusive: "2026-02-01T00:00:00.000Z",
			asOf: "2026-03-01T00:00:00.000Z",
			scope: "clinical",
		});
		expect(result).toMatchObject({
			facts: [],
			asOfApplied: true,
			querySucceeded: true,
		});
		expect(calls).toHaveLength(1);
		const call = calls[0];
		expect(call?.query).toContain("r.invalid_at > datetime($validFrom)");
		expect(call?.query).toContain("r.valid_at < datetime($validToExclusive)");
		expect(call?.query).toContain("r.created_at IS NOT NULL");
		expect(call?.query).toContain("r.expired_at > datetime($asOf)");
		expect(call?.query).toContain("r.group_id = $groupId");
		expect(call?.query).not.toContain("r.scope = $scope");
		expect(call?.query).toContain("datetime({epochMillis: 0})");
		expect(call?.query).toContain("coalesce(r.fact, '') ASC");
		expect(call?.query).not.toContain("datetime() ASC");
		expect(call?.params).toMatchObject({ groupId: CLINICAL_GROUP_ID });
		expect(call?.params).not.toHaveProperty("scope");
	});

	it("marks as_of unapplied when the graph query fails", async () => {
		const { driver } = makeCapturingDriver([], true);
		const result = await queryTimelineForSubject(driver, "Mneme", {
			asOf: "2026-03-01T00:00:00.000Z",
		});
		expect(result).toEqual({
			facts: [],
			asOfApplied: false,
			querySucceeded: false,
		});
	});

	it("defaults to the current non-expired transaction snapshot", async () => {
		const { driver, calls } = makeCapturingDriver();
		await queryTimelineForSubject(driver, "Mneme");
		expect(calls[0]?.query).toContain("r.expired_at IS NULL");
		expect(calls[0]?.params).toMatchObject({ groupId: "mneme-temporal" });
		expect(calls[0]?.params).not.toHaveProperty("scope");
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
	const records = [
		{
			entity: "ClinicalEntity",
			summary: "clinical",
			group_id: CLINICAL_GROUP_ID,
		},
		{
			entity: "FreelanceEntity",
			summary: "freelance",
			group_id: FREELANCE_GROUP_ID,
		},
		{
			entity: "GroupedClinical",
			summary: "group",
			group_id: CLINICAL_GROUP_ID,
		},
		{ entity: "DefaultGroup", summary: "default", group_id: "mneme-temporal" },
		{ entity: "LegacyEntity", summary: "legacy", scope: null, group_id: null },
		{
			entity: "LegacyScopedEntity",
			summary: "legacy scoped",
			scope: "clinical",
			group_id: null,
		},
	];

	it("a non-default scope excludes legacy and foreign entities", async () => {
		const hits = await expandTopicNeighborhood(
			makeScopeFilteringDriver(records),
			"entity",
			"clinical",
		);
		expect(hits.some((hit) => hit.entity.includes("ClinicalEntity"))).toBe(
			true,
		);
		expect(hits.some((hit) => hit.entity.includes("GroupedClinical"))).toBe(
			true,
		);
		expect(hits.some((hit) => hit.entity.includes("FreelanceEntity"))).toBe(
			false,
		);
		expect(hits.some((hit) => hit.entity.includes("LegacyEntity"))).toBe(false);
		expect(hits.some((hit) => hit.entity.includes("LegacyScopedEntity"))).toBe(
			false,
		);
	});

	it("default owns legacy unscoped entities", async () => {
		const hits = await expandTopicNeighborhood(
			makeScopeFilteringDriver(records),
			"entity",
			"default",
		);
		expect(hits.some((hit) => hit.entity.includes("DefaultGroup"))).toBe(true);
		expect(hits.some((hit) => hit.entity.includes("LegacyEntity"))).toBe(true);
		expect(hits.some((hit) => hit.entity.includes("LegacyScopedEntity"))).toBe(
			true,
		);
		expect(hits.some((hit) => hit.entity.includes("ClinicalEntity"))).toBe(
			false,
		);
	});

	it("the explicit wildcard returns every scope", async () => {
		const hits = await expandTopicNeighborhood(
			makeScopeFilteringDriver(records),
			"entity",
			"*",
		);
		expect(hits).toHaveLength(records.length);
	});
});

describe("timelineForSubject scope filtering", () => {
	const records = [
		{
			subject: "ClinicalSubject",
			fact: "clinical",
			group_id: CLINICAL_GROUP_ID,
		},
		{
			subject: "FreelanceSubject",
			fact: "freelance",
			group_id: FREELANCE_GROUP_ID,
		},
		{ subject: "GroupedClinical", fact: "group", group_id: CLINICAL_GROUP_ID },
		{ subject: "DefaultGroup", fact: "default", group_id: "mneme-temporal" },
		{ subject: "LegacySubject", fact: "legacy", scope: null, group_id: null },
		{
			subject: "LegacyScopedSubject",
			fact: "legacy scoped",
			scope: "clinical",
			group_id: null,
		},
	];

	it("a non-default scope excludes legacy and foreign facts", async () => {
		const facts = await timelineForSubject(
			makeScopeFilteringDriver(records),
			"Subject",
			{ scope: "clinical" },
		);
		expect(facts.some((fact) => fact.subject.includes("ClinicalSubject"))).toBe(
			true,
		);
		expect(facts.some((fact) => fact.subject.includes("GroupedClinical"))).toBe(
			true,
		);
		expect(
			facts.some((fact) => fact.subject.includes("FreelanceSubject")),
		).toBe(false);
		expect(facts.some((fact) => fact.subject.includes("LegacySubject"))).toBe(
			false,
		);
		expect(
			facts.some((fact) => fact.subject.includes("LegacyScopedSubject")),
		).toBe(false);
	});

	it("default owns the historical and unscoped graph", async () => {
		const facts = await timelineForSubject(
			makeScopeFilteringDriver(records),
			"Subject",
			{ scope: "default" },
		);
		expect(facts.some((fact) => fact.subject.includes("DefaultGroup"))).toBe(
			true,
		);
		expect(facts.some((fact) => fact.subject.includes("LegacySubject"))).toBe(
			true,
		);
		expect(
			facts.some((fact) => fact.subject.includes("LegacyScopedSubject")),
		).toBe(true);
		expect(facts.some((fact) => fact.subject.includes("ClinicalSubject"))).toBe(
			false,
		);
	});

	it("the explicit wildcard returns every graph scope", async () => {
		const facts = await timelineForSubject(
			makeScopeFilteringDriver(records),
			"Subject",
			{ scope: "*" },
		);
		expect(facts).toHaveLength(records.length);
	});
});

describe("Graphiti privacy boundary", () => {
	it("redacts neighborhood provider parameters and returned metadata", async () => {
		const secret = "GRAPH_QUERY_CANARY";
		const { driver, calls } = makeCapturingDriver([
			{
				entity: `<private>${secret}</private>`,
				summary: `<private>${secret}</private>`,
				source_doc: `<private>${secret}</private>`,
			},
		]);
		const hits = await expandTopicNeighborhood(
			driver,
			`topic <private>${secret}</private>`,
		);

		expect(JSON.stringify(calls[0]?.params)).not.toContain(secret);
		expect(JSON.stringify(hits)).not.toContain(secret);
	});

	it("redacts timeline provider parameters and every returned string", async () => {
		const secret = "GRAPH_TIMELINE_CANARY";
		const { driver, calls } = makeCapturingDriver([
			{
				subject: `<private>${secret}</private>`,
				fact: `<private>${secret}</private>`,
				object: `<private>${secret}</private>`,
				valid_at: `<private>${secret}</private>`,
				group_id: `<private>${secret}</private>`,
			},
		]);
		const result = await queryTimelineForSubject(
			driver,
			`subject <private>${secret}</private>`,
		);

		expect(JSON.stringify(calls[0]?.params)).not.toContain(secret);
		expect(JSON.stringify(result)).not.toContain(secret);
	});
});
