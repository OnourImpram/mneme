"""Thin Neo4j credentials helper.

The KG layer reads bolt URL, user, and password from a JSON file at
``VaultConfig.kg_credentials_path``. This module owns the file format
and a tiny round-trip helper so the worker and the install CLI agree
on the shape.

The credentials file is local to the vault state directory and is
read by the worker process only. It is never sent over the wire and
never logged. ``write_credentials`` enforces ``0600`` permissions on
POSIX so accidental ``ls -la`` does not surface the password.
"""

from __future__ import annotations

import contextlib
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(creds.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
