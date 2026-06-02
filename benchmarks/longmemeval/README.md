# LongMemEval Harness

Measures mneme retrieval quality on the [LongMemEval](https://arxiv.org/abs/2410.10813)
benchmark: session-level QA pairs with gold answer documents.

Metrics: Recall@1, Recall@5, Recall@10, MRR@10, abstention accuracy.

No superiority claim is made. This harness records results only.
Real-dataset numbers depend on the production vault and the official dataset download.

---

## Running with the synthetic fixture (CI-safe, no download required)

The synthetic fixture ships 6 documents and 8 cases (6 factual + 2 abstention)
using a closed vocabulary that does not require any external data.

```bash
# From the repo root
python -m benchmarks.longmemeval.runner
```

Write results to `baseline.json`:

```bash
python -m benchmarks.longmemeval.runner --write-baseline
```

---

## Running with the real LongMemEval dataset

1. Download the LongMemEval dataset from the official repository:
   https://github.com/xiaowu0162/LongMemEval

2. The dataset is a JSON file containing a list of case objects.
   Each case has the fields: `session_id`, `query`, `gold_answer`,
   `gold_doc_paths`, `case_type`.

3. Run the harness:

```bash
python -m benchmarks.longmemeval.runner --dataset path/to/longmemeval.json
```

4. Write results to a file:

```bash
python -m benchmarks.longmemeval.runner \
  --dataset path/to/longmemeval.json \
  --output benchmarks/_runs/longmemeval-result.json
```

---

## Case types

| case_type | Description |
|-----------|-------------|
| `single-session-user` | User memory from one session |
| `multi-session-user` | User memory across multiple sessions |
| `single-session-assistant` | Assistant-generated content from one session |
| `temporal` | Time-sensitive retrieval |
| `knowledge-update` | Fact that was updated in a later session |
| `abstention` | No relevant memory exists; correct response is empty result |

Abstention cases are evaluated separately: `retrieve()` must return an
empty list. A non-empty result on an abstention case is a false recall.

---

## Output format

```json
{
  "benchmark": "longmemeval",
  "recorded_at": "YYYY-MM-DD",
  "dataset_source": "synthetic-fixture | path/to/dataset.json",
  "metadata": {
    "vault_doc_count": 6,
    "index_locale": "identity",
    "mneme_version": "1.1.0"
  },
  "results": {
    "recall_at_1": 0.83,
    "recall_at_5": 0.83,
    "recall_at_10": 0.83,
    "mrr_at_10": 0.83,
    "abstention_accuracy": 1.0,
    "case_count": 8,
    "abstention_count": 2,
    "by_case_type": { ... }
  }
}
```

---

## Disclaimer

Results on the synthetic fixture are not comparable to results on the real
LongMemEval dataset. The synthetic fixture uses a small closed-vocabulary
corpus designed to verify the harness runs correctly, not to benchmark
absolute retrieval quality.

The FTS5 leg is the only active retrieval leg in these results. Dense vector
and KG backends are roadmap items and are not included.
