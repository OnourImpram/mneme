"""Thin Neo4j credentials helper.

The KG layer reads bolt URL, user, and password from a JSON file at
``VaultConfig.kg_credentials_path``. This module owns the file format
and a tiny round-trip helper so the worker and the install CLI agree
on the shape.

The credentials file is local to the vault state directory and is
read by the worker process only. It is never sent over the wire and
never logged. ``write_credentials`` creates the file atomically at
mode 0o600 on POSIX so no group/world-readable window ever exists.

.. warning:: **Windows — credential file is NOT access-controlled by
   this code.**  ``os.open`` mode bits do not map to NTFS ACLs on
   Windows, so the file inherits default ACLs from its parent
   directory.  Operators on multi-user Windows hosts are responsible
   for restricting the state directory via NTFS permissions (icacls or
   Security tab in Explorer) so that only the owning user account can
   read it.  On POSIX the parent directory is created 0o700, blocking
   directory traversal by other local users entirely.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Neo4jCredentials:
    bolt_url: str
    user: str
    password: str

    def to_dict(self) -> dict[str, str]:
        return {
            "bolt_url": self.bolt_url,
            "user": self.user,
            "password": self.password,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> Neo4jCredentials:
        return cls(
            bolt_url=str(raw["bolt_url"]),
            user=str(raw["user"]),
            password=str(raw["password"]),
        )


def read_credentials(path: Path) -> Neo4jCredentials:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Neo4jCredentials.from_dict(raw)


def write_credentials(path: Path, creds: Neo4jCredentials) -> None:
    """Write *creds* to *path* with no group/world-readable window.

    On POSIX the parent directory is created with mode 0o700 so other
    local users cannot traverse into it.  The credential file itself is
    created (or overwritten) via :func:`os.open` with ``O_CREAT |
    O_TRUNC`` at mode 0o600, which is atomic with respect to the umask:
    the file is never visible at a looser mode, even momentarily.
    :func:`os.chmod` is additionally called on the path after the file is
    closed, to harden a pre-existing inode that ``O_TRUNC`` reuses and that
    might retain a wider mode from a previous write.

    On Windows ``os.open`` mode bits do not set NTFS ACLs.  See the
    module-level warning for operator guidance.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(parent, stat.S_IRWXU)

    payload = json.dumps(creds.to_dict(), ensure_ascii=False, indent=2) + "\n"
    encoded = payload.encode("utf-8")

    if os.name != "nt":
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        # Harden pre-existing inodes: O_TRUNC keeps the inode, so a file
        # previously created at a wider mode retains that mode.  chmod after
        # close locks it down.  The O_CREAT path already set 0o600 atomically.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    else:
        # Windows: write without ACL control; operator must restrict the
        # parent directory via NTFS permissions (see module docstring).
        path.write_bytes(encoded)
