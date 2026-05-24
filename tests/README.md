# Cross-Package Integration Tests

Tests that exercise mneme across package boundaries. Per-package unit and integration tests live inside each package's own `tests/` directory.

## Layout

- `e2e_hook_lifecycle.py`: end-to-end test that simulates a full Claude Code session and asserts hook ordering, retrieval correctness, and vault state.
- `e2e_migrate_then_search.py`: tests `mneme migrate-from-claude-mem` followed by `mneme_search` against the migrated vault.
- `e2e_three_tier_install.py`: validates that `mneme install --profile=lite` then `mneme upgrade --profile=standard` then `mneme upgrade --profile=full` produces equivalent retrieval state without data loss.

## Running

```bash
cd packages/mneme-core && pip install -e ".[dev]"
pnpm install
pnpm -r build
pytest tests/ -v
```

## Prerequisites

- Python 3.11 or newer.
- Node 20 or newer.
- For full profile tests: Docker plus Neo4j 5.x.
