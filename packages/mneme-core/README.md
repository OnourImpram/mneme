# mneme-core

Python core for [mneme](https://github.com/TheGoatPsy/mneme): vault-native memory for Claude Code, also usable from Codex and any MCP client. mneme is Claude-Code-native by origin and client-neutral at the core.

This package provides:

- FTS5 BM25 full-text indexer with language-aware normalization (Turkish casefold module included as utility).
- LEANN dense embedding adapter (optional, standard+ profile).
- Graphiti bi-temporal knowledge graph adapter (optional, full profile).
- Retrieval pipeline with Reciprocal Rank Fusion at `k=60`.
- Background compression pipeline with 4-D rubric (Accuracy, Depth, Context, Continuity) and cost cap ledger.
- Adaptive Context Layer: `distill.shell_compress`, `distill.injection_dedup`, `distill.adaptive_topk`, `distill.compressed_format`, and the `mneme audit` CLI.

## Installation

```bash
pip install mneme-core
```

For development:

```bash
pip install -e ".[dev]"
```

## Quality Gates

This package targets Python 3.11+ and passes `ruff` lint plus `mypy --strict`. Test coverage minimum is 80 percent for business logic.

## License

MIT. See LICENSE in the repository root.
