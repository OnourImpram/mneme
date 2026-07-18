"""Regression test: fts5_search closes the SQLite connection on all paths.

Covers the bug where the ``except`` branches returned early, bypassing
``conn.close()`` and leaking the connection handle.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch


class _TrackingConnection:
    """Thin wrapper around a real sqlite3.Connection that records close calls."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.close_called: bool = False

    def close(self) -> None:
        self.close_called = True
        self._real.close()

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._real.execute(sql, params)

    def __enter__(self) -> _TrackingConnection:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _make_patched_connect(
    wrapper_store: list[_TrackingConnection],
) -> Any:
    """Return a replacement for ``sqlite3.connect`` that wraps the result."""
    real_connect = sqlite3.connect

    def _connect(database: str, **kwargs: Any) -> _TrackingConnection:
        real_conn = real_connect(database, **kwargs)
        wrapper = _TrackingConnection(real_conn)
        wrapper_store.append(wrapper)
        return wrapper

    return _connect


class TestFts5SearchConnectionLeak:
    """Verify that the SQLite connection is always closed by fts5_search."""

    def test_close_called_on_operational_error(self, tmp_path: Path) -> None:
        """OperationalError path must close the connection and return []."""
        # Create a valid SQLite file that lacks the expected FTS5 tables, so
        # any attempt to query documents_fts will raise OperationalError.
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        wrappers: list[_TrackingConnection] = []

        with patch("mneme_core.retrieval.rrf.sqlite3.connect", _make_patched_connect(wrappers)):
            from mneme_core.retrieval.rrf import fts5_search

            result = fts5_search(
                "this query has enough tokens to pass filtering",
                db_path,
                scope="*",
            )

        assert result == [], "fts5_search must return [] on OperationalError"
        assert len(wrappers) == 1, "exactly one connection should have been opened"
        assert wrappers[0].close_called, "close() must run on the error path"

    def test_close_called_on_success(self, tmp_path: Path) -> None:
        """Happy path must also close the connection."""
        db_path = tmp_path / "fts.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(title, content, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE documents ("
            "id INTEGER PRIMARY KEY, path TEXT, title TEXT, "
            "content_hash TEXT, trust TEXT, scope TEXT)"
        )
        conn.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a/b.md', 'Hello world', 'hash', 'user', 'default')"
        )
        conn.execute(
            "INSERT INTO documents_fts(rowid, title, content) "
            "VALUES (1, 'Hello world', 'some content')"
        )
        conn.commit()
        conn.close()

        wrappers: list[_TrackingConnection] = []

        with patch("mneme_core.retrieval.rrf.sqlite3.connect", _make_patched_connect(wrappers)):
            from mneme_core.retrieval.rrf import fts5_search

            # Use a query long enough to pass the build_fts5_query filter
            fts5_search("Hello world content retrieval query", db_path)

        assert len(wrappers) == 1, "exactly one connection should have been opened"
        assert wrappers[0].close_called, "connection.close() must be called on success path"
