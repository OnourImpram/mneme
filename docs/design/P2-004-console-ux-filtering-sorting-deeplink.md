# P2-004 — Read-Only Console UX: Filtering, Sorting, Deep-Linking

## Capability

Extend the static HTML vault-audit report with client-side column filtering
on the type table, sort-by-click on column headers, and URL fragment
deep-linking to named sections — all within the existing no-CDN,
no-network, offline-safe design contract.

## Current State

`packages/mneme-core/src/mneme_core/console.py`:

- `_HTML_TEMPLATE` (lines 73-192) — single-string HTML/CSS/JS template.
  The inline `<script>` (lines 134-190) reads `audit-data` JSON and
  populates three tables: type counts, security, and knowledge-graph.
  No filtering, no sortable headers, no anchor IDs on section headings.
- `render_html_report()` (lines 206-224) — formats the template with
  `json_payload` and `title`.  Pure function; deterministic.
- `_escape_json_for_script()` (lines 195-203) — XSS guard for embedded JSON.

The HTML already contains `id="type-table"`, `id="sec-table"`, and
`id="graph-card"` on the containing elements but has no per-section anchor
`<a>` targets or filter controls.

## Proposed Design

All changes are confined to `_HTML_TEMPLATE`; `render_html_report()` and
`audit_to_dict()` are untouched.

### 1. Filtering

Add a `<input type="search" id="type-filter">` above the type table.
The inline JS attaches an `input` listener that hides rows whose first cell
does not contain the filter string (case-insensitive, plain `includes`).
The filter is never sent anywhere (no XHR, no `window.location` mutation).

### 2. Sorting

Make `<th>` elements in the type table clickable.  Each click toggles
ascending / descending sort on that column.  Sort state is held in a
module-local JS variable; no `localStorage`, no cookies.  Sort comparator:
column 0 (type) lexicographic, column 1 (count) numeric.

### 3. Deep-linking

Add `id="section-overview"`, `id="section-types"`, `id="section-security"`,
`id="section-graph"` to the four `<div class="card">` elements.  The
existing JS `setText('report-title', ...)` call can also set
`document.title` to the vault path so browser history is meaningful.

### Extension Point

`console.py:73` — `_HTML_TEMPLATE` string.  The template is a single Python
string literal; replacement is a one-file edit with no API surface change.
The `render_html_report()` call signature and `audit_to_dict()` schema are
unchanged, so the interactive console (`console_serve.py`) continues to work.

## Feature-Flag / Rollout Plan

No flag needed: the changes are pure client-side JS in the rendered HTML.
The `--format json` path is entirely unaffected.  A `--legacy-html` flag
could be added if minimal-JS compatibility is ever needed, but is not
anticipated.
