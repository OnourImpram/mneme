/**
 * mneme_summarize - Topic summary grouped by directory, optionally enriched
 * with a Graphiti 1-hop neighborhood.
 *
 * The FTS5 grouping is always present. When the vault has the KG
 * active flag set and a reachable Neo4j instance, the tool also
 * returns a list of related entities and any source documents the
 * KG knows about for the topic. The output shape stays stable: KG
 * enrichment populates the ``graph_expansion`` flag and the optional
 * ``related_entities`` array.
 */

import { existsSync } from "node:fs";
import { dirname } from "node:path";
import { z } from "zod";
import { ERROR_CODES } from "../errors.js";
import { neutralize } from "../injection.js";
import { normalizeTr } from "../locale/tr.js";
import { buildFts5Query, fts5Search } from "../retrieval/fts5.js";
import {
	closeDriver,
	createDriverFromVault,
	expandTopicNeighborhood,
	type GraphHit,
	isKgActive,
} from "../retrieval/graphiti.js";
import type { VaultConfig } from "../vault/config.js";
import {
	DEFAULT_STOPWORDS,
	isoDateToUnix,
	isoDateToUnixEndOfDay,
	type ToolResult,
} from "./common.js";

export const SummarizeInputSchema = z.object({
	topic: z
		.string()
		.min(1)
		.max(2048)
		.describe("Topic query to summarize."),
	date_range: z
		.tuple([
			z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
			z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
		])
		.describe("Inclusive [from, to] UTC date range in YYYY-MM-DD form.")
		.optional(),
	/** Maximum number of FTS5 docs to walk. KG hits are reported separately. */
	top_k: z
		.number()
		.int()
		.positive()
		.max(50)
		.default(20)
		.describe("Maximum number of FTS5 documents to group."),
	/**
	 * Scope filter. Omit to use config.defaultScope(). Pass "*" for
	 * cross-scope. Skipped when the index lacks the scope column.
	 */
	scope: z
		.string()
		.max(256)
		.describe(
			"Scope to summarize. Omit for the configured default scope. Pass '*' only for an explicit cross-scope query.",
		)
		.optional(),
});

export type SummarizeInput = z.infer<typeof SummarizeInputSchema>;

export interface SummarizeOutput {
	topic: string;
	directories: Record<string, Array<{ path: string; title: string }>>;
	total_docs: number;
	graph_expansion: boolean;
	related_entities?: GraphHit[];
}

export async function summarizeTool(
	args: SummarizeInput,
	vault: VaultConfig,
): Promise<ToolResult<SummarizeOutput>> {
	if (!existsSync(vault.fts5Db)) {
		return {
			ok: false,
			error: {
				code: ERROR_CODES.INDEX_NOT_FOUND,
				message: `FTS5 index not found at ${vault.fts5Db}. Run 'mneme-core index rebuild' first.`,
			},
		};
	}

	const scope = args.scope ?? vault.defaultScope();

	const ftsQuery = buildFts5Query(args.topic, {
		minTokenLength: 2,
		stopwords: DEFAULT_STOPWORDS,
		normalize: normalizeTr,
	});

	const [from, to] = args.date_range ?? [undefined, undefined];

	let hits: ReturnType<typeof fts5Search> = [];
	if (ftsQuery.length > 0) {
		try {
			hits = fts5Search({
				dbPath: vault.fts5Db,
				ftsQuery,
				limit: args.top_k,
				mtimeFrom: from ? isoDateToUnix(from) : undefined,
				mtimeTo: to ? isoDateToUnixEndOfDay(to) : undefined,
				scope,
			});
		} catch (err) {
			return {
				ok: false,
				error: {
					code: ERROR_CODES.IO_ERROR,
					message: err instanceof Error ? err.message : String(err),
				},
			};
		}
	}

	const directories: SummarizeOutput["directories"] = {};
	for (const h of hits) {
		const norm = h.path.replace(/\\/g, "/");
		const dir = dirname(norm);
		if (!directories[dir]) directories[dir] = [];
		// Title is untrusted vault text; defang the fence sentinel (G-3).
		directories[dir].push({ path: norm, title: neutralize(h.title) });
	}

	const kgEnabled = isKgActive(vault);
	let relatedEntities: GraphHit[] = [];
	if (kgEnabled) {
		const driver = await createDriverFromVault(vault);
		if (driver !== null) {
			try {
				relatedEntities = await expandTopicNeighborhood(
					driver,
					args.topic,
					scope,
				);
			} finally {
				await closeDriver(driver);
			}
		}
	}

	return {
		ok: true,
		data: {
			topic: args.topic,
			directories,
			total_docs: hits.length,
			graph_expansion: kgEnabled && relatedEntities.length > 0,
			...(relatedEntities.length > 0
				? { related_entities: relatedEntities }
				: {}),
		},
	};
}
