"""The leak gate has to be able to fail, and it has to fail only on leaks.

WHY THIS EXISTS
``sanitize.sh`` guards the one place a leak becomes permanent: a publish. It
had no test, and no workflow called it either -- so "we have a leak gate" was a
configuration fact, not a running one. A gate nobody has watched fail is
indistinguishable from a gate that cannot.

Each test builds a throwaway git repository, plants exactly one thing, and runs
the real script against it. The pairing is the point: every "this is caught"
has a matching "this is not", because a gate that flags everything passes the
first kind of test and is useless.

The planted values are assembled at runtime rather than written as literals.
A scan can match its own pattern: spelled out, this file trips the very gate it
tests -- and the fix for that must not be to stop scanning the file, which
would make the one place full of credential-shaped strings the one place
nobody looks.

Written as ``unittest`` on purpose: CI discovers ``tools/tests`` with
``python -m unittest discover``, and a pytest-only file would be imported,
collected as nothing, and reported green -- the exact failure this file exists
to rule out elsewhere.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "sanitize.sh"
PUBLIC_PATTERNS = REPO_ROOT / ".github" / "sanitization-patterns.public.txt"
ALLOW_FILE = REPO_ROOT / ".github" / "sanitization-allow.txt"
BASH = shutil.which("bash")


@unittest.skipIf(BASH is None, "bash is not available on this runner")
class SanitizeGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def sandbox(self, files: dict[str, str]) -> Path:
        """A real git repo carrying the real gate and whatever we plant."""
        root = self.tmp / "repo"
        (root / ".github" / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, root / ".github" / "scripts" / "sanitize.sh")
        shutil.copy(PUBLIC_PATTERNS, root / ".github" / "sanitization-patterns.public.txt")
        shutil.copy(ALLOW_FILE, root / ".github" / "sanitization-allow.txt")
        for rel, body in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        return root

    def run_gate(self, root: Path) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, ".github/scripts/sanitize.sh"],
            cwd=root,
            capture_output=True,
            text=True,
        )

    def test_a_clean_tree_passes(self) -> None:
        """Without this, every "it caught the leak" below could be satisfied by
        a gate that fails on anything at all."""
        root = self.sandbox({"packages/a/mod.py": "VALUE = 'nothing private here'\n"})
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASSED", result.stdout)

    def test_it_runs_without_the_maintainers_private_list(self) -> None:
        """The condition CI is always in.

        The script used to require the gitignored private list and exit 1 when
        it was missing, which is why it could never be wired into a workflow.
        """
        root = self.sandbox({"README.md": "public docs\n"})
        self.assertFalse((root / ".github" / "sanitization-patterns.txt").exists())
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private list: no", result.stdout)

    def test_an_aws_key_is_caught(self) -> None:
        root = self.sandbox({"packages/a/conf.py": 'KEY = "AKIA' + "Z" * 16 + '"\n'})
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("packages/a/conf.py", result.stdout)

    def test_a_users_home_path_is_caught(self) -> None:
        """A local path names the person who ran the build."""
        root = self.sandbox({"docs/NOTES.md": "see C:/Users/" + "somebody/x.md\n"})
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("docs/NOTES.md", result.stdout)

    def test_a_consumer_mailbox_is_caught(self) -> None:
        root = self.sandbox({"pyproject.toml": 'authors = ["x <someone@' + 'gmail.com>"]\n'})
        self.assertEqual(self.run_gate(root).returncode, 1)

    def test_a_project_domain_address_is_not_caught(self) -> None:
        """The mail rule must separate a personal inbox from a project contact."""
        root = self.sandbox({"pyproject.toml": 'authors = ["x <hello@example.org>"]\n'})
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_reviewed_placeholder_is_not_caught(self) -> None:
        """Security fixtures must be able to carry credential-shaped values."""
        root = self.sandbox(
            {"packages/a/tests/test_redaction.py": 'AWS = "AKIAIOSFODNN7EXAMPLE"\n'}
        )
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_an_untracked_file_is_not_scanned(self) -> None:
        """The scope claim: a publish ships tracked content, so that is what
        the gate judges. Flagging an ignored local note is an alarm with no
        action behind it, and those are the ones that get bypassed."""
        root = self.sandbox({"README.md": "public docs\n"})
        (root / "private-notes.md").write_text(
            "key AKIA" + "Q" * 16 + "\n", encoding="utf-8"
        )
        result = self.run_gate(root)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
