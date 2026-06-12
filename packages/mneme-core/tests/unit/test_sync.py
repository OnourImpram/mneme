"""Team sync: redaction-before-share, fail-closed pushes, conflict surfacing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mneme_core import sync as sync_mod
from mneme_core.sync import (
    SyncConfig,
    build_share_tree,
    load_sync_config,
    pull,
    push,
    write_sync_config,
)
from mneme_core.vault.config import VaultConfig


def _vault(tmp_path: Path, name: str) -> VaultConfig:
    root = tmp_path / name
    (root / ".mneme").mkdir(parents=True)
    return VaultConfig.from_path(root)


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch", "vault-sync", str(remote)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return remote


def _configure(vault: VaultConfig, remote: Path, member: str, **kw: object) -> None:
    write_sync_config(
        vault,
        SyncConfig(
            remote_url=str(remote),
            member=member,
            exclude=tuple(kw.get("exclude", ())),  # type: ignore[arg-type]
        ),
    )


class TestConfig:
    def test_absent_config_unconfigured(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        assert load_sync_config(vault).configured is False

    def test_round_trip(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        write_sync_config(
            vault,
            SyncConfig(remote_url="ssh://host/repo", member="alice", exclude=("drafts/*",)),
        )
        cfg = load_sync_config(vault)
        assert cfg.remote_url == "ssh://host/repo"
        assert cfg.member == "alice"
        assert cfg.exclude == ("drafts/*",)


class TestShareTree:
    def test_redaction_before_share(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        (vault.root / "notes").mkdir()
        (vault.root / "notes" / "n.md").write_text(
            "fact <private>secret-token</private> tail", encoding="utf-8"
        )
        dest = tmp_path / "dest"
        report = build_share_tree(vault, dest, SyncConfig(remote_url="x", member="alice"))
        assert report.files_shared == 1
        assert report.redactions_applied == 1
        assert report.safe is True
        staged = (dest / "team" / "alice" / "notes" / "n.md").read_text(encoding="utf-8")
        assert "secret-token" not in staged
        assert "[REDACTED]" in staged

    def test_state_dir_never_shared(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        (vault.state_dir / "internal.md").write_text("state", encoding="utf-8")
        (vault.root / "ok.md").write_text("ok", encoding="utf-8")
        dest = tmp_path / "dest"
        build_share_tree(vault, dest, SyncConfig(remote_url="x", member="alice"))
        assert not (dest / "team" / "alice" / ".mneme").exists()
        assert (dest / "team" / "alice" / "ok.md").is_file()

    def test_exclude_globs(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        (vault.root / "drafts").mkdir()
        (vault.root / "drafts" / "wip.md").write_text("wip", encoding="utf-8")
        (vault.root / "done.md").write_text("done", encoding="utf-8")
        dest = tmp_path / "dest"
        report = build_share_tree(
            vault, dest, SyncConfig(remote_url="x", member="a", exclude=("drafts/*",))
        )
        assert report.files_excluded == 1
        assert not (dest / "team" / "a" / "drafts").exists()

    def test_team_imports_not_reshared(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        (vault.root / "team" / "bob").mkdir(parents=True)
        (vault.root / "team" / "bob" / "x.md").write_text("bob's", encoding="utf-8")
        dest = tmp_path / "dest"
        report = build_share_tree(vault, dest, SyncConfig(remote_url="x", member="a"))
        assert report.files_shared == 0


class TestPushPull:
    def test_round_trip_between_two_vaults(self, tmp_path: Path) -> None:
        remote = _bare_remote(tmp_path)
        alice = _vault(tmp_path, "alice-vault")
        bob = _vault(tmp_path, "bob-vault")
        _configure(alice, remote, "alice")
        _configure(bob, remote, "bob")
        (alice.root / "notes").mkdir()
        (alice.root / "notes" / "fact.md").write_text(
            "shared fact <private>never</private>", encoding="utf-8"
        )

        pushed = push(alice)
        assert pushed.ok, pushed.detail

        pulled = pull(bob)
        assert pulled.ok, pulled.detail
        imported = bob.root / "team" / "alice" / "notes" / "fact.md"
        assert imported.is_file()
        text = imported.read_text(encoding="utf-8")
        assert "never" not in text
        assert "[REDACTED]" in text

    def test_pull_conflict_surfaced_not_overwritten(self, tmp_path: Path) -> None:
        remote = _bare_remote(tmp_path)
        alice = _vault(tmp_path, "alice-vault")
        bob = _vault(tmp_path, "bob-vault")
        _configure(alice, remote, "alice")
        _configure(bob, remote, "bob")
        (alice.root / "n.md").write_text("alice v2", encoding="utf-8")
        assert push(alice).ok

        local = bob.root / "team" / "alice" / "n.md"
        local.parent.mkdir(parents=True)
        local.write_text("bob's local edit", encoding="utf-8")

        pulled = pull(bob)
        assert pulled.ok
        assert pulled.conflicts == ("team/alice/n.md",)
        assert local.read_text(encoding="utf-8") == "bob's local edit"
        assert (local.parent / "n.md.conflict").read_text(encoding="utf-8") == "alice v2"

    def test_unconfigured_push_refused(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        result = push(vault)
        assert result.ok is False
        assert "not configured" in result.detail

    def test_leak_aborts_push(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If redaction were ever broken, the re-scan must stop the push."""
        remote = _bare_remote(tmp_path)
        vault = _vault(tmp_path, "v")
        _configure(vault, remote, "alice")
        (vault.root / "n.md").write_text(
            "x <private>leak</private> y", encoding="utf-8"
        )
        calls = {"n": 0}
        real = sync_mod.redact

        def broken_then_real(text: str | None) -> str:
            calls["n"] += 1
            # First pass (copy) silently broken; verification pass real.
            return str(text or "") if calls["n"] <= 1 else real(text)

        monkeypatch.setattr(sync_mod, "redact", broken_then_real)
        result = push(vault)
        assert result.ok is False
        assert "private spans survived" in result.detail
        # Nothing was committed or pushed.
        ls = subprocess.run(
            ["git", "ls-remote", str(remote)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        assert "vault-sync" not in ls.stdout


class TestEncryption:
    def test_encrypt_tree_runs_age_per_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "tree"
        (dest / "a").mkdir(parents=True)
        (dest / "a" / "x.md").write_text("plain", encoding="utf-8")
        seen: list[list[str]] = []

        def fake_runner(argv, cwd):  # type: ignore[no-untyped-def]
            seen.append(list(argv))
            out = Path(argv[argv.index("-o") + 1])
            out.write_text("CIPHERTEXT", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        count = sync_mod._encrypt_tree(dest, ("age1abc",), fake_runner)
        assert count == 1
        assert seen[0][:3] == ["age", "-r", "age1abc"]
        assert not (dest / "a" / "x.md").exists()
        assert (dest / "a" / "x.md.age").read_text(encoding="utf-8") == "CIPHERTEXT"

    def test_encrypt_failure_raises(self, tmp_path: Path) -> None:
        dest = tmp_path / "tree"
        dest.mkdir()
        (dest / "x.md").write_text("plain", encoding="utf-8")

        def failing_runner(argv, cwd):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(argv, 1, "", "no recipient")

        with pytest.raises(RuntimeError, match="age encryption failed"):
            sync_mod._encrypt_tree(dest, ("age1abc",), failing_runner)
