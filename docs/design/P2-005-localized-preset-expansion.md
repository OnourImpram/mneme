# P2-005 — Localized Preset Expansion

## Capability

Allow `de`, `fr`, `es`, `ja`, and `zh` locale normalizers to be registered
and selected via `mneme index --locale <code>` without any architectural
change — by adding a locale registry to the existing seam.

## Current State

The locale seam exists at two layers, each with only Turkish (tr) today.

**Python (index side):**
- `packages/mneme-core/src/mneme_core/fts5/locale/__init__.py` (lines 1-7) —
  package-level docstring only; no registry, no `__all__`, no discovery
  mechanism.
- `packages/mneme-core/src/mneme_core/fts5/locale/tr.py` — exports
  `normalize_tr`, `normalize_tr_for_fts`, `normalize_tr_ascii_fold`,
  `normalize_tr_ascii_fold_for_fts`.  Pattern is the locale module contract.
- The `--locale` CLI flag (referenced at `cli.py:894`) selects `tr` only;
  unknown codes fall through to a rebuild-without-locale warning.

**TypeScript (query side):**
- `packages/mneme-mcp/src/locale/tr.ts` — exports `normalizeTr`,
  `normalizeTrForFts`, `normalizeTrAsciiFold`, `normalizeTrAsciiFoldForFts`.
- `search.ts` (line 15) imports `normalizeTr` directly; `QUERY_SIDE_NORMALIZER_PROFILE`
  is hardcoded to `"tr-cldr"` (line 58).

## Proposed Design

### Python registry (locale `__init__.py`)

```python
from dataclasses import dataclass
from collections.abc import Callable

NormFn = Callable[[str], str]

@dataclass(frozen=True)
class LocaleEntry:
    code: str                  # e.g. "de", "fr", "tr"
    normalize: NormFn          # for FTS query
    normalize_for_fts: NormFn  # for ingest (collapses whitespace)
    profile_name: str          # stored in index_meta for mismatch detection

LOCALE_REGISTRY: dict[str, LocaleEntry] = {}

def register_locale(entry: LocaleEntry) -> None:
    LOCALE_REGISTRY[entry.code] = entry

def get_locale(code: str) -> LocaleEntry | None:
    return LOCALE_REGISTRY.get(code)
```

Each locale module calls `register_locale(...)` at import time.
`tr.py` is updated to call `register_locale` for `"tr"`.
New locale modules (`de.py`, `fr.py`, etc.) follow the identical pattern:
Python's `str.lower()` with language-specific pre-substitutions where
required (German ß → ss, French ligatures, etc.).

The `mneme index --locale <code>` CLI path imports the corresponding module
(e.g. `from mneme_core.fts5.locale import de; _ = de`) to trigger
`register_locale`, then calls `get_locale(code)`.  Unknown codes produce a
clear error: `"Unknown locale '{code}'. Registered: {list(LOCALE_REGISTRY)}"`.

### TypeScript query side

Mirror: add `packages/mneme-mcp/src/locale/de.ts`, `fr.ts`, etc., each
exporting `normalize<Code>` and `normalize<Code>ForFts`.  `search.ts` reads
the `normalization_profile` stored by the indexer (already done at lines
260-281) and selects the correct normalizer at runtime via a
`LOCALE_NORMALIZERS: Record<string, (s: string) => string>` map imported
from a new `locale/index.ts`.

## Extension Point

- Python: `fts5/locale/__init__.py` lines 1-7 — add registry there.
- TS: `locale/tr.ts` pattern (lines 1-44) is the template for new modules.
  `search.ts:58` hardcoded profile constant becomes a lookup into the map.

## Feature-Flag / Rollout Plan

No flag needed.  Unrecognized `--locale` codes already degrade gracefully
(rebuild without locale-specific normalizer).  New locale modules are
additive imports; existing `tr` behavior is unchanged.  TS query side change
is gated on the `normalization_profile` value stored in the index — a `tr`
index continues to use `normalizeTr` exactly as today.
