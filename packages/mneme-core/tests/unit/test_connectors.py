"""Tests for mneme_core.connectors: opt-in ingest framework + local reference."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from mneme_core.connectors import (
    IngestedMemory,
    LocalMarkdownConnector,
    SourceDocument,
    ingest,
    to_markdown,
)

_AT = datetime(2026, 6, 2, tzinfo=UTC)


class _StubConnector:
    """A minimal Connector for ingest tests."""

    def __init__(self, docs: list[SourceDocument], *, enabled: bool) -> None:
        self.name = "stub"
        self.enabled = enabled
        self._docs = docs

    def fetch(self) -> list[SourceDocument]:
        return self._docs


def _docs() -> list[SourceDocument]:
    return [
        SourceDocument(
            external_id="ext-1",
            title="A note",
            content="body with <private>topsecret</private> inside",
            source_kind="stub",
        )
    ]


class TestOptIn:
    def test_disabled_connector_ingests_nothing(self) -> None:
        conn = _StubConnector(_docs(), enabled=False)
        assert ingest(conn, fetched_at=_AT) == []

    def test_local_markdown_connector_defaults_disabled(self, tmp_path: Path) -> None:
        assert LocalMarkdownConnector(root=tmp_path).enabled is False


class TestIngest:
    def test_enabled_ingest_redacts_content(self) -> None:
        conn = _StubConnector(_docs(), enabled=True)
        mems = ingest(conn, fetched_at=_AT)
        assert len(mems) == 1
        assert "topsecret" not in mems[0].content

    def test_provenance_fields(self) -> None:
        conn = _StubConnector(_docs(), enabled=True)
        mem = ingest(conn, fetched_at=_AT)[0]
        assert mem.external_id == "ext-1"
        assert mem.source_kind == "stub"
        assert mem.trust == "external"
        assert len(mem.content_hash) == 64
        assert mem.fetched_at == _AT


class TestToMarkdown:
    def test_markdown_has_external_provenance(self) -> None:
        conn = _StubConnector(_docs(), enabled=True)
        md = to_markdown(ingest(conn, fetched_at=_AT)[0])
        assert "type: reference" in md
        assert "trust: external" in md
        assert "source_kind:" in md
        assert "topsecret" not in md

    def test_markdown_created_is_utc(self) -> None:
        conn = _StubConnector(_docs(), enabled=True)
        md = to_markdown(ingest(conn, fetched_at=_AT)[0])
        assert "created: 2026-06-02T00:00:00.000000+00:00" in md

    def test_reredacts_manually_constructed_memory_at_render_sink(self) -> None:
        memory = IngestedMemory(
            external_id="id-<private>ID_SECRET</private>",
            source_kind="<private>KIND_SECRET</private>",
            title="Title <private>TITLE_SECRET</private>",
            content="Body <private>CONTENT_SECRET</private>",
            content_hash="<private>HASH_SECRET</private>",
            fetched_at=_AT,
            trust="<private>TRUST_SECRET</private>",
        )

        markdown = to_markdown(memory)

        for secret in (
            "ID_SECRET",
            "KIND_SECRET",
            "TITLE_SECRET",
            "CONTENT_SECRET",
            "HASH_SECRET",
            "TRUST_SECRET",
        ):
            assert secret not in markdown
        expected_hash = hashlib.sha256(b"Body [REDACTED]").hexdigest()
        assert f"content_hash: {expected_hash}" in markdown


class TestLocalMarkdownConnector:
    def test_fetch_reads_local_files(self, tmp_path: Path) -> None:
        (tmp_path / "one.md").write_text("# one\nhello", encoding="utf-8")
        (tmp_path / "two.md").write_text("# two\nworld", encoding="utf-8")
        conn = LocalMarkdownConnector(root=tmp_path, enabled=True)
        docs = conn.fetch()
        ids = {d.external_id for d in docs}
        assert ids == {"one.md", "two.md"}
        assert all(d.source_kind == "local-markdown" for d in docs)

    def test_end_to_end_local_ingest(self, tmp_path: Path) -> None:
        (tmp_path / "n.md").write_text(
            "secret <private>leak</private> here", encoding="utf-8"
        )
        conn = LocalMarkdownConnector(root=tmp_path, enabled=True)
        mems = ingest(conn, fetched_at=_AT)
        assert len(mems) == 1
        assert "leak" not in mems[0].content
        assert mems[0].trust == "external"
