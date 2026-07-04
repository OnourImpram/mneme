# Contributing to mneme

Thank you for your interest in contributing.

## Quick Start

```bash
git clone https://github.com/OnourImpram/mneme.git
cd mneme
make install-dev
make test
```

## Development Workflow

1. Open or claim a GitHub issue before starting non-trivial work.
2. Create a feature branch: `git checkout -b feat/your-feature`.
3. Write tests first when adding new behavior.
4. Run `make lint test` before committing.
5. Run `make bench-retrieval` if your change touches retrieval code.
6. Open a pull request against `main`.

## Commit Style

mneme uses Conventional Commits. Examples:

- `feat(retrieval): add RRF fusion with configurable k`
- `fix(hooks): handle BOM-prefixed settings.json on Windows`
- `docs(readme): clarify three-tier install profile`
- `test(fts5): add Turkish casefold edge case for KIYASLAMA`
- `chore(deps): bump pydantic to 2.7.1`

Allowed types: feat, fix, docs, test, refactor, perf, chore, build, ci.

## Pull Request Checklist

- [ ] Tests added or updated.
- [ ] Documentation updated where user-facing behavior changed.
- [ ] `CHANGELOG.md` entry under `[Unreleased]`.
- [ ] Sanitization gate passes (CI).
- [ ] `make bench-retrieval` shows nDCG@5 regression of zero points, or justified in PR description.

## Three Areas of Highest Contributor Value

1. Documentation improvements, especially the cookbook recipes in `docs/COOKBOOK.md`.
2. Tests, particularly integration coverage for retrieval and compression.
3. Benchmark methodology and reproducibility improvements.

New MCP tools require an Architecture Decision Record entry in `docs/ARCHITECTURE.md` before implementation.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Report violations to onuribram+mneme-conduct@outlook.com.

## License

By contributing, you agree that your contributions are licensed under the Apache License 2.0 (see `LICENSE` and `NOTICE`), the project's license from 3.0.0 onward. Inbound contributions are accepted under the same terms as the outbound license (inbound = outbound). Contributions merged while the project was MIT-licensed remain MIT in those published releases.
