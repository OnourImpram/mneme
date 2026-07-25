"""``mneme-console --serve``: localhost-only, read-only web console.

The serve mode lifts the static audit report into a small live web UI:
an interactive explorer (vault audit, code-graph view, temporal claims
with supersedes chains, autonomous-edit journal, audit-chain
verification) served from the loopback interface.

Security posture, in order:

* **Read-only.** Only ``GET`` is implemented; every other method gets
  ``405``. There are no write endpoints at all — a future write surface
  must demand a capability token (``capability.py``) and explicit
  consent, per the conflict-resolution #5 record.
* **Loopback only.** The default bind is ``127.0.0.1`` and the CLI
  refuses non-loopback hosts unless ``--unsafe-expose`` is passed.
* **Host-header pinned.** A loopback bind alone does not stop DNS
  rebinding: a malicious page can resolve its own hostname to
  ``127.0.0.1`` and read the JSON APIs through the victim's browser.
  Every request must therefore carry a Host header naming a loopback
  alias (or the explicit bind host); anything else is refused with
  ``403``. ``--unsafe-expose`` disables the check along with the bind
  guard, since a remote-exposed console cannot enumerate valid hosts.
* **No external requests.** The explorer page is fully self-contained
  (inline CSS/JS, no CDN), so the console works air-gapped and leaks
  nothing (Core Invariant 2: no network beyond the local socket).
* **Stdlib only.** ``http.server`` threading server; no new deps.

Every JSON payload is assembled from the same deterministic builders
the CLI uses (``build_audit``, ``graph.json``, the claims table, the
rollback journal, ``verify_chain``), so the web view never invents
state of its own.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .audit import build_audit
from .audit_chain import verify_chain
from .console import audit_to_dict, read_graph_summary
from .vault.config import VaultConfig

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_REFUSED_BODY_BYTES = 1_048_576


def host_header_allowed(raw: str | None, allowed: frozenset[str]) -> bool:
    """True when the Host header names an allowed host.

    A missing header is allowed: every browser (the DNS-rebinding
    vehicle) always sends Host, so refusing header-less requests would
    only break odd local HTTP/1.0 tooling without closing anything.
    Ports are ignored; bracketed IPv6 forms are unwrapped.
    """
    if raw is None:
        return True
    host = raw.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return False
        host = host[1:end]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host in allowed


def _graph_payload(vault: VaultConfig) -> dict[str, Any]:
    graph_path = vault.state_dir / "graph.json"
    if not graph_path.is_file():
        return {"nodes": [], "edges": [], "note": "graph.json absent — run mneme-graph build"}
    try:
        parsed = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"nodes": [], "edges": [], "note": "graph.json unreadable"}
    if not isinstance(parsed, dict):
        return {"nodes": [], "edges": [], "note": "graph.json malformed"}
    return {
        "nodes": parsed.get("nodes", []),
        "edges": parsed.get("edges", []),
    }


def _claims_payload(vault: VaultConfig) -> dict[str, Any]:
    if not vault.fts5_db.exists():
        return {"claims": [], "note": "index not found"}
    try:
        conn = sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"claims": [], "note": f"db unavailable: {exc}"}
    try:
        from .temporal.query import _all_columns, _table_exists

        if not _table_exists(conn):
            return {"claims": [], "note": "claims table absent"}
        rows = conn.execute(
            f"SELECT {_all_columns()} FROM claims ORDER BY observed_at"  # noqa: S608
        ).fetchall()
    finally:
        conn.close()
    cols = [
        "claim_id",
        "path",
        "statement",
        "statement_normalized",
        "valid_from",
        "valid_to",
        "observed_at",
        "supersedes",
        "superseded_by",
        "claim_key",
        "confidence_label",
        "trust",
        "content_hash",
        "indexed_at",
    ]
    return {"claims": [dict(zip(cols, row, strict=True)) for row in rows]}


def _changes_payload(vault: VaultConfig) -> dict[str, Any]:
    from .memory_apply import list_changes

    changes = list_changes(vault)
    return {"count": len(changes), "changes": changes}


def _chain_payload(vault: VaultConfig, day: str | None) -> dict[str, Any]:
    resolved = day or datetime.now(UTC).strftime("%Y-%m-%d")
    if not _DAY_RE.match(resolved):
        return {"error": "day must be YYYY-MM-DD"}
    report = verify_chain(vault.state_dir, resolved)
    return {
        "day": resolved,
        "records": report.records,
        "valid": report.valid,
        "first_break_line": report.first_break_line,
        "detail": report.detail,
    }


def build_payload(vault: VaultConfig, route: str, query: dict[str, list[str]]) -> Any | None:
    """Resolve an ``/api/...`` route to its JSON-ready payload.

    Returns ``None`` for unknown routes. Pure dispatch — the handler
    owns HTTP concerns, this owns content.
    """
    if route == "/api/audit":
        report = build_audit(vault.root)
        return audit_to_dict(report, graph_summary=read_graph_summary(vault.root))
    if route == "/api/graph":
        return _graph_payload(vault)
    if route == "/api/claims":
        return _claims_payload(vault)
    if route == "/api/changes":
        return _changes_payload(vault)
    if route == "/api/chain":
        day_values = query.get("day", [])
        return _chain_payload(vault, day_values[0] if day_values else None)
    return None


_EXPLORER_FALLBACK = "<!DOCTYPE html><title>mneme console</title><p>explorer asset missing</p>"


def _load_explorer_html() -> str:
    """Load the self-contained explorer page shipped as package data."""
    try:
        return (Path(__file__).resolve().parent / "console_explorer.html").read_text(
            encoding="utf-8"
        )
    except OSError:
        return _EXPLORER_FALLBACK


class _ConsoleHandler(BaseHTTPRequestHandler):
    """GET-only request handler bound to one vault."""

    vault: VaultConfig  # injected by make_server via subclassing
    allowed_hosts: frozenset[str] = LOOPBACK_HOSTS
    enforce_host: bool = True
    server_version = "mneme-console"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # quiet by default; the console is a local tool, not a service

    def _host_ok(self) -> bool:
        if not self.enforce_host:
            return True
        return host_header_allowed(self.headers.get("Host"), self.allowed_hosts)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if not self._host_ok():
            self._send(403, json.dumps({"error": "forbidden host header"}), "application/json")
            return
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/" or route == "/index.html":
            self._send(200, _load_explorer_html(), "text/html; charset=utf-8")
            return
        if route == "/report":
            from .console import render_html_report

            report = build_audit(self.vault.root)
            data = audit_to_dict(
                report, graph_summary=read_graph_summary(self.vault.root)
            )
            self._send(200, render_html_report(data), "text/html; charset=utf-8")
            return
        payload = build_payload(self.vault, route, parse_qs(parsed.query))
        if payload is None:
            self._send(404, json.dumps({"error": "not found"}), "application/json")
            return
        self._send(
            200,
            json.dumps(payload, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
        )

    def _send(self, status: int, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    # Read-only contract: everything except GET is refused. The host
    # check still runs first so a rebound origin learns nothing, not
    # even the method policy.
    def _discard_refused_body(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            return
        if length < 0 or length > _MAX_REFUSED_BODY_BYTES:
            self.close_connection = True
            return
        if length:
            self.rfile.read(length)

    def _refuse(self) -> None:
        self._discard_refused_body()
        if not self._host_ok():
            self._send(403, json.dumps({"error": "forbidden host header"}), "application/json")
            return
        self._send(405, json.dumps({"error": "read-only console"}), "application/json")

    do_POST = _refuse  # noqa: N815 - http.server API
    do_PUT = _refuse  # noqa: N815
    do_DELETE = _refuse  # noqa: N815
    do_PATCH = _refuse  # noqa: N815


def make_server(
    vault: VaultConfig,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    allow_remote: bool = False,
) -> ThreadingHTTPServer:
    """Build (but do not start) the console HTTP server.

    Refuses a non-loopback *host* unless ``allow_remote`` is explicit,
    so the read-only console cannot be exposed by accident.
    """
    if host not in LOOPBACK_HOSTS and not allow_remote:
        raise ValueError(
            f"refusing non-loopback bind {host!r}; pass --unsafe-expose to override"
        )

    class BoundHandler(_ConsoleHandler):
        pass

    BoundHandler.vault = vault
    BoundHandler.enforce_host = not allow_remote
    BoundHandler.allowed_hosts = frozenset({*LOOPBACK_HOSTS, host.strip().lower()})
    return ThreadingHTTPServer((host, port), BoundHandler)


def serve_forever(
    vault: VaultConfig,
    host: str = "127.0.0.1",
    port: int = 7421,
    *,
    allow_remote: bool = False,
) -> None:
    """Blocking entry point used by the CLI ``--serve`` flag."""
    server = make_server(vault, host, port, allow_remote=allow_remote)
    actual_host = str(server.server_address[0])
    actual_port = int(server.server_address[1])
    print(
        f"mneme console: http://{actual_host}:{actual_port}/ (read-only, Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
