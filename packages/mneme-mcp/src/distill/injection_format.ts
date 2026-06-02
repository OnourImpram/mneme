/**
 * Three injection format levels for retrieval results.
 *
 * Mirrors ``mneme_core.distill.compressed_format`` (Python) exactly:
 * same threshold constant, same 4-row decision table, same render shapes.
 *
 * ``InjectionFormat`` — string union so it serialises transparently to JSON
 * and matches the Python StrEnum values without an import.
 *
 * ``selectFormat`` picks the appropriate level from two signals:
 *   1. Has the doc already been injected this session?
 *   2. How tight is the context budget?
 *
 * ``renderInjection`` takes a minimal doc record and emits the right
 * markdown shape for the chosen level.
 *
 * No imports from prime.ts or tracker — fully self-contained.
 * Core Invariant 2 compliance: no network, no LLM calls anywhere here.
 */

/**
 * The three compression levels for injected vault content.
 *
 * ``full``      — complete markdown section, first injection at low pressure.
 * ``keypoints`` — bullet summary + path reference, re-injection or high pressure.
 * ``ref``       — single ``see vault://path`` line, re-injection at high pressure.
 */
export type InjectionFormat = "full" | "keypoints" | "ref";

/**
 * Context pressure at or above this value is considered "high pressure".
 * Mirrors ``HIGH_PRESSURE_THRESHOLD`` in ``compressed_format.py``.
 */
export const HIGH_PRESSURE_THRESHOLD = 0.75;

/**
 * Pick a format level from two binary signals.
 *
 * Decision table (mirrors Python ``select_format``):
 *
 * | hasBeenInjected | contextPressure >= 0.75 | result      |
 * |-----------------|------------------------|-------------|
 * | false           | false                  | 'full'      |
 * | false           | true                   | 'keypoints' |
 * | true            | false                  | 'keypoints' |
 * | true            | true                   | 'ref'       |
 *
 * ``contextPressure`` is clamped to [0.0, 1.0] before comparison.
 */
export function selectFormat(
	hasBeenInjected: boolean,
	contextPressure: number,
): InjectionFormat {
	const pressure = Math.max(0.0, Math.min(1.0, contextPressure));
	const highPressure = pressure >= HIGH_PRESSURE_THRESHOLD;
	if (hasBeenInjected) {
		return highPressure ? "ref" : "keypoints";
	}
	return highPressure ? "keypoints" : "full";
}

/**
 * Minimal record the injector hands to ``renderInjection``.
 *
 * ``keyPoints`` is an already-summarized bullet list. Mneme does not
 * summarize on the fly during injection; the indexer extracts these
 * once at index time so injection stays deterministic (mirrors Python
 * ``InjectionInput``). When empty, the keypoints level falls back to
 * a plain ``See \`path\`.`` line.
 */
export interface InjectionDoc {
	path: string;
	title: string;
	body: string;
	keyPoints: string[];
}

/**
 * Render the markdown payload for a given format level.
 *
 * Mirrors Python ``render_injection`` exactly:
 *
 * - ``full``: ``## {title}\n\n{body.trim()}\n``
 *   (empty body omits the body paragraph)
 * - ``keypoints`` with points: ``## {title}\n\n{bullets}\n\nSee \`{path}\`.\n``
 * - ``keypoints`` without points: ``## {title}\n\nSee \`{path}\`.\n``
 * - ``ref``: ``see vault://{path}\n``
 */
export function renderInjection(
	doc: InjectionDoc,
	fmt: InjectionFormat,
): string {
	const title = doc.title || doc.path;

	if (fmt === "full") {
		const body = doc.body.trim();
		if (body) {
			return `## ${title}\n\n${body}\n`;
		}
		return `## ${title}\n`;
	}

	if (fmt === "keypoints") {
		if (doc.keyPoints.length > 0) {
			const bullets = doc.keyPoints.map((kp) => `- ${kp}`).join("\n");
			return `## ${title}\n\n${bullets}\n\nSee \`${doc.path}\`.\n`;
		}
		return `## ${title}\n\nSee \`${doc.path}\`.\n`;
	}

	// ref
	return `see vault://${doc.path}\n`;
}
