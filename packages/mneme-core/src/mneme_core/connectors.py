"""Opt-in external-source connector framework (v1) with a local reference connector.

A :class:`Connector` pulls documents from an external source *into* the vault.
Every connector is opt-in (``enabled`` defaults to ``False``); :func:`ingest`
is a no-op until it is explicitly enabled, and flipping ``enabled`` back to
``False`` is the revocation mechanism. Ingestion REDACTS all title/content via
``mneme_core.privacy.redact`` before it becomes a memory and attaches provenance
(source kind, external id, content hash, fetch time, ``trust='external'``). This
is the privacy-safe inbound direction; pushing data *out* is gated separately by
``mneme_core.modes`` (artifact_upload_allowed).

The bundled :class:`LocalMarkdownConnector` reads local markdown files and uses
no network. Network connectors (GitHub, Drive, Notion, Slack, Gmail, S3,
Zotero, Obsidian) are deferred: each belongs behind this same protocol with
per-source credentials and revocation.

No LLM, no network in this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .privacy import redact


@dataclass(frozen=True)
class SourceDocument:
    """A raw document fetched from an external source (not yet redacted)."""

    external_id: str
    title: str
    content: str
    source_kind: str


@dataclass(frozen=True)
class IngestedMemory:
    """A redacted, provenance-bearing memory derived from a SourceDocument."""

    external_id: str
    source_kind: str
    title: str  # redacted
    content: str  # redacted
    content_hash: str
    fetched_at: datetime
    trust: str = "external"


class Connector(Protocol):
    """Structural interface for an external-source connector."""

    name: str
    enabled: bool

    def fetch(self) -> list[SourceDocument]: ...


def _to_utc_iso(dt: datetime) -> str:
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return aware.isoformat(timespec="microseconds")


def ingest(connector: Connector, *, fetched_at: datetime) -> list[IngestedMemory]:
    """Fetch, redact, and wrap source documents with provenance.

    Returns ``[]`` when the connector is disabled (opt-in default / revocation).
    ``redact`` is applied to title and content BEFORE the IngestedMemory is
    built, so no raw external content is ever stored. ``fetched_at`` is injected
    by the caller (the module stays clock-free).
    """
    if not connector.enabled:
        return []
    out: list[IngestedMemory] = []
    for doc in connector.fetch():
        title = redact(doc.title)
        content = redact(doc.content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        out.append(
            IngestedMemory(
                external_id=doc.external_id,
                source_kind=doc.source_kind,
                title=title,
                content=content,
                content_hash=content_hash,
                fetched_at=fetched_at,
            )
        )
    return out


def to_markdown(memory: IngestedMemory) -> str:
    """Render an :class:`IngestedMemory` as a vault note with external provenance.

    The object is treated as untrusted even if it normally came from
    :func:`ingest`. Every text field is re-redacted at the render sink and
    string values are JSON-encoded so arbitrary external ids/titles stay valid
    YAML.
    """
    external_id = redact(memory.external_id)
    source_kind = redact(memory.source_kind)
    title = redact(memory.title)
    content = redact(memory.content)
    trust = redact(memory.trust)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    front = [
        "---",
        f"id: {json.dumps(external_id)}",
        "type: reference",
        f"created: {_to_utc_iso(memory.fetched_at)}",
        "tags:",
        "  - external",
        f"source_kind: {json.dumps(source_kind)}",
        f"external_id: {json.dumps(external_id)}",
        f"content_hash: {content_hash}",
        f"trust: {trust}",
        "---",
    ]
    # Collapse any newlines in the title so a single-line heading cannot break
    # the note structure.
    safe_title = title.replace("\r", " ").replace("\n", " ")
    body = [f"# {safe_title}", "", content]
    return "\n".join(front) + "\n\n" + "\n".join(body) + "\n"


@dataclass
class LocalMarkdownConnector:
    """Reference connector: reads ``*.md`` from a local directory. No network.

    ``enabled`` defaults to ``False`` (opt-in). ``fetch`` reads the files the
    operator pointed at; :func:`ingest` is what gates on ``enabled``.
    """

    root: Path
    name: str = "local-markdown"
    enabled: bool = False

    def fetch(self) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        root_resolved = self.root.resolve()
        for md_path in sorted(self.root.rglob("*.md")):
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
            rel = str(md_path.relative_to(self.root)).replace("\\", "/")
            docs.append(
                SourceDocument(
                    external_id=rel,
                    title=md_path.stem,
                    content=text,
                    source_kind="local-markdown",
                )
            )
        return docs
