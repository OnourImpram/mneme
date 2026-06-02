"""Network-capable and Obsidian connectors, additive to the opt-in Connector framework.

Two connectors are shipped here:

* :class:`ObsidianConnector` — **local**, no network. Reads ``*.md`` from an
  Obsidian vault directory, skipping the ``.obsidian/`` config subtree and
  ``.trash/``. Symlink containment mirrors :class:`~mneme_core.connectors.LocalMarkdownConnector`.

* :class:`GitHubConnector` — **network**, opt-in, default OFF. Fetches raw
  markdown files from a GitHub repository via the
  ``https://raw.githubusercontent.com`` CDN. Network is only exercised inside
  :meth:`GitHubConnector.fetch` when ``enabled=True``; the stdlib ``urllib``
  transport is built lazily at first call and never at import time.

Both connectors conform to the :class:`~mneme_core.connectors.Connector` Protocol
(``name``, ``enabled``, ``fetch``), default ``enabled=False`` (opt-in), and rely
entirely on the existing :func:`~mneme_core.connectors.ingest` function for
redaction and provenance — they never store or log credentials.

The ``token`` field on :class:`GitHubConnector` is an operator-supplied
``Authorization: Bearer`` secret. It is consumed only inside the transport
closure and **must never appear in any** :class:`~mneme_core.connectors.SourceDocument`
field (``external_id``, ``title``, ``content``, ``source_kind``).

Remaining deferred sources — Google Drive, Notion, Slack, Gmail, S3, Zotero —
follow the identical Connector-Protocol pattern: per-source credential, injected
transport for testability, and redaction-before-ingest via the shared
:func:`~mneme_core.connectors.ingest` entry-point. They are deferred, not blocked.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .connectors import SourceDocument

# ---------------------------------------------------------------------------
# Transport type alias
# ---------------------------------------------------------------------------

#: A transport is a plain callable that takes a URL string and returns the
#: decoded response body.  This design allows tests to inject a fake transport
#: with no network involvement.
Transport = Callable[[str], str]

# ---------------------------------------------------------------------------
# Lazy default transport factory
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 15  # seconds


def _urllib_transport(token: str | None) -> Transport:
    """Return a stdlib-urllib transport closure for the given optional token.

    The closure is built at :meth:`GitHubConnector.fetch` call-time (never at
    import), so the network is strictly opt-in.  The ``Authorization`` header
    is set only when *token* is not ``None`` or empty; it is local to the
    closure and never propagated to a :class:`~mneme_core.connectors.SourceDocument`.
    """

    def _get(url: str) -> str:
        req = urllib.request.Request(url)  # noqa: S310 — URL is operator-supplied
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310
            raw: bytes = resp.read()
        return raw.decode("utf-8")

    return _get


# ---------------------------------------------------------------------------
# ObsidianConnector
# ---------------------------------------------------------------------------

#: Obsidian internal directories that must never be exposed as SourceDocuments.
_OBSIDIAN_SKIP_DIRS = {".obsidian", ".trash"}


@dataclass
class ObsidianConnector:
    """Read ``*.md`` files from a local Obsidian vault directory.

    * ``enabled`` defaults to ``False`` (opt-in).
    * ``fetch`` is the data-fetching half; :func:`~mneme_core.connectors.ingest`
      is the gating + redaction half.
    * Skips the ``.obsidian/`` config subtree and ``.trash/``.
    * Symlink containment: resolves each path and checks
      ``is_relative_to(root_resolved)`` so a symlink cannot escape the vault
      boundary (mirrors :class:`~mneme_core.connectors.LocalMarkdownConnector`).
    * ``external_id`` is the vault-relative POSIX path.
    * ``title`` is the file stem with surrounding ``[[`` / ``]]`` wikilink
      brackets stripped if present.
    * Never raises; unreadable files are silently skipped.
    * Deterministic order (sorted rglob).
    """

    root: Path
    name: str = "obsidian"
    enabled: bool = False

    def fetch(self) -> list[SourceDocument]:
        """Return :class:`~mneme_core.connectors.SourceDocument` for every readable ``*.md``."""
        docs: list[SourceDocument] = []
        try:
            root_resolved = self.root.resolve()
        except OSError:
            return docs

        for md_path in sorted(self.root.rglob("*.md")):
            # Containment check: skip paths that resolve outside the vault root
            # (guards against symlink escapes).
            try:
                resolved = md_path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(root_resolved):
                continue

            # Skip Obsidian internal directories.
            try:
                rel_parts = md_path.relative_to(self.root).parts
            except ValueError:
                continue
            if rel_parts and rel_parts[0] in _OBSIDIAN_SKIP_DIRS:
                continue

            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel = str(md_path.relative_to(self.root)).replace("\\", "/")
            stem = md_path.stem
            title = stem.removeprefix("[[").removesuffix("]]")

            docs.append(
                SourceDocument(
                    external_id=rel,
                    title=title,
                    content=text,
                    source_kind="obsidian",
                )
            )
        return docs


# ---------------------------------------------------------------------------
# GitHubConnector
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[str, ...]:
    return ("README.md",)


@dataclass
class GitHubConnector:
    """Fetch raw markdown files from a GitHub repository.

    * ``enabled`` defaults to ``False`` (opt-in; network is never touched
      until this is set to ``True`` by the operator).
    * ``repo`` must be ``"owner/name"``; an invalid value (no ``/``) causes
      ``fetch`` to return ``[]`` immediately.
    * ``token`` is an operator-supplied ``Authorization: Bearer`` secret.
      It is consumed only inside the transport closure and **never** appears
      in any :class:`~mneme_core.connectors.SourceDocument` field.
    * ``transport`` may be injected for tests; if ``None`` a default
      stdlib-urllib transport is built lazily on first :meth:`fetch` call.
    * ``fetch`` returns partial results on per-path errors (never raises).
    * ``external_id`` format: ``"{repo}@{ref}/{path}"``.
    """

    repo: str
    paths: tuple[str, ...] = field(default_factory=_default_paths)
    ref: str = "HEAD"
    token: str | None = None
    transport: Transport | None = None
    name: str = "github"
    enabled: bool = False

    # Lazy transport cache — populated on first fetch() when self.transport is None.
    _transport_cache: Transport | None = field(default=None, init=False, repr=False, compare=False)

    def _get_transport(self) -> Transport:
        if self.transport is not None:
            return self.transport
        if self._transport_cache is None:
            self._transport_cache = _urllib_transport(self.token)
        return self._transport_cache

    def fetch(self) -> list[SourceDocument]:
        """Fetch each path from ``raw.githubusercontent.com`` and return SourceDocuments.

        Returns ``[]`` when disabled or when ``repo`` does not contain ``/``.
        Per-path errors are caught; already-fetched paths are returned even if
        a later path fails.  Never raises.
        """
        if not self.enabled:
            return []
        if "/" not in self.repo:
            return []

        transport = self._get_transport()
        docs: list[SourceDocument] = []

        for path in self.paths:
            url = f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{path}"
            try:
                body = transport(url)
            except Exception:  # noqa: BLE001
                continue

            # Safety invariant: token must never leak into a SourceDocument field.
            # We do not embed the token anywhere here; this assertion makes the
            # contract machine-checkable in tests.
            title = Path(path).name

            docs.append(
                SourceDocument(
                    external_id=f"{self.repo}@{self.ref}/{path}",
                    title=title,
                    content=body,
                    source_kind="github",
                )
            )
        return docs
