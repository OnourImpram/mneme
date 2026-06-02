"""``mneme-console`` CLI: read-only vault audit as a self-contained HTML report.

Produces a deterministic, offline-safe HTML document (or JSON) summarising the
vault audit snapshot. No network calls, no server, no LLM, no clock inside the
render functions. Everything is inline — the report renders fully offline.

Entry point: ``mneme-console --vault PATH [--out FILE] [--format html|json]``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from .audit import AuditReport, build_audit

# ---------------------------------------------------------------------------
# Pure data helpers
# ---------------------------------------------------------------------------


def audit_to_dict(
    report: AuditReport,
    *,
    graph_summary: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe dict representation of *report*.

    Keys: ``note_count`` (int), ``type_counts`` (dict with sorted keys),
    ``security`` (passthrough from report), ``graph`` (dict or None).
    Deterministic: ``type_counts`` keys are always sorted.
    """
    return {
        "note_count": report.note_count,
        "type_counts": dict(sorted(report.type_counts.items())),
        "security": report.security,
        "graph": graph_summary,
    }


def read_graph_summary(vault_root: Path) -> dict[str, int] | None:
    """Read ``.mneme/graph.json`` under *vault_root* and return a node/edge count.

    Returns ``{"nodes": <int>, "edges": <int>}`` when the file exists and
    parses cleanly. Returns ``None`` when absent, unreadable, or malformed.
    Never raises.
    """
    graph_path = vault_root / ".mneme" / "graph.json"
    try:
        raw = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    nodes_val = raw.get("nodes", [])
    edges_val = raw.get("edges", [])
    try:
        nodes_count = len(nodes_val) if not isinstance(nodes_val, int) else nodes_val
        edges_count = len(edges_val) if not isinstance(edges_val, int) else edges_val
    except TypeError:
        return None
    return {"nodes": nodes_count, "edges": edges_count}


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#f8f9fa;color:#212529;padding:2rem}}
h1{{font-size:1.5rem;margin-bottom:1.5rem;color:#0d6efd}}
h2{{font-size:1.1rem;margin:1.25rem 0 0.5rem;color:#495057}}
.card{{background:#fff;border:1px solid #dee2e6;border-radius:0.5rem;
padding:1.25rem;margin-bottom:1rem}}
.stat{{font-size:2rem;font-weight:700;color:#0d6efd}}
table{{width:100%;border-collapse:collapse;font-size:0.9rem}}
th,td{{text-align:left;padding:0.4rem 0.75rem;border-bottom:1px solid #dee2e6}}
th{{background:#f1f3f5;font-weight:600}}
tr:last-child td{{border-bottom:none}}
.badge{{display:inline-block;padding:0.2em 0.55em;border-radius:0.3em;
font-size:0.8rem;font-weight:600}}
.badge-warn{{background:#fff3cd;color:#856404}}
.badge-ok{{background:#d1e7dd;color:#0f5132}}
.hidden{{display:none}}
</style>
</head>
<body>
<h1 id="report-title"></h1>

<div class="card">
  <h2>Overview</h2>
  <div class="stat" id="note-count"></div>
  <div style="color:#6c757d;font-size:0.9rem">total notes indexed</div>
</div>

<div class="card">
  <h2>Notes by type</h2>
  <table id="type-table">
    <thead><tr><th>Type</th><th>Count</th></tr></thead>
    <tbody id="type-tbody"></tbody>
  </table>
  <div id="type-empty" class="hidden" style="color:#6c757d;font-size:0.9rem">No notes found.</div>
</div>

<div class="card">
  <h2>Security scan</h2>
  <table id="sec-table">
    <thead><tr><th>Metric</th><th>Value</th></tr></thead>
    <tbody id="sec-tbody"></tbody>
  </table>
</div>

<div id="graph-card" class="card hidden">
  <h2>Knowledge graph</h2>
  <table>
    <thead><tr><th>Metric</th><th>Value</th></tr></thead>
    <tbody id="graph-tbody"></tbody>
  </table>
</div>

<script type="application/json" id="audit-data">{json_payload}</script>
<script>
(function () {{
  var el = document.getElementById('audit-data');
  var data = JSON.parse(el.textContent);

  function setText(id, value) {{
    document.getElementById(id).textContent = String(value);
  }}

  function appendRow(tbodyId, cells) {{
    var tbody = document.getElementById(tbodyId);
    var tr = document.createElement('tr');
    for (var i = 0; i < cells.length; i++) {{
      var td = document.createElement('td');
      td.textContent = String(cells[i]);
      tr.appendChild(td);
    }}
    tbody.appendChild(tr);
  }}

  // Title
  document.getElementById('report-title').textContent = document.title;

  // Overview
  setText('note-count', data.note_count);

  // By-type table
  var typeCounts = data.type_counts || {{}};
  var typeKeys = Object.keys(typeCounts);
  if (typeKeys.length === 0) {{
    document.getElementById('type-empty').classList.remove('hidden');
    document.getElementById('type-table').classList.add('hidden');
  }} else {{
    for (var i = 0; i < typeKeys.length; i++) {{
      appendRow('type-tbody', [typeKeys[i], typeCounts[typeKeys[i]]]);
    }}
  }}

  // Security
  var sec = data.security || {{}};
  appendRow('sec-tbody', ['files flagged', sec.files_flagged != null ? sec.files_flagged : 0]);
  appendRow('sec-tbody', ['total findings', sec.total_findings != null ? sec.total_findings : 0]);
  var byKind = sec.by_kind || {{}};
  var kindKeys = Object.keys(byKind);
  for (var k = 0; k < kindKeys.length; k++) {{
    appendRow('sec-tbody', ['  └ ' + kindKeys[k], byKind[kindKeys[k]]]);
  }}

  // Graph panel (optional)
  if (data.graph && typeof data.graph === 'object') {{
    var g = data.graph;
    document.getElementById('graph-card').classList.remove('hidden');
    appendRow('graph-tbody', ['nodes', g.nodes != null ? g.nodes : 0]);
    appendRow('graph-tbody', ['edges', g.edges != null ? g.edges : 0]);
  }}
}})();
</script>
</body>
</html>"""


def _escape_json_for_script(raw_json: str) -> str:
    """Escape a JSON string so it cannot break out of a ``<script>`` block.

    Replaces ``<`` with ``\\u003c``, ``>`` with ``\\u003e``, and ``&`` with
    ``\\u0026``. This prevents a literal ``</script>`` (or any tag) inside a
    string value from terminating the script element or injecting markup.
    The resulting text is still valid JSON and parses identically.
    """
    return raw_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_html_report(
    data: dict[str, Any],
    *,
    title: str = "mneme vault audit",
) -> str:
    """Render *data* as a single self-contained HTML document.

    Security guarantees:
    - No external resources (no CDN, no web fonts, no fetch/XHR).
    - ``data`` is embedded as JSON inside a ``<script type="application/json">``
      block with ``<``, ``>``, ``&`` escaped to Unicode escapes so that
      ``</script>`` or any tag inside a string value cannot break the block.
    - The inline JS reads ``textContent`` (not ``innerHTML``) so no string
      value can inject live markup.
    - Output is deterministic: same ``data`` -> identical HTML.
    """
    raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    safe_json = _escape_json_for_script(raw_json)
    return _HTML_TEMPLATE.format(title=title, json_payload=safe_json)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command(help="Read-only vault audit console — HTML or JSON report.")
@click.option(
    "--vault",
    "vault_root",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the vault root directory.",
)
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write output to FILE instead of stdout.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["html", "json"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
def _cli(vault_root: Path, out_file: Path | None, fmt: str) -> None:
    report = build_audit(vault_root)
    graph_summary = read_graph_summary(vault_root)
    data = audit_to_dict(report, graph_summary=graph_summary)

    if fmt == "json":
        output = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        output = render_html_report(data)

    if out_file is not None:
        out_file.write_text(output, encoding="utf-8")
    else:
        click.echo(output)


def main() -> None:
    """Entry point for the ``mneme-console`` script."""
    _cli(prog_name="mneme-console")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
