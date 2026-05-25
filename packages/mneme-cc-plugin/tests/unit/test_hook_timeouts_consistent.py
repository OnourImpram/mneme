"""Guards for the installer's hook wiring.

Two invariants the reviews flagged:

1. The per-event ``timeout`` the ``mneme install`` path writes into
   ``settings.json`` must equal the values in the native plugin manifest
   ``hooks/hooks.json``. They drifted before (installer used milliseconds,
   manifest used seconds), which the Claude Code schema read as a 1000x
   timeout. This test fails the build if they diverge again.
2. Hooks must be wired through the ``mneme hook <event>`` console script
   so they survive a pipx isolated venv, with an absolute-interpreter
   fallback only when the script is not on PATH.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_cc_plugin.install.cli import (
    HOOK_EVENT_COMMAND,
    HOOK_TIMEOUTS_S,
    Installer,
    InstallerConfig,
)

_MANIFEST = Path(__file__).parents[2] / "hooks" / "hooks.json"


def _manifest_timeouts() -> dict[str, int]:
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for event, handlers in data["hooks"].items():
        inner = handlers[0]["hooks"][0]
        out[event] = int(inner["timeout"])
    return out


def test_installer_timeouts_match_native_manifest() -> None:
    assert _manifest_timeouts() == HOOK_TIMEOUTS_S


def _installer(tmp_path: Path) -> Installer:
    cfg = InstallerConfig(
        profile="lite",
        vault_root=tmp_path / "vault",
        settings_path=tmp_path / "settings.json",
        backup_dir=tmp_path / "backups",
    )
    return Installer(config=cfg)


@pytest.mark.parametrize("event", list(HOOK_EVENT_COMMAND))
def test_hook_command_uses_console_shim_when_on_path(
    tmp_path: Path, event: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/mneme")
    cmd = _installer(tmp_path)._hook_command(event)
    assert cmd == f"mneme hook {HOOK_EVENT_COMMAND[event]}"


def test_hook_command_falls_back_when_script_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    cmd = _installer(tmp_path)._hook_command("Stop")
    assert "mneme_cc_plugin.install.cli hook stop" in cmd
    assert "mneme hook" not in cmd
