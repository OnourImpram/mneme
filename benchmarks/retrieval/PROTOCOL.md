# Benchmark A — Pre-Registration Protocol

**Version**: 1.0

**Date**: 2026-05-25

**Scope**: `benchmarks/retrieval/` (Benchmark A — Retrieval Quality)

---

## 1. Purpose

This document pre-registers the evaluation design for Benchmark A before
any system output is interpreted. Pre-registration means the corpus, query
set, relevance judgments, metrics, and pass/fail criteria are all fixed and
committed to version control prior to running the harness. Changing any of
these after a run and then re-evaluating would invalidate the result; if
changes become necessary, a new pre-registration entry must be added to
this file with a new version number and date.

---

## 2. Corpus

| Property | Value |
|---|---|
| Generator | `mneme_core.bench.synth.build_synthetic_corpus` |
| Seed | 42 (pinned; `MNEME_BENCH_SEED=42` in CI) |
| Topics | 10 (build\_system, compiler, consensus, garbage\_collector, networking, rate\_limit, scheduler, storage\_engine, type\_theory, vector\_index) |
| Documents per topic | 50 |
| Total documents | 500 |
| Document format | Markdown with YAML front-matter (`id`, `type`, `topic`) |
| Vocabulary | Closed per topic (10 terms each); generic filler from a fixed list |
| Language | English only |
| Cultural content | None; vocabulary is drawn from software-engineering terminology |

The corpus is entirely synthetic and deterministic. There is no
operator-specific, personal, or culturally loaded content.

---

## 3. Query Set

| Property | Value |
|---|---|
| Queries per topic | 5 |
| Total queries | 50 |
| Query construction | `"{_QUERY_STEM} {term_a} {term_b} {_QUERY_TAIL}"` where `term_a`, `term_b` are the target document's title terms |
| Minimum query length | Padded to exceed `RetrievalConfig.min_query_length` (default 20 chars) |

Each query has exactly one designated relevant document. The other
documents in the same topic serve as hard negatives (same vocabulary,
different title-term pair). The retriever must break ties on the specific
two-term combination, not just topic membership.

---

## 4. Relevance Judgments

Relevance judgments are **embedded in the corpus generator**
(`mneme_core.bench.synth.SyntheticQuery.relevant_doc_ids`). Each query's
single relevant document is determined at corpus-generation time by the
rule: the document whose `title_terms` match the query's `(term_a, term_b)`
pair is relevant; all others are non-relevant.

**Key properties that prevent circularity:**

1. **Judgments are authored independently of system output.** The relevance
   rule is a structural property of the generator (title-term identity), not
   a score or ranking produced by any retrieval model. The system never
   labels its own output as relevant.

2. **Judgments are frozen in source code.** The generator is part of the
   committed package (`packages/mneme-core/src/mneme_core/bench/synth.py`).
   Changing judgments requires a source code change with a visible diff and
   a new pre-registration entry in this file.

3. **Deterministic seed.** Given `seed=42`, the generator produces a
   byte-identical corpus and judgment set on every run. Any deviation
   (e.g., a seed change) must be reflected in a new pre-registration version.

4. **No post-hoc label adjustment.** Relevance labels are not updated based
   on what the system retrieves. The generator is run once to produce the
   corpus; retrieved results are compared against the frozen labels.

**Limitation to state honestly:** The judgments are synthetic and
automatically derived, not produced by human assessors reviewing real
documents. The metric values therefore measure retrieval quality on a
controlled synthetic task, not on real-world vault content. This is an
acknowledged limitation of the benchmark, documented here rather than
obscured.

---

## 5. Metrics

Two retrieval metrics are computed and reported for every evaluation
condition. A third metric (MRR) is reported for diagnostic purposes but
not guarded.

### 5.1 nDCG@5 (primary)

Normalized Discounted Cumulative Gain at cutoff 5. Binary relevance (a
document is either the designated answer or not). Implemented in
`mneme_core.bench.metrics.ndcg_at_k`.

Formula:

```
DCG@5   = sum_{i=1}^{5} rel_i / log2(i + 1)
IDCG@5  = sum_{i=1}^{min(|R|,5)} 1 / log2(i + 1)
nDCG@5  = DCG@5 / IDCG@5
```

Returns 0.0 when the query has no relevant documents (not applicable to
positive queries; see negative probe below).

### 5.2 Recall@10 (secondary, guarded)

Fraction of relevant documents that appear in the top-10 ranked results.
Because each query has exactly one relevant document, Recall@10 is either
0 (relevant doc not in top 10) or 1 (relevant doc found in top 10).
Implemented in `mneme_core.bench.metrics.recall_at_k`.

### 5.3 MRR (diagnostic, not guarded)

Mean Reciprocal Rank at cutoff 10. Reported for diagnostic context; not
enforced by `regression_guard.py`.

---

## 6. Evaluation Conditions

Three conditions are reported:

| Condition key | Description |
|---|---|
| `baseline_fts5_only` | Direct FTS5 BM25 search; bypasses `RetrievalConfig.min_query_length` gate to establish a clean single-leg baseline |
| `pipeline_fts5_only` | Production `retrieve()` pipeline with only FTS5 enabled |
| `pipeline_rrf_fts5_plus_bow` | Production `retrieve()` pipeline with RRF fusion of FTS5 and BoW cosine surrogate |

The **headline condition** for regression guarding is `pipeline_fts5_only`,
the shipped production retrieval path. The BoW backend is a lexical surrogate,
not a semantic or dense model, and is retained only as a fusion ablation.

---

## 7. Negative Probe

A set of 5 queries is run against the indexed corpus to verify the retriever
does not hallucinate relevance for out-of-vocabulary input.

**Query selection criterion (pre-registered):** Each query string contains
only terms that are absent from all topic vocabularies in `_TOPIC_VOCAB` and
from the `_FILLER` list in `synth.py`. These terms were chosen before running
the harness and are committed in `run.py` as `_NEGATIVE_PROBE_QUERIES`.

**Pass criterion:** The FTS5 leg must return zero hits (`hits_returned == 0`)
for every negative-probe query. Because the FTS5 index only stores tokens
present in indexed documents, a query composed entirely of
out-of-vocabulary terms must produce an empty result set. Any non-empty
result indicates the retriever matched on noise or stop-word overlap, which
is a defect.

**Negative probe queries (frozen):**

1. `luminescent crystallography photon refraction prism`
2. `seismic tectonic lithosphere mantle subduction`
3. `endoplasmic reticulum mitochondria ribosome cytoplasm`
4. `sonata allegro cadence fugue counterpoint harmony`
5. `hydraulic reservoir turbine impeller cavitation nozzle`

These queries are static constants in `run.py` (`_NEGATIVE_PROBE_QUERIES`).
Modifying them after a run requires a new pre-registration entry.

**nDCG@5 behaviour on negative probes:** `ndcg_at_k` returns `0.0` when
`relevant_doc_ids` is empty. Negative-probe queries are NOT passed through
`ndcg_at_k` or `recall_at_k`; they are evaluated only by the hit-count
criterion above. This avoids inflating or deflating the positive-query
metric averages.

---

## 8. Pass / Regression Criteria

Enforced by `regression_guard.py` against `baseline.json`:

| Check | Criterion |
|---|---|
| nDCG@5 | Fresh value must not drop more than **0.02** below baseline |
| Recall@10 | Fresh value must not drop more than **0.05** below baseline |
| Negative probe | `negative_probe.all_passed` must be `true` in the fresh result |

Baseline values (measured 2026-05-25, seed 42, 500 docs, 50 queries):

| Condition | nDCG@5 | Recall@10 |
|---|---|---|
| baseline\_fts5\_only | 0.8006 | 1.0000 |
| pipeline\_fts5\_only | 0.8006 | 1.0000 |
| pipeline\_rrf\_fts5\_plus\_bow, lexical ablation | 0.5210 | 0.9600 |

The guard compares the `pipeline_fts5_only` condition. Baseline
values are locked in `baseline.json`; they are not updated automatically.
A deliberate improvement to the retriever that raises metrics above the
baseline is welcome — the guard does not penalize upward movement.

---

## 9. Anti-Circularity Methodology

The primary reviewer critique addressed here is that retrieval evaluation
is circular when relevance judgments are derived from the system being
evaluated. This protocol guards against that in the following ways:

| Risk | Mitigation |
|---|---|
| System labels its own output as relevant | Judgments are defined by a structural rule (title-term identity) in the corpus generator, not by any retrieval score |
| Judgments changed after seeing results | Judgments live in committed source code; any change produces a visible diff and requires a new pre-registration entry in this file |
| Baseline updated to hide regressions | `baseline.json` is committed; automated CI never writes back to it; updates require an explicit PR |
| Seed changed to cherry-pick a favourable corpus | Seed is pinned to 42 in CI and in `baseline.json`; deviation requires a source change |
| Negative-probe queries chosen after seeing output | Probe queries are committed as static constants in `run.py` before any run |

**What this protocol does not guarantee:** Human-assessed relevance on
real vault content. The synthetic judgments are correct by construction
for the closed-vocabulary corpus, but they do not validate retrieval quality
on open-ended real-world queries. That validation is a Phase J dogfood-week
deliverable (see `benchmarks/README.md`).

---

## 10. Reproducibility

- Seed: `42` (override with `--seed` flag, but a different seed invalidates
  the locked baseline).
- Python: `3.12` (CI-pinned in `bench.yml`).
- Per-run `hardware.json` captures CPU, RAM, OS, Python version.
- All inputs are synthetic; no external data dependencies.
- The `--output` flag writes UTF-8 without BOM on all platforms.

---

## 11. Change Log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05-25 | Initial pre-registration: corpus, queries, judgments, nDCG@5, Recall@10, negative probe, pass criteria |
