"""Direct tests for repository and client plugin validators."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_ROOT.parent
sys.path.insert(0, str(TOOLS_ROOT))

import repo_integrity  # noqa: E402
import validate_antigravity_plugin  # noqa: E402
import validate_claude_plugin  # noqa: E402
import validate_codex_plugin  # noqa: E402
import version_bump  # noqa: E402


def _rewrite_json(path: Path, mutator: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert callable(mutator)
    mutator(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class RepositoryTruthTests(unittest.TestCase):
    def test_live_repository_integrity_passes(self) -> None:
        self.assertEqual(repo_integrity.collect_errors(), [])

    def test_tool_registry_exposes_canonical_tools(self) -> None:
        registry = (REPO_ROOT / "packages/mneme-mcp/src/tool_registry.ts").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            repo_integrity._tool_names_from_registry(registry),
            repo_integrity.EXPECTED_TOOL_NAMES,
        )

    def test_version_registry_contains_exactly_eighteen_sources(self) -> None:
        self.assertEqual(len(repo_integrity.EXPECTED_VERSION_SOURCE_LABELS), 18)
        self.assertEqual(
            tuple(source.label for source in version_bump.SOURCES),
            repo_integrity.EXPECTED_VERSION_SOURCE_LABELS,
        )

    def test_immutable_registry_name_has_only_allowlisted_locations(self) -> None:
        self.assertEqual(
            repo_integrity._immutable_name_locations(),
            repo_integrity.IMMUTABLE_MCP_NAME_LOCATIONS,
        )


class PluginValidatorTests(unittest.TestCase):
    def test_live_plugin_bundles_pass(self) -> None:
        self.assertEqual(
            validate_claude_plugin.validate_plugin(
                REPO_ROOT / "packages/mneme-cc-plugin",
                REPO_ROOT,
            ),
            [],
        )
        self.assertEqual(
            validate_codex_plugin.validate_plugin(
                REPO_ROOT / "packages/mneme-codex-plugin"
            ),
            [],
        )
        self.assertEqual(
            validate_antigravity_plugin.validate_plugin(
                REPO_ROOT / "packages/mneme-antigravity-plugin"
            ),
            [],
        )

    def test_claude_validator_rejects_license_and_node_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plugin_root = temp_root / "packages/mneme-cc-plugin"
            shutil.copytree(REPO_ROOT / "packages/mneme-cc-plugin", plugin_root)
            shutil.copytree(REPO_ROOT / ".claude-plugin", temp_root / ".claude-plugin")
            _rewrite_json(
                plugin_root / ".claude-plugin/plugin.json",
                lambda payload: payload.__setitem__("license", "MIT"),
            )

            def lower_node(payload: dict[str, object]) -> None:
                engines = payload["engines"]
                assert isinstance(engines, dict)
                engines["node"] = ">=20"

            _rewrite_json(plugin_root / "plugin.json", lower_node)
            errors = validate_claude_plugin.validate_plugin(plugin_root, temp_root)
            self.assertTrue(any("license must be Apache-2.0" in error for error in errors))
            self.assertTrue(any("engines must equal" in error for error in errors))

    def test_codex_validator_rejects_license_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "mneme-codex-plugin"
            shutil.copytree(REPO_ROOT / "packages/mneme-codex-plugin", plugin_root)
            _rewrite_json(
                plugin_root / ".codex-plugin/plugin.json",
                lambda payload: payload.__setitem__("license", "MIT"),
            )
            errors = validate_codex_plugin.validate_plugin(plugin_root)
            self.assertIn("license must be Apache-2.0", errors)

    def test_antigravity_validator_rejects_hook_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "mneme-antigravity-plugin"
            shutil.copytree(REPO_ROOT / "packages/mneme-antigravity-plugin", plugin_root)

            def remove_stop(payload: dict[str, object]) -> None:
                hooks = payload["hooks"]
                assert isinstance(hooks, dict)
                hooks.pop("Stop")

            _rewrite_json(plugin_root / "hooks/hooks.json", remove_stop)
            errors = validate_antigravity_plugin.validate_plugin(plugin_root)
            self.assertTrue(any("must declare exactly" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
