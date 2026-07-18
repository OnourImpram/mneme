# mneme-core

Python core for [mneme](https://github.com/OnourImpram/mneme): vault-native memory for Claude Code, also usable from Codex and any MCP client. mneme is Claude-Code-native by origin and client-neutral at the core.

This package provides:

- FTS5 BM25 full-text indexer with language-aware normalization (Turkish casefold module included as utility).
- RRF-ready retrieval pipeline at `k=60`, with FTS5 shipped and optional backends injectable by callers.
- Graphiti bi-temporal knowledge graph adapter, gated by the full profile and local Neo4j.
- Roadmap dense embedding adapter. The v1.0 standard profile reserves the runtime slot but does not ship packaged LEANN retrieval.
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

Apache License 2.0. See `LICENSE` and `NOTICE` in the repository root.
