"""Security tests for write_credentials atomic file creation.

Verifies:
- On POSIX the credential file is created at exactly 0o600 (no readable window).
- The implementation does not use Path.write_text for the credential path.
- The password value never appears in log output or exception text.
"""

from __future__ import annotations

import inspect
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from mneme_core.kg import client as kg_client_module
from mneme_core.kg.client import Neo4jCredentials, write_credentials

_SECRET = "sup3r-s3cr3t-passw0rd"
_CREDS = Neo4jCredentials(
    bolt_url="bolt://localhost:7687",
    user="neo4j",
    password=_SECRET,
)


class TestAtomicMode:
    @pytest.mark.skipif(sys.platform == "win32", reason="0o600 mode check is POSIX-only")
    def test_file_mode_is_0o600_on_posix(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "creds.json"
        write_credentials(path, _CREDS)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    @pytest.mark.skipif(sys.platform == "win32", reason="0o700 dir check is POSIX-only")
    def test_parent_dir_mode_is_0o700_on_posix(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "creds.json"
        write_credentials(path, _CREDS)
        dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"

    def test_file_exists_on_windows(self, tmp_path: Path) -> None:
        """On Windows we at minimum assert the file was created."""
        path = tmp_path / "creds.json"
        write_credentials(path, _CREDS)
        assert path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: overwrite keeps 0o600")
    def test_overwrite_keeps_0o600_on_posix(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.json"
        write_credentials(path, _CREDS)
        # Loosen the mode manually to simulate a pre-existing wide-open file.
        os.chmod(path, 0o644)
        write_credentials(path, _CREDS)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"After overwrite expected 0o600, got {oct(mode)}"


class TestNoWriteText:
    """Assert that write_credentials does not call Path.write_text for the secret file."""

    def test_write_credentials_source_does_not_call_write_text(self) -> None:
        source = inspect.getsource(kg_client_module.write_credentials)
        assert ".write_text(" not in source, (
            "write_credentials must not use Path.write_text — "
            "it would expose the credential under the process umask before chmod."
        )


class TestSecretNotLogged:
    def test_secret_absent_from_log_output(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "creds.json"
        with caplog.at_level(logging.DEBUG):
            write_credentials(path, _CREDS)
        for record in caplog.records:
            assert _SECRET not in record.getMessage(), (
                f"Password leaked into log record: {record.getMessage()!r}"
            )

    def test_secret_absent_from_exception_text(self, tmp_path: Path) -> None:
        # Make the target path unwritable by using a file as the parent dir.
        blocker = tmp_path / "notadir"
        blocker.write_text("x", encoding="utf-8")
        path = blocker / "creds.json"
        try:
            write_credentials(path, _CREDS)
        except OSError as exc:
            assert _SECRET not in str(exc), (
                f"Password leaked into exception text: {exc}"
            )
        except Exception as exc:  # noqa: BLE001
            assert _SECRET not in str(exc), (
                f"Password leaked into exception text: {exc}"
            )
