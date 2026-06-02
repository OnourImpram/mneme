"""Read-only vault audit aggregator (the console surface, v1).

Produces a deterministic, read-only snapshot of the vault: note counts by
memory type plus a security-scan summary. This is the programmatic/text console
surface; a browser audit UI (timeline, graph explorer, token-ROI dashboard) is
deferred. The aggregator never writes and never raises on a single bad file.

No LLM, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import scan_vault, summarize
from .vault.frontmatter import parse


@dataclass(frozen=True)
class AuditReport:
    """A read-only vault health + safety snapshot."""

    note_count: int
    type_counts: dict[str, int]
    security: dict[str, Any]


def build_audit(vault_root: Path) -> AuditReport:
    """Walk the vault markdown and build a read-only audit snapshot.

    Counts notes by frontmatter ``type`` (notes without parseable frontmatter
    are bucketed as ``"(none)"``) and folds in a security-scan summary. Symlinks
    that escape the vault are skipped; unreadable or malformed files never crash
    the audit.
    """
    root_resolved = vault_root.resolve()
    type_counts: dict[str, int] = {}
    note_count = 0
    for md_path in sorted(vault_root.rglob("*.md")):
        try:
            resolved = md_path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        note_count += 1
        type_name = "(none)"
        try:
            front, _ = parse(text)
            if front is not None and front.type:
                type_name = front.type
        except Exception:  # noqa: BLE001 - audit must never crash on a bad note
            type_name = "(unparseable)"
        type_counts[type_name] = type_counts.get(type_name, 0) + 1

    security = summarize(scan_vault(vault_root))
    return AuditReport(note_count=note_count, type_counts=type_counts, security=security)


def render_audit(report: AuditReport) -> str:
    """Render an :class:`AuditReport` as a plain-text report."""
    lines = ["# Vault audit", "", f"Notes: {report.note_count}", "", "## By type"]
    if report.type_counts:
        for type_name, count in sorted(report.type_counts.items()):
            lines.append(f"- {type_name}: {count}")
    else:
        lines.append("- (no notes)")
    lines.extend(["", "## Security"])
    sec = report.security
    lines.append(f"- files flagged: {sec.get('files_flagged', 0)}")
    lines.append(f"- total findings: {sec.get('total_findings', 0)}")
    by_kind = sec.get("by_kind", {})
    if isinstance(by_kind, dict):
        for kind, count in sorted(by_kind.items()):
            lines.append(f"  - {kind}: {count}")
    return "\n".join(lines) + "\n"
