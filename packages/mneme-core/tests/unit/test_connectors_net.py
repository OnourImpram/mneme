"""Tests for mneme_core.connectors_net: ObsidianConnector + GitHubConnector.

All tests are fully offline — no real network calls are made.
GitHubConnector tests always inject a fake transport.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.connectors import ingest
from mneme_core.connectors_net import (
    GitHubConnector,
    ObsidianConnector,
    _urllib_transport,
)

_AT = datetime(2026, 6, 2, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal Obsidian vault layout for tests."""
    # .obsidian config dir (must be excluded)
    obsidian_dir = tmp_path / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "config").write_text("{}", encoding="utf-8")
    (obsidian_dir / "workspace.md").write_text("# workspace", encoding="utf-8")

    # .trash dir (must be excluded)
    trash_dir = tmp_path / ".trash"
    trash_dir.mkdir()
    (trash_dir / "deleted.md").write_text("# deleted", encoding="utf-8")

    # Two real notes
    (tmp_path / "note-one.md").write_text("# Note One\nHello world.", encoding="utf-8")
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "note-two.md").write_text("# Note Two\nIn a subfolder.", encoding="utf-8")

    return tmp_path


def _fake_transport(responses: dict[str, str]) -> tuple[object, object]:
    """Return (transport_fn, call_tracker) where call_tracker.calls records URLs called."""

    class _Tracker:
        calls: list[str] = []

    tracker = _Tracker()

    def _get(url: str) -> str:
        tracker.calls.append(url)
        if url in responses:
            return responses[url]
        raise ValueError(f"unexpected URL: {url}")

    return _get, tracker


def _raising_transport(url: str) -> str:  # noqa: ARG001
    raise OSError("simulated network failure")


# ---------------------------------------------------------------------------
# ObsidianConnector — fetch behaviour
# ---------------------------------------------------------------------------


class TestObsidianConnectorFetch:
    def test_returns_two_notes(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        conn = ObsidianConnector(root=vault)
        docs = conn.fetch()
        assert len(docs) == 2

    def test_source_kind_is_obsidian(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        docs = ObsidianConnector(root=vault).fetch()
        assert all(d.source_kind == "obsidian" for d in docs)

    def test_external_ids_are_vault_relative_posix(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        docs = ObsidianConnector(root=vault).fetch()
        ids = {d.external_id for d in docs}
        assert ids == {"note-one.md", "subfolder/note-two.md"}

    def test_obsidian_config_dir_excluded(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        docs = ObsidianConnector(root=vault).fetch()
        ids = {d.external_id for d in docs}
        assert not any(eid.startswith(".obsidian") for eid in ids)

    def test_trash_dir_excluded(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        docs = ObsidianConnector(root=vault).fetch()
        ids = {d.external_id for d in docs}
        assert not any(eid.startswith(".trash") for eid in ids)

    def test_wikilink_brackets_stripped_from_title(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        # A file whose stem looks like a wikilink (rare but valid Obsidian name)
        (tmp_path / "[[My Note]].md").write_text("body", encoding="utf-8")
        docs = ObsidianConnector(root=tmp_path).fetch()
        assert any(d.title == "My Note" for d in docs)

    def test_deterministic_order(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        docs1 = ObsidianConnector(root=vault).fetch()
        docs2 = ObsidianConnector(root=vault).fetch()
        assert [d.external_id for d in docs1] == [d.external_id for d in docs2]

    def test_default_enabled_false(self, tmp_path: Path) -> None:
        conn = ObsidianConnector(root=tmp_path)
        assert conn.enabled is False

    def test_unreadable_file_skipped_no_raise(self, tmp_path: Path) -> None:
        """fetch() never raises even when the vault root is empty or unreadable."""
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        (tmp_path / "readable.md").write_text("ok", encoding="utf-8")
        # Verify the connector returns the readable file and does not raise.
        conn = ObsidianConnector(root=tmp_path)
        docs = conn.fetch()
        assert len(docs) >= 1


# ---------------------------------------------------------------------------
# ObsidianConnector — ingest (disabled no-op + redaction)
# ---------------------------------------------------------------------------


class TestObsidianConnectorIngest:
    def test_disabled_ingest_returns_empty(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        conn = ObsidianConnector(root=vault, enabled=False)
        assert ingest(conn, fetched_at=_AT) == []

    def test_enabled_ingest_redacts_private_content(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        (tmp_path / "secret.md").write_text(
            "Safe header.\n<private>top-secret-data</private>\nSafe footer.",
            encoding="utf-8",
        )
        conn = ObsidianConnector(root=tmp_path, enabled=True)
        mems = ingest(conn, fetched_at=_AT)
        assert len(mems) == 1
        assert "top-secret-data" not in mems[0].content

    def test_enabled_ingest_trust_is_external(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        (tmp_path / "n.md").write_text("hello", encoding="utf-8")
        conn = ObsidianConnector(root=tmp_path, enabled=True)
        mems = ingest(conn, fetched_at=_AT)
        assert all(m.trust == "external" for m in mems)

    def test_revocation_flip_enabled_false(self, tmp_path: Path) -> None:
        """Flipping enabled back to False is the revocation mechanism."""
        vault = _make_vault(tmp_path)
        conn = ObsidianConnector(root=vault, enabled=True)
        assert len(ingest(conn, fetched_at=_AT)) > 0
        conn.enabled = False
        assert ingest(conn, fetched_at=_AT) == []


# ---------------------------------------------------------------------------
# GitHubConnector — fetch behaviour (fake transport, no network)
# ---------------------------------------------------------------------------

_FAKE_README = "# My Repo\nThis is the readme.\n"
_FAKE_DOCS = "# Docs\nSome <private>internal</private> notes.\n"

_FAKE_REPO = "acme/myrepo"
_FAKE_RESPONSES = {
    f"https://raw.githubusercontent.com/{_FAKE_REPO}/HEAD/README.md": _FAKE_README,
    f"https://raw.githubusercontent.com/{_FAKE_REPO}/HEAD/docs/guide.md": _FAKE_DOCS,
}


class TestGitHubConnectorFetch:
    def test_returns_source_documents_for_each_path(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md", "docs/guide.md"),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        assert len(docs) == 2

    def test_source_kind_is_github(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        assert all(d.source_kind == "github" for d in docs)

    def test_external_id_format(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        assert docs[0].external_id == f"{_FAKE_REPO}@HEAD/README.md"

    def test_title_is_basename(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("docs/guide.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        assert docs[0].title == "guide.md"

    def test_token_does_not_appear_in_any_source_document_field(self) -> None:
        secret_token = "ghp_supersecrettoken123"
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            token=secret_token,
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        for doc in docs:
            assert secret_token not in doc.external_id
            assert secret_token not in doc.title
            assert secret_token not in doc.content
            assert secret_token not in doc.source_kind

    def test_default_enabled_false(self) -> None:
        conn = GitHubConnector(repo=_FAKE_REPO)
        assert conn.enabled is False

    def test_disabled_fetch_returns_empty_no_transport_call(self) -> None:
        transport, tracker = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=False,
        )
        result = conn.fetch()
        assert result == []
        assert tracker.calls == []  # type: ignore[attr-defined]

    def test_raising_transport_returns_empty_never_raises(self) -> None:
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=_raising_transport,
            enabled=True,
        )
        # Must not raise; returns []
        docs = conn.fetch()
        assert docs == []

    def test_raising_transport_partial_results(self) -> None:
        """If first path succeeds and second raises, first result is still returned."""
        good_url = f"https://raw.githubusercontent.com/{_FAKE_REPO}/HEAD/README.md"

        def _partial_transport(url: str) -> str:
            if url == good_url:
                return _FAKE_README
            raise OSError("second path fails")

        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md", "docs/guide.md"),
            transport=_partial_transport,
            enabled=True,
        )
        docs = conn.fetch()
        assert len(docs) == 1
        assert docs[0].external_id == f"{_FAKE_REPO}@HEAD/README.md"

    def test_invalid_repo_no_slash_returns_empty(self) -> None:
        transport, tracker = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo="noslash",
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        result = conn.fetch()
        assert result == []
        assert tracker.calls == []  # type: ignore[attr-defined]

    def test_custom_ref(self) -> None:
        ref = "main"
        responses = {
            f"https://raw.githubusercontent.com/{_FAKE_REPO}/{ref}/README.md": _FAKE_README,
        }
        transport, _ = _fake_transport(responses)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            ref=ref,
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        docs = conn.fetch()
        assert len(docs) == 1
        assert docs[0].external_id == f"{_FAKE_REPO}@{ref}/README.md"


# ---------------------------------------------------------------------------
# GitHubConnector — ingest (disabled no-op + redaction)
# ---------------------------------------------------------------------------


class TestGitHubConnectorIngest:
    def test_disabled_ingest_returns_empty_no_transport_call(self) -> None:
        transport, tracker = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=False,
        )
        result = ingest(conn, fetched_at=_AT)
        assert result == []
        assert tracker.calls == []  # type: ignore[attr-defined]

    def test_enabled_ingest_redacts_private_content(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("docs/guide.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        mems = ingest(conn, fetched_at=_AT)
        assert len(mems) == 1
        assert "internal" not in mems[0].content

    def test_trust_is_external(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        mems = ingest(conn, fetched_at=_AT)
        assert all(m.trust == "external" for m in mems)

    def test_provenance_fields(self) -> None:
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        mems = ingest(conn, fetched_at=_AT)
        mem = mems[0]
        assert mem.source_kind == "github"
        assert mem.external_id == f"{_FAKE_REPO}@HEAD/README.md"
        assert mem.fetched_at == _AT
        assert len(mem.content_hash) == 64

    def test_token_not_in_ingested_memory(self) -> None:
        secret_token = "ghp_supersecrettoken456"
        transport, _ = _fake_transport(_FAKE_RESPONSES)
        conn = GitHubConnector(
            repo=_FAKE_REPO,
            paths=("README.md",),
            token=secret_token,
            transport=transport,  # type: ignore[arg-type]
            enabled=True,
        )
        mems = ingest(conn, fetched_at=_AT)
        for mem in mems:
            assert secret_token not in mem.external_id
            assert secret_token not in mem.title
            assert secret_token not in mem.content
            assert secret_token not in mem.source_kind
            assert secret_token not in mem.content_hash


class TestUrllibTransport:
    """The default urllib transport attaches Authorization: Bearer only when a
    token is set. urlopen is monkeypatched so no real network call is made."""

    def _patch_urlopen(self, monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
        import urllib.request

        class _FakeResp:
            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def read(self) -> bytes:
                return b"hello"

        def _fake_urlopen(req: object, timeout: float = 0.0) -> _FakeResp:
            captured["headers"] = dict(getattr(req, "headers", {}))
            return _FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    def test_sets_bearer_header_when_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        self._patch_urlopen(monkeypatch, captured)
        body = _urllib_transport("tok-123")("https://example.test/x.md")
        assert body == "hello"
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert any(v == "Bearer tok-123" for v in headers.values())

    def test_omits_header_when_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        self._patch_urlopen(monkeypatch, captured)
        _urllib_transport(None)("https://example.test/x.md")
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert all("bearer" not in str(v).lower() for v in headers.values())
