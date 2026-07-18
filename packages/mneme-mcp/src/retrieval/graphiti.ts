/**
 * Read-only Graphiti / Neo4j adapter.
 *
 * Mirrors the architecture decision in Phase C: the TypeScript MCP
 * server talks directly to its backing stores. For FTS5 this means
 * better-sqlite3 against the indexer's SQLite file; for the KG this
 * means neo4j-driver against the operator's Graphiti instance.
 * There is no Python <-> TS bridge.
 *
 * The KG leg is gated by ``vault.kgActiveFlag``. When the flag is
 * absent (lite, standard, or full-but-not-bootstrapped profile), all
 * exported helpers return ``null`` or an empty result without raising.
 * This keeps the v1.0 lite install path totally side-effect free.
 *
 * Neo4j driver acquisition uses a dynamic import. Bundlers that ship
 * the mneme-mcp package will include neo4j-driver because it is
 * listed in the manifest, but importing this module does not eagerly
 * load the driver. ``createDriverFromVault`` performs the import the
 * first time it is asked for a driver.
 *
 * The bi-temporal model follows Graphiti conventions:
 *   - Entity nodes carry ``name`` and ``summary``.
 *   - Episode nodes carry ``name``, ``content``, ``valid_at``, and
 *     ``reference_time``.
 *   - RELATES_TO edges carry ``fact``, ``valid_at``, and ``invalid_at``.
 *
 * Queries here aim for **safety over completeness**: substring match
 * on Entity.name, capped result sets, explicit driver close. The TS
 * tools wrap this module, decide whether to call it, and merge with
 * FTS5 results.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { wrapUntrusted } from "../injection.js";
import { redact } from "../privacy.js";
import { DEFAULT_SCOPE, ScopeSchema } from "../scope.js";
import type { VaultConfig } from "../vault/config.js";

export interface Neo4jCredentials {
	bolt_url: string;
	user: string;
	password: string;
}

/** Minimal subset of neo4j.Driver the rest of this module uses. */
export interface Neo4jDriverLike {
	session(): {
		run(
			query: string,
			params?: Record<string, unknown>,
		): Promise<{
			records: Array<{
				get(key: string): unknown;
				keys: string[];
			}>;
		}>;
		close(): Promise<void>;
	};
	close(): Promise<void>;
}

export interface GraphHit {
	entity: string;
	summary: string;
	/** Doc path inferred from MENTIONS edge target, if any. */
	source_doc?: string;
}

export interface TimelineFact {
	/** Entity name acting as the subject. */
	subject: string;
	/** Free-form fact text from the RELATES_TO edge. */
	fact: string;
	/** Object entity name, if the fact resolves to a binary relation. */
	object?: string;
	/** ISO timestamp the fact became true. */
	valid_at?: string;
	/** ISO timestamp the fact stopped being true. ``null`` means still valid. */
	invalid_at?: string | null;
	/** Source episode reference time, useful as a tiebreaker for sort. */
	reference_time?: string;
	/** Transaction-time start recorded by Graphiti. */
	created_at?: string;
	/** Transaction-time end when this edge was superseded. */
	expired_at?: string | null;
	/** Graphiti isolation group that owns the relationship. */
	group_id?: string;
}

export interface TimelineQueryOptions {
	/** Inclusive lower bound for overlap with the valid-time interval. */
	validFrom?: string;
	/** Exclusive upper bound for overlap with the valid-time interval. */
	validToExclusive?: string;
	/** Transaction-time snapshot. */
	asOf?: string;
	/** Concrete vault scope. Only "*" disables scope filtering. */
	scope?: string;
}

export interface TimelineQueryResult {
	facts: TimelineFact[];
	asOfApplied: boolean;
	querySucceeded: boolean;
}

const MAX_GRAPH_HITS = 25;
const MAX_TIMELINE_FACTS = 50;
const DEFAULT_GROUP_ID = "mneme-temporal";

function normalizeScope(scope: string | undefined): string {
	const parsed = ScopeSchema.safeParse(scope ?? DEFAULT_SCOPE);
	if (!parsed.success) throw new Error("scope is not a valid read selector");
	return parsed.data;
}

export function graphitiGroupId(scope: string): string {
	const normalized = normalizeScope(scope);
	if (normalized === "*") {
		throw new Error("'*' is a cross-scope read marker, not a writable scope");
	}
	if (normalized === DEFAULT_SCOPE) return DEFAULT_GROUP_ID;

	// Graphiti accepts only ASCII alphanumerics, dashes, and underscores in
	// group_id. Hashing the UTF-8 scope keeps arbitrary printable vault scopes
	// deterministic, collision-resistant, and identical across Python and TS.
	const digest = createHash("sha256").update(normalized, "utf8").digest("hex");
	return `mneme-${digest}`;
}

function scopePredicate(
	alias: string,
	requestedScope: string | undefined,
): string {
	const scope = normalizeScope(requestedScope);
	if (scope === "*") return "";
	return scope === DEFAULT_SCOPE
		? `(${alias}.group_id = $groupId OR ${alias}.group_id IS NULL OR ${alias}.group_id = '')`
		: `${alias}.group_id = $groupId`;
}

function scopeParams(
	requestedScope: string | undefined,
): Record<string, string> {
	const scope = normalizeScope(requestedScope);
	if (scope === "*") return {};
	return { groupId: graphitiGroupId(scope) };
}

function redactedOptional(value: unknown): string | undefined {
	if (value === null || value === undefined) return undefined;
	return redact(String(value)).text;
}

/** True when the operator has flipped on the KG leg for this vault. */
export function isKgActive(vault: VaultConfig): boolean {
	try {
		return (
			existsSync(vault.kgActiveFlag) && statSync(vault.kgActiveFlag).isFile()
		);
	} catch {
		return false;
	}
}

export function readCredentials(vault: VaultConfig): Neo4jCredentials | null {
	try {
		if (!existsSync(vault.kgCredentialsPath)) return null;
		const raw = readFileSync(vault.kgCredentialsPath, "utf8");
		const parsed = JSON.parse(raw) as Partial<Neo4jCredentials>;
		if (
			!parsed.bolt_url ||
			!parsed.user ||
			typeof parsed.password !== "string"
		) {
			return null;
		}
		return {
			bolt_url: String(parsed.bolt_url),
			user: String(parsed.user),
			password: String(parsed.password),
		};
	} catch {
		return null;
	}
}

/**
 * Build a driver from the vault's credentials file. Returns ``null``
 * when KG is inactive, credentials are missing or unreadable, or the
 * driver fails to instantiate. The caller is responsible for closing
 * the returned driver.
 */
export async function createDriverFromVault(
	vault: VaultConfig,
): Promise<Neo4jDriverLike | null> {
	if (!isKgActive(vault)) return null;
	const creds = readCredentials(vault);
	if (creds === null) return null;

	// The neo4j-driver package is an optional runtime dep (full profile
	// only). We import it dynamically and type-erase the result so the
	// mneme-mcp build does not require the package at typecheck time.
	// ``Neo4jDriverLike`` defines the minimal surface the rest of this
	// module uses.
	let neo4j: {
		driver: (...args: unknown[]) => unknown;
		auth: { basic: (u: string, p: string) => unknown };
	};
	try {
		// Use a variable so the bundler does not pre-resolve the spec.
		// neo4j-driver is listed in optionalDependencies; it is absent in
		// lite/standard installs. When the import fails we throw an
		// actionable error so the operator knows exactly what is missing,
		// rather than silently returning null and leaving the KG inactive
		// with no explanation.
		const moduleSpecifier: string = "neo4j-driver";
		neo4j = (await import(moduleSpecifier)) as typeof neo4j;
	} catch {
		throw new Error(
			"[mneme-mcp] neo4j-driver is not installed. " +
				"The full-profile KG leg requires it: " +
				"run `npm install neo4j-driver` (or the equivalent for your " +
				"package manager) in the mneme-mcp package directory, then restart " +
				"the MCP server.",
		);
	}

	try {
		const driver = neo4j.driver(
			creds.bolt_url,
			neo4j.auth.basic(creds.user, creds.password),
		);
		return driver as Neo4jDriverLike;
	} catch {
		return null;
	}
}

/**
 * Find entities whose name contains the topic and return their
 * 1-hop neighbors. The query is capped at ``MAX_GRAPH_HITS`` results.
 *
 * Returns an empty array if any Cypher error occurs; callers treat
 * the KG leg as best-effort.
 */
export async function expandTopicNeighborhood(
	driver: Neo4jDriverLike,
	topic: string,
	scope?: string,
): Promise<GraphHit[]> {
	const redactedTopic = redact(topic).text;
	if (redactedTopic.trim().length === 0) return [];
	const session = driver.session();
	try {
		const entityScope = scopePredicate("e", scope);
		const episodeScope = scopePredicate("ep", scope);
		const params: Record<string, unknown> = {
			topic: redactedTopic,
			limit: MAX_GRAPH_HITS,
			...scopeParams(scope),
		};
		const result = await session.run(
			`
      MATCH (e:Entity)
      WHERE toLower(e.name) CONTAINS toLower($topic)
			${entityScope.length > 0 ? `AND ${entityScope}` : ""}
      OPTIONAL MATCH (e)-[:MENTIONED_IN]->(ep:Episode)
			${episodeScope.length > 0 ? `WHERE ${episodeScope}` : ""}
      RETURN e.name AS entity,
             coalesce(e.summary, '') AS summary,
             ep.source_description AS source_doc
      LIMIT $limit
      `,
			params,
		);
		return result.records.map((r) => ({
			entity: wrapUntrusted(redact(String(r.get("entity"))).text, "kg-entity"),
			summary: wrapUntrusted(
				redact(String(r.get("summary") ?? "")).text,
				"kg-entity",
			),
			source_doc: redactedOptional(r.get("source_doc")),
		}));
	} catch {
		return [];
	} finally {
		await session.close().catch(() => undefined);
	}
}

/**
 * Walk RELATES_TO edges anchored on entities whose name matches the
 * subject and return their bi-temporal facts. Optional valid_from /
 * valid_to / as_of windows scope the result set.
 *
 * The shape returned is intentionally flat so the TS timeline tool
 * can merge it with FTS-mtime entries without further reshaping.
 */
export async function queryTimelineForSubject(
	driver: Neo4jDriverLike,
	subject: string,
	opts: TimelineQueryOptions = {},
): Promise<TimelineQueryResult> {
	const redactedSubject = redact(subject).text;
	if (redactedSubject.trim().length === 0) {
		return { facts: [], asOfApplied: false, querySucceeded: false };
	}
	const session = driver.session();
	try {
		const whereClauses: string[] = [
			"toLower(s.name) CONTAINS toLower($subject)",
		];
		const relationshipScope = scopePredicate("r", opts.scope);
		if (relationshipScope.length > 0) whereClauses.push(relationshipScope);
		if (opts.validFrom !== undefined) {
			whereClauses.push(
				"(r.invalid_at IS NULL OR r.invalid_at > datetime($validFrom))",
			);
		}
		if (opts.validToExclusive !== undefined) {
			whereClauses.push(
				"(r.valid_at IS NULL OR r.valid_at < datetime($validToExclusive))",
			);
		}
		if (opts.asOf !== undefined) {
			whereClauses.push(
				"r.created_at IS NOT NULL AND r.created_at <= datetime($asOf)",
			);
			whereClauses.push(
				"(r.expired_at IS NULL OR r.expired_at > datetime($asOf))",
			);
		} else {
			whereClauses.push("r.expired_at IS NULL");
		}
		const cypher = `
      MATCH (s:Entity)-[r:RELATES_TO]->(o:Entity)
      WHERE ${whereClauses.join(" AND ")}
      RETURN s.name AS subject,
             coalesce(r.fact, '') AS fact,
             o.name AS object,
             toString(r.valid_at) AS valid_at,
             toString(r.invalid_at) AS invalid_at,
			 toString(r.reference_time) AS reference_time,
			 toString(r.created_at) AS created_at,
			 toString(r.expired_at) AS expired_at,
			 r.group_id AS group_id
		ORDER BY coalesce(r.valid_at, r.created_at, datetime({epochMillis: 0})) ASC,
		         coalesce(r.created_at, datetime({epochMillis: 0})) ASC,
		         coalesce(r.uuid, '') ASC,
		         coalesce(s.name, '') ASC,
		         coalesce(o.name, '') ASC,
		         coalesce(r.fact, '') ASC
      LIMIT $limit
    `;
		const params: Record<string, unknown> = {
			subject: redactedSubject,
			limit: MAX_TIMELINE_FACTS,
			...scopeParams(opts.scope),
		};
		if (opts.validFrom !== undefined) params.validFrom = opts.validFrom;
		if (opts.validToExclusive !== undefined) {
			params.validToExclusive = opts.validToExclusive;
		}
		if (opts.asOf !== undefined) params.asOf = opts.asOf;
		const result = await session.run(cypher, params);
		const facts = result.records.map((r) => {
			const validAt = r.get("valid_at");
			const invalidAt = r.get("invalid_at");
			const refTime = r.get("reference_time");
			const createdAt = r.get("created_at");
			const expiredAt = r.get("expired_at");
			const groupId = r.get("group_id");
			return {
				subject: wrapUntrusted(
					redact(String(r.get("subject"))).text,
					"kg-fact",
				),
				fact: wrapUntrusted(
					redact(String(r.get("fact") ?? "")).text,
					"kg-fact",
				),
				object:
					r.get("object") != null
						? wrapUntrusted(redact(String(r.get("object"))).text, "kg-fact")
						: undefined,
				valid_at: redactedOptional(validAt),
				invalid_at: redactedOptional(invalidAt) ?? null,
				reference_time: redactedOptional(refTime),
				created_at: redactedOptional(createdAt),
				expired_at: redactedOptional(expiredAt) ?? null,
				group_id: redactedOptional(groupId),
			};
		});
		return {
			facts,
			asOfApplied: opts.asOf !== undefined,
			querySucceeded: true,
		};
	} catch {
		return { facts: [], asOfApplied: false, querySucceeded: false };
	} finally {
		await session.close().catch(() => undefined);
	}
}

export async function timelineForSubject(
	driver: Neo4jDriverLike,
	subject: string,
	opts: TimelineQueryOptions = {},
): Promise<TimelineFact[]> {
	return (await queryTimelineForSubject(driver, subject, opts)).facts;
}

/** Idempotent close for the dynamically imported driver. */
export async function closeDriver(
	driver: Neo4jDriverLike | null,
): Promise<void> {
	if (driver === null) return;
	try {
		await driver.close();
	} catch {
		// Driver close is best-effort. Mid-test teardown can race with
		// session close; swallow to keep the public surface non-throwing.
	}
}
