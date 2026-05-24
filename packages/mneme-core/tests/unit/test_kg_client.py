"""Tests for the Neo4j credentials helper."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mneme_core.kg.client import (
    Neo4jCredentials,
    read_credentials,
    write_credentials,
)


class TestRoundTrip:
    def test_write_then_read_recovers_fields(self, tmp_path: Path) -> None:
        creds = Neo4jCredentials(
            bolt_url="bolt://localhost:7687",
            user="neo4j",
            password="passw0rd",
        )
        path = tmp_path / "creds.json"
        write_credentials(path, creds)
        loaded = read_credentials(path)
        assert loaded == creds

    def test_writes_human_readable_json(self, tmp_path: Path) -> None:
        creds = Neo4jCredentials(
            bolt_url="bolt://x", user="u", password="p"
        )
        path = tmp_path / "c.json"
        write_credentials(path, creds)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"bolt_url": "bolt://x", "user": "u", "password": "p"}

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only permissions")
    def test_posix_permissions_are_user_only(self, tmp_path: Path) -> None:
        creds = Neo4jCredentials(bolt_url="b", user="u", password="p")
        path = tmp_path / "c.json"
        write_credentials(path, creds)
        mode = stat.S_IMODE(path.stat().st_mode)
        # Owner read/write, no group/other bits.
        assert mode == (stat.S_IRUSR | stat.S_IWUSR)

    def test_from_dict_validates_required_fields(self) -> None:
        with pytest.raises(KeyError):
            Neo4jCredentials.from_dict({"bolt_url": "x", "user": "u"})  # type: ignore[arg-type]
