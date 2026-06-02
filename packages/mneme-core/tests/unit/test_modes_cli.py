"""Tests for mneme_core.modes_cli: the mneme-modes CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mneme_core.modes_cli import cli


class TestListCommand:
    def test_exits_zero_and_contains_code(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "code" in result.output

    def test_output_is_sorted(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        names = [line for line in result.output.splitlines() if line.strip()]
        assert names == sorted(names)

    def test_contains_all_builtins(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        output = result.output
        for name in ("code", "research", "clinical-research", "security-review"):
            assert name in output

    def test_with_config_includes_user_mode(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "myusermode": {
                "language": "tr",
                "allowed_types": ["topic", "reference"],
                "write": {"default_type": "topic"},
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "myusermode" in result.output
        assert "code" in result.output

    def test_config_with_invalid_mode_exits_nonzero(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "badmode": {
                "language": "en",
                "allowed_types": ["not_a_real_type"],
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--config", str(cfg)])
        assert result.exit_code != 0


class TestShowCommand:
    def test_show_code_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "code"])
        assert result.exit_code == 0

    def test_show_code_contains_allowed_types(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "code"])
        assert result.exit_code == 0
        # allowed_types for "code" must appear in output
        assert "allowed_types" in result.output
        assert "topic" in result.output

    def test_show_code_contains_name_and_language(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "code"])
        assert result.exit_code == 0
        assert "name:" in result.output
        assert "language:" in result.output

    def test_show_unknown_exits_one(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "nope"])
        assert result.exit_code == 1

    def test_show_unknown_writes_error_message(self) -> None:
        # CliRunner merges stderr into output by default; check the combined output.
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "nope"])
        assert result.exit_code == 1
        assert "nope" in result.output

    def test_show_clinical_research_shows_privacy_blocks(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "clinical-research"])
        assert result.exit_code == 0
        assert "True" in result.output  # block flags are True

    def test_show_user_mode_via_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "custommode": {
                "language": "en",
                "allowed_types": ["topic"],
                "write": {"default_type": "topic"},
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "custommode", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "custommode" in result.output


class TestValidateCommand:
    def test_builtins_validate_ok(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate"])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_validate_with_valid_user_config_exits_zero(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "validmode": {
                "language": "en",
                "allowed_types": ["topic", "reference"],
                "write": {"default_type": "topic"},
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_validate_with_bad_json_exits_one(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text("{not valid json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--config", str(cfg)])
        assert result.exit_code == 1
        # Should print the error, not a traceback
        assert "Traceback" not in result.output

    def test_validate_with_unknown_type_exits_one_with_problem_text(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "badmode": {
                "language": "en",
                "allowed_types": ["not_a_real_type"],
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--config", str(cfg)])
        assert result.exit_code == 1
        # Problem text must be printed (not a bare traceback)
        assert "Traceback" not in result.output
        assert "not_a_real_type" in result.output or "unknown" in result.output.lower()

    def test_validate_with_bad_default_type_exits_one(self, tmp_path: Path) -> None:
        # default_type not in allowed_types — loads OK but validate_modes finds the problem
        # We need a mode that passes mode_from_dict but fails validate_modes.
        # Actually mode_from_dict itself raises ValueError for this case, so the
        # validate command catches the ValueError and exits 1.
        cfg = tmp_path / "modes.json"
        payload = {
            "weirdmode": {
                "language": "en",
                "allowed_types": ["topic"],
                "write": {"default_type": "claim"},  # claim not in allowed_types
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "Traceback" not in result.output
