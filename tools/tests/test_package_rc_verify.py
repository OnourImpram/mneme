"""Tests for the local-only Mneme release-candidate package verifier."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_ROOT.parent
sys.path.insert(0, str(TOOLS_ROOT))

import package_rc_verify  # noqa: E402


class VerificationReportTests(unittest.TestCase):
    def _report(self) -> package_rc_verify.VerificationReport:
        return package_rc_verify.VerificationReport(
            expected_version="3.6.0",
            repo_root="/repo",
            artifact_dir="/artifacts",
        )

    def test_optional_unavailable_does_not_block_pass(self) -> None:
        report = self._report()
        report.checks.extend(
            [
                package_rc_verify.CheckResult("required", "verified", "ok"),
                package_rc_verify.CheckResult(
                    "optional", "unavailable", "not installed", required=False
                ),
            ]
        )
        self.assertEqual(report.outcome, "pass")

    def test_required_unavailable_is_incomplete(self) -> None:
        report = self._report()
        report.checks.append(
            package_rc_verify.CheckResult("required", "unavailable", "missing")
        )
        self.assertEqual(report.outcome, "incomplete")

    def test_required_failure_wins_over_unavailable(self) -> None:
        report = self._report()
        report.checks.extend(
            [
                package_rc_verify.CheckResult("missing", "unavailable", "missing"),
                package_rc_verify.CheckResult("broken", "failed", "bad"),
            ]
        )
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(report.to_json()["schema"], "mneme-package-rc-verification/1")


class ArchiveSafetyTests(unittest.TestCase):
    def test_fixture_tree_fingerprint_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "nested").mkdir()
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "nested/a.txt").write_text("alpha", encoding="utf-8")

            fingerprints = package_rc_verify._fingerprint_tree(root)

            self.assertEqual(
                [entry[0] for entry in fingerprints],
                ["nested/a.txt", "z.txt"],
            )
            self.assertEqual(fingerprints, package_rc_verify._fingerprint_tree(root))

    def test_tar_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unsafe.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                payload = b"escape"
                member = tarfile.TarInfo("../escape.txt")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            errors = package_rc_verify.validate_tar_safety(path)
            self.assertTrue(any("unsafe path segment" in error for error in errors))

    def test_tar_links_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "link.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo("safe-link")
                member.type = tarfile.SYMTYPE
                member.linkname = "target"
                archive.addfile(member)
            errors = package_rc_verify.validate_tar_safety(path)
            self.assertTrue(any("links are not allowed" in error for error in errors))

    def test_claude_tarball_is_deterministic_complete_and_cache_free(self) -> None:
        plugin_root = REPO_ROOT / "packages/mneme-cc-plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.tar.gz"
            second = Path(temp_dir) / "second.tar.gz"
            package_rc_verify.build_claude_plugin_tarball(plugin_root, first)
            package_rc_verify.build_claude_plugin_tarball(plugin_root, second)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            self.assertEqual(package_rc_verify.validate_tar_safety(first), [])
            with tarfile.open(first, "r:gz") as archive:
                names = {member.name for member in archive.getmembers()}
            self.assertIn(".claude-plugin/plugin.json", names)
            self.assertIn(".mcp.json", names)
            self.assertIn("plugin.json", names)
            self.assertIn("src/mneme_cc_plugin/__init__.py", names)
            self.assertIn("hooks/hooks.json", names)
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))


class ArtifactMetadataTests(unittest.TestCase):
    def test_wheel_metadata_and_runtime_version_are_read_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "demo-3.6.0-py3-none-any.whl"
            metadata = (
                "Metadata-Version: 2.4\n"
                "Name: demo\n"
                "Version: 3.6.0\n"
                "License-Expression: Apache-2.0\n"
                "Requires-Python: >=3.11\n\n"
            )
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("demo/__init__.py", '__version__ = "3.6.0"\n')
                archive.writestr("demo-3.6.0.dist-info/METADATA", metadata)
                archive.writestr(
                    "demo-3.6.0.dist-info/entry_points.txt",
                    "[console_scripts]\ndemo = demo:main\n",
                )
            parsed, names, module_version = package_rc_verify._wheel_metadata(wheel)
            self.assertEqual(parsed.name, "demo")
            self.assertEqual(parsed.version, "3.6.0")
            self.assertEqual(module_version, "3.6.0")
            self.assertIn("demo/__init__.py", names)
            self.assertEqual(package_rc_verify._entry_points_from_wheel(wheel), {"demo"})

    def test_metadata_derived_runtime_version_is_deferred_to_clean_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "demo-3.6.0-py3-none-any.whl"
            metadata = (
                "Metadata-Version: 2.4\n"
                "Name: demo\n"
                "Version: 3.6.0\n"
                "License-Expression: Apache-2.0\n"
                "Requires-Python: >=3.11\n\n"
            )
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "demo/__init__.py",
                    "from importlib.metadata import version\n"
                    '__version__ = version("demo")\n',
                )
                archive.writestr("demo-3.6.0.dist-info/METADATA", metadata)

            parsed, _, module_version = package_rc_verify._wheel_metadata(wheel)

            self.assertEqual(parsed.version, "3.6.0")
            self.assertIsNone(module_version)

    def test_persisted_profile_uses_toml_value_not_comment_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text(
                'profile = "lite"\n# profile = "standard"\nschema_version = 1\n',
                encoding="utf-8",
            )
            self.assertEqual(package_rc_verify.read_persisted_profile(config), "lite")


class RegistryContractTests(unittest.TestCase):
    @staticmethod
    def _artifact_manifest() -> dict[str, object]:
        return json.loads(
            (REPO_ROOT / "packages/mneme-mcp/package.json").read_text(encoding="utf-8")
        )

    def test_live_registry_contract_matches_current_artifact_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_manifest = self._artifact_manifest()
            expected_version = artifact_manifest.get("version")
            self.assertIsInstance(expected_version, str)
            verifier = package_rc_verify.PackageRcVerifier(
                REPO_ROOT,
                expected_version,
                Path(temp_dir),
            )
            verifier._npm_tarball = Path(temp_dir) / "mneme-mcp-server.tgz"
            verifier._npm_package_manifest = artifact_manifest
            verifier._validate_registry_metadata()
            self.assertEqual(verifier.report.checks[-1].status, "verified")
            registry_artifact = Path(temp_dir) / "registry/server.json"
            self.assertTrue(registry_artifact.is_file())
            self.assertEqual(
                registry_artifact.read_bytes(),
                (REPO_ROOT / "server.json").read_bytes(),
            )
            self.assertEqual(verifier.report.artifacts[-1].kind, "mcp-registry-metadata")

    def test_registry_version_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            payload = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
            payload["version"] = "9.9.9"
            (repo / "server.json").write_text(json.dumps(payload), encoding="utf-8")
            verifier = package_rc_verify.PackageRcVerifier(
                repo,
                "3.5.0",
                Path(temp_dir) / "artifacts",
            )
            verifier._npm_tarball = Path(temp_dir) / "mneme-mcp-server.tgz"
            verifier._npm_package_manifest = self._artifact_manifest()
            verifier._validate_registry_metadata()
            self.assertEqual(verifier.report.checks[-1].status, "failed")
            self.assertIn("top-level version", verifier.report.checks[-1].detail)

    def test_registry_identity_uses_validated_tarball_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            verifier = package_rc_verify.PackageRcVerifier(
                REPO_ROOT,
                "3.5.0",
                Path(temp_dir),
            )
            verifier._npm_tarball = Path(temp_dir) / "mneme-mcp-server.tgz"
            verifier._npm_package_manifest = {
                **self._artifact_manifest(),
                "mcpName": "io.example.wrong",
            }

            verifier._validate_registry_metadata()

            self.assertEqual(verifier.report.checks[-1].status, "failed")
            self.assertIn("npm artifact MCP identity", verifier.report.checks[-1].detail)

    def test_verifier_contains_no_publish_or_release_command(self) -> None:
        source = (TOOLS_ROOT / "package_rc_verify.py").read_text(encoding="utf-8").lower()
        for forbidden in (
            "npm publish",
            "twine upload",
            "git tag",
            "gh release",
            "mcp-publisher publish",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
