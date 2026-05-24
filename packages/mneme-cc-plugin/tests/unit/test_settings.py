"""Unit tests for the BOM-safe settings.json mutation layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_cc_plugin.install.settings import (
    UTF8_BOM,
    SettingsMutationError,
    add_hook,
    add_mcp_server,
    file_has_bom,
    read_settings,
    remove_hooks,
    remove_mcp_server,
    write_settings,
)


def _write_with_bom(path: Path, payload: dict[str, object]) -> None:
    raw = json.dumps(payload, indent=2)
    with path.open("wb") as f:
        f.write(UTF8_BOM)
        f.write(raw.encode("utf-8"))


def _write_without_bom(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class TestFileHasBom:
    def test_returns_true_for_file_with_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_with_bom(target, {"hello": "world"})
        assert file_has_bom(target) is True

    def test_returns_false_for_file_without_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_without_bom(target, {"hello": "world"})
        assert file_has_bom(target) is False

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        assert file_has_bom(tmp_path / "absent.json") is False


class TestReadSettings:
    def test_reads_bom_file(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_with_bom(target, {"a": 1})
        assert read_settings(target) == {"a": 1}

    def test_reads_no_bom_file(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_without_bom(target, {"a": 2})
        assert read_settings(target) == {"a": 2}

    def test_raises_on_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_settings(tmp_path / "missing.json")

    def test_rejects_non_object_root(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        target.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(SettingsMutationError):
            read_settings(target)


class TestWriteSettings:
    def test_preserves_bom_when_present(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_with_bom(target, {"k": "v"})
        result = write_settings(
            target,
            {"k": "v2"},
            backup_dir=tmp_path / "bak",
        )
        assert result.bom_preserved is True
        assert file_has_bom(target) is True
        assert read_settings(target)["k"] == "v2"

    def test_preserves_absence_of_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_without_bom(target, {"k": "v"})
        result = write_settings(
            target,
            {"k": "v3"},
            backup_dir=tmp_path / "bak",
        )
        assert result.bom_preserved is False
        assert file_has_bom(target) is False

    def test_backup_file_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / "s.json"
        _write_with_bom(target, {"k": "v"})
        result = write_settings(
            target, {"k": "v2"}, backup_dir=tmp_path / "bak", reason="r1"
        )
        assert result.backup_path.exists()
        assert "r1" in result.backup_path.name

    def test_validation_failure_does_not_touch_original(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "s.json"
        _write_without_bom(target, {"k": "v"})
        original_bytes = target.read_bytes()

        class _BadlySerializable:
            pass

        with pytest.raises(TypeError):
            write_settings(
                target,
                {"k": _BadlySerializable()},  # type: ignore[dict-item]
                backup_dir=tmp_path / "bak",
            )
        assert target.read_bytes() == original_bytes

    def test_requires_explicit_bom_when_target_missing(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "new.json"
        with pytest.raises(SettingsMutationError):
            write_settings(target, {"k": "v"}, backup_dir=tmp_path / "bak")

    def test_writes_new_file_with_explicit_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "new.json"
        write_settings(
            target,
            {"k": "v"},
            backup_dir=tmp_path / "bak",
            preserve_bom=True,
        )
        assert file_has_bom(target) is True

    def test_writes_new_file_without_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "new.json"
        write_settings(
            target,
            {"k": "v"},
            backup_dir=tmp_path / "bak",
            preserve_bom=False,
        )
        assert file_has_bom(target) is False


class TestAddHook:
    def test_appends_new_event_handler(self) -> None:
        data: dict[str, object] = {}
        added = add_hook(data, "Stop", "echo stop")
        assert added is True
        assert data["hooks"] == {  # type: ignore[index]
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo stop",
                            "timeout": 5000,
                            "_mneme_tag": "mneme",
                        }
                    ]
                }
            ]
        }

    def test_is_idempotent_for_same_command(self) -> None:
        data: dict[str, object] = {}
        add_hook(data, "Stop", "echo stop")
        added_again = add_hook(data, "Stop", "echo stop")
        assert added_again is False
        # Still exactly one entry.
        assert len(data["hooks"]["Stop"]) == 1  # type: ignore[index]

    def test_appends_alongside_foreign_entries(self) -> None:
        data: dict[str, object] = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "foreign"}]}
                ]
            }
        }
        added = add_hook(data, "Stop", "mneme stop")
        assert added is True
        handlers = data["hooks"]["Stop"]  # type: ignore[index]
        assert len(handlers) == 2

    def test_matcher_and_custom_timeout(self) -> None:
        data: dict[str, object] = {}
        add_hook(
            data,
            "PostToolUse",
            "py -3 hook.py",
            matcher="Edit|Write",
            timeout_ms=1234,
        )
        entry = data["hooks"]["PostToolUse"][0]  # type: ignore[index]
        assert entry["matcher"] == "Edit|Write"
        assert entry["hooks"][0]["timeout"] == 1234


class TestRemoveHooks:
    def test_removes_only_tagged_entries(self) -> None:
        data: dict[str, object] = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "mneme",
                                "_mneme_tag": "mneme",
                            },
                            {
                                "type": "command",
                                "command": "foreign",
                            },
                        ]
                    }
                ]
            }
        }
        removed = remove_hooks(data, tag="mneme")
        assert removed == 1
        kept = data["hooks"]["Stop"][0]["hooks"]  # type: ignore[index]
        assert len(kept) == 1
        assert kept[0]["command"] == "foreign"

    def test_drops_handler_when_empty(self) -> None:
        data: dict[str, object] = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "x",
                                "_mneme_tag": "mneme",
                            }
                        ]
                    }
                ]
            }
        }
        remove_hooks(data, tag="mneme")
        assert data["hooks"]["Stop"] == []  # type: ignore[index]

    def test_no_hooks_root(self) -> None:
        data: dict[str, object] = {}
        assert remove_hooks(data) == 0


class TestMcpServer:
    def test_add_new_mcp_server(self) -> None:
        data: dict[str, object] = {}
        added = add_mcp_server(
            data, "mneme", command="mneme-mcp", args=[], env={"X": "Y"}
        )
        assert added is True
        assert data["mcpServers"]["mneme"]["command"] == "mneme-mcp"  # type: ignore[index]
        assert data["mcpServers"]["mneme"]["env"] == {"X": "Y"}  # type: ignore[index]

    def test_idempotent_when_already_present(self) -> None:
        data: dict[str, object] = {}
        add_mcp_server(data, "mneme", command="mneme-mcp", args=[])
        again = add_mcp_server(data, "mneme", command="mneme-mcp", args=[])
        assert again is False

    def test_replaces_when_shape_differs(self) -> None:
        data: dict[str, object] = {}
        add_mcp_server(data, "mneme", command="mneme-mcp", args=[])
        changed = add_mcp_server(
            data, "mneme", command="mneme-mcp", args=["--verbose"]
        )
        assert changed is True
        assert data["mcpServers"]["mneme"]["args"] == ["--verbose"]  # type: ignore[index]

    def test_remove_mcp_server_present(self) -> None:
        data: dict[str, object] = {"mcpServers": {"mneme": {"command": "x"}}}
        assert remove_mcp_server(data, "mneme") is True
        assert "mneme" not in data["mcpServers"]  # type: ignore[index]

    def test_remove_mcp_server_absent(self) -> None:
        assert remove_mcp_server({}, "mneme") is False

    def test_foreign_servers_untouched_by_remove(self) -> None:
        data: dict[str, object] = {
            "mcpServers": {"mneme": {"command": "x"}, "other": {"command": "y"}}
        }
        remove_mcp_server(data, "mneme")
        assert "other" in data["mcpServers"]  # type: ignore[index]
