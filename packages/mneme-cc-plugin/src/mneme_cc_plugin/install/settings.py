"""BOM-safe Claude Code settings.json mutation.

Claude Code on Windows sometimes writes ``settings.json`` with a UTF-8
BOM. Tooling that uses ``json.load`` plus ``json.dump`` silently strips
the BOM, which then breaks the next read by something that expects it.
This module reads with ``utf-8-sig`` (BOM-aware decode) and writes with
the original BOM state preserved exactly.

The write path follows a backup-validate-replace discipline:

1. Copy the current target to a timestamped backup in ``backup_dir``.
2. Serialize into a sibling ``.tmp`` file with the same encoding.
3. Re-parse the ``.tmp`` to confirm the JSON is valid.
4. On validation failure, delete ``.tmp`` and raise. The original
   file is never touched.
5. On success, ``Path.replace`` the original with the validated tmp.
   ``replace`` is atomic on POSIX and on Windows (overwriting an
   existing file is supported by the syscall, unlike ``rename``).

Hook insertion is idempotent and append-style: existing entries with
the same command string are reused, mneme entries are never
duplicated, and user hook entries from other plugins are preserved.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UTF8_BOM = b"\xef\xbb\xbf"


class SettingsMutationError(RuntimeError):
    """Raised when a mutation could not be applied safely."""


@dataclass(frozen=True)
class MutationResult:
    """Outcome of a settings.json mutation."""

    backup_path: Path
    bom_preserved: bool
    operation: str


def file_has_bom(path: Path) -> bool:
    """Return True if ``path`` starts with the UTF-8 BOM bytes."""
    if not path.exists():
        return False
    with path.open("rb") as f:
        head = f.read(3)
    return head == UTF8_BOM


def read_settings(path: Path) -> dict[str, Any]:
    """Read ``path`` as JSON, transparently handling an optional BOM.

    Raises FileNotFoundError if the file does not exist. The caller
    decides whether absence is an error.
    """
    if not path.exists():
        raise FileNotFoundError(f"settings file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SettingsMutationError(
            f"settings root must be a JSON object, got {type(data).__name__}"
        )
    return data


def write_settings(
    path: Path,
    data: dict[str, Any],
    *,
    backup_dir: Path,
    reason: str = "mneme-mutation",
    preserve_bom: bool | None = None,
) -> MutationResult:
    """Backup, validate, and atomically replace ``path`` with ``data``.

    Args:
        path: target settings file. Must already exist if
            ``preserve_bom`` is None.
        data: parsed JSON to serialize.
        backup_dir: directory where a timestamped backup is stored.
            Created if missing.
        reason: short label embedded into the backup filename for
            traceability.
        preserve_bom: if True, write with BOM. If False, write without.
            If None, the current state of ``path`` is detected and
            preserved.

    Returns:
        MutationResult with the backup path, whether the BOM was
        preserved, and the operation label.

    Raises:
        SettingsMutationError if the produced tmp file fails to parse.
    """
    if preserve_bom is None:
        if not path.exists():
            raise SettingsMutationError(
                "preserve_bom must be set explicitly when target does not exist."
            )
        preserve_bom = file_has_bom(path)

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{path.name}.bak-{reason}-{ts}"
    if path.exists():
        shutil.copy2(path, backup_path)

    encoding = "utf-8-sig" if preserve_bom else "utf-8"
    tmp_path = path.with_suffix(path.suffix + f".tmp-{ts}")
    with tmp_path.open("w", encoding=encoding) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    try:
        with tmp_path.open("r", encoding="utf-8-sig") as f:
            json.load(f)
    except json.JSONDecodeError as e:
        tmp_path.unlink(missing_ok=True)
        raise SettingsMutationError(
            f"mutation produced invalid JSON: {e}"
        ) from e

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.replace(path)

    return MutationResult(
        backup_path=backup_path,
        bom_preserved=preserve_bom,
        operation=reason,
    )


def _ensure_hooks_map(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsMutationError(
            f"settings.hooks must be an object, got {type(hooks).__name__}"
        )
    return hooks


def add_hook(
    data: dict[str, Any],
    event: str,
    command: str,
    *,
    matcher: str | None = None,
    timeout_s: int = 10,
    tag: str = "mneme",
) -> bool:
    """Append a hook entry to ``data['hooks'][event]`` if not present.

    The mutation is in-place. Returns True if the entry was added,
    False if an identical command was already wired.

    ``timeout_s`` is written verbatim into the entry's ``timeout`` field,
    which the Claude Code hook schema interprets in SECONDS.

    `tag` is stamped into the entry as `_mneme_tag` so a future
    ``remove_hooks`` pass can identify mneme entries even after the
    command string changes between versions.
    """
    hooks = _ensure_hooks_map(data)
    handlers = hooks.setdefault(event, [])
    if not isinstance(handlers, list):
        raise SettingsMutationError(
            f"settings.hooks.{event} must be an array, got {type(handlers).__name__}"
        )

    for h in handlers:
        if not isinstance(h, dict):
            continue
        inner_hooks = h.get("hooks") or []
        for entry in inner_hooks:
            if isinstance(entry, dict) and entry.get("command") == command:
                return False

    new_entry: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout_s,
                "_mneme_tag": tag,
            }
        ]
    }
    if matcher is not None:
        new_entry["matcher"] = matcher
    handlers.append(new_entry)
    return True


def remove_hooks(data: dict[str, Any], *, tag: str = "mneme") -> int:
    """Remove every hook entry whose `_mneme_tag` matches ``tag``.

    Handlers whose entry list becomes empty are dropped entirely. The
    enclosing ``hooks`` map is left in place even if it becomes empty
    to keep diffs minimal.

    Returns the count of entries removed.
    """
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0

    removed = 0
    for event, handlers in list(hooks.items()):
        if not isinstance(handlers, list):
            continue
        new_handlers: list[dict[str, Any]] = []
        for h in handlers:
            if not isinstance(h, dict):
                new_handlers.append(h)
                continue
            inner = h.get("hooks") or []
            kept_inner = [
                e
                for e in inner
                if not (isinstance(e, dict) and e.get("_mneme_tag") == tag)
            ]
            removed += len(inner) - len(kept_inner)
            if kept_inner:
                new_handler = dict(h)
                new_handler["hooks"] = kept_inner
                new_handlers.append(new_handler)
        hooks[event] = new_handlers
    return removed


def add_mcp_server(
    data: dict[str, Any],
    server_name: str,
    *,
    command: str,
    args: Iterable[str] = (),
    env: dict[str, str] | None = None,
    transport: str = "stdio",
) -> bool:
    """Wire an MCP server entry if missing or replace it if present.

    Returns True if an entry was added or replaced, False if the
    existing entry already matched the requested shape exactly.
    """
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SettingsMutationError(
            f"settings.mcpServers must be an object, got {type(servers).__name__}"
        )

    desired: dict[str, Any] = {
        "type": transport,
        "command": command,
        "args": list(args),
    }
    if env is not None:
        desired["env"] = dict(env)

    if servers.get(server_name) == desired:
        return False
    servers[server_name] = desired
    return True


def remove_mcp_server(data: dict[str, Any], server_name: str) -> bool:
    """Drop an MCP server entry. Returns True if anything was removed."""
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    if server_name not in servers:
        return False
    del servers[server_name]
    return True
