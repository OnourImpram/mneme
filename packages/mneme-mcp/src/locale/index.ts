/**
 * Locale profile registry (4.0).
 *
 * Before 4.0 the query path hard-coded a single profile string, `tr-cldr`,
 * and rejected any index built with a different normalizer. That made the
 * Turkish fold mandatory for every vault in every language.
 *
 * The direction is now inverted: the INDEX declares which profile built it
 * (`index_meta.normalization_profile`), and the query path resolves that id
 * here to obtain the matching normalizer. An index and its queries therefore
 * agree by construction, and a vault can be built in any registered locale
 * without touching the query code.
 *
 * A profile id is a wire contract: it is written into the SQLite index and
 * read back by a different process, possibly a different release. Never
 * rename an id — add a new one and migrate by rebuild.
 */

import { normalizeEn, normalizeEnForFts } from "./en.js";
import {
	normalizeTr,
	normalizeTrAsciiFold,
	normalizeTrAsciiFoldForFts,
	normalizeTrForFts,
} from "./tr.js";

/**
 * One normalization strategy, shared by ingest and query.
 *
 * `asciiFold` is the optional second index key. Turkish uses it to bridge
 * dotted/dotless i (a query typed `Izmir` recalls a vault storing `İzmir`);
 * locales without that ambiguity leave it undefined and pay nothing.
 */
export interface LocaleProfile {
	/** Stable wire id persisted in index_meta.normalization_profile. */
	readonly id: string;
	/** BCP-47-ish language code this profile serves. */
	readonly language: string;
	readonly normalize: (s: string) => string;
	readonly normalizeForFts: (s: string) => string;
	readonly asciiFold?: (s: string) => string;
	readonly asciiFoldForFts?: (s: string) => string;
	/** Wire id for the ascii key, persisted as ascii_normalization_profile. */
	readonly asciiProfileId?: string;
}

export const TR_PROFILE: LocaleProfile = {
	id: "tr-cldr",
	language: "tr",
	normalize: normalizeTr,
	normalizeForFts: normalizeTrForFts,
	asciiFold: normalizeTrAsciiFold,
	asciiFoldForFts: normalizeTrAsciiFoldForFts,
	asciiProfileId: "tr-ascii-fold",
};

export const EN_PROFILE: LocaleProfile = {
	id: "en-unicode",
	language: "en",
	normalize: normalizeEn,
	normalizeForFts: normalizeEnForFts,
};

/**
 * Registry keyed by wire id.
 *
 * `identity` is deliberately NOT registered. The indexer can build with no
 * normalizer, but such an index cannot serve locale-sensitive retrieval: a
 * query for "istanbul" would miss a document stored as "İstanbul" because
 * nothing folded case at either end. Leaving it unresolvable makes the search
 * path fail closed and tell the operator to rebuild, which is better than
 * silently answering with case-sensitive matching. Same reasoning excludes
 * `tr-ascii-fold`: it is a secondary recall key, never a primary index
 * profile.
 */
export const LOCALE_PROFILES: ReadonlyMap<string, LocaleProfile> = new Map([
	[TR_PROFILE.id, TR_PROFILE],
	[EN_PROFILE.id, EN_PROFILE],
]);

/** Resolve a profile by its persisted id. Returns undefined when unknown. */
export function profileById(id: string): LocaleProfile | undefined {
	return LOCALE_PROFILES.get(id);
}

/**
 * Comma-separated list of resolvable profile ids, for error messages.
 *
 * A rejection that only says "unknown profile" leaves the operator guessing
 * what to rebuild with; naming the supported set makes the remedy actionable.
 */
export function supportedProfileIds(): string {
	return [...LOCALE_PROFILES.keys()].join(", ");
}
