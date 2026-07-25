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

    @pytest.mark.parametrize(
        ("member", "branch"),
        [
            ("../../outside", "vault-sync"),
            ("alice", "../unsafe"),
            ("alice", "--upload-pack"),
        ],
    )
    def test_unsafe_path_and_git_identifiers_are_rejected(
        self, tmp_path: Path, member: str, branch: str
    ) -> None:
        vault = _vault(tmp_path, "v")
        with pytest.raises(ValueError, match="sync member|sync branch"):
            write_sync_config(
                vault,
                SyncConfig(
                    remote_url="ssh://host/repo",
                    member=member,
                    branch=branch,
                ),
            )

    def test_manually_poisoned_member_config_fails_before_path_use(
        self, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path, "v")
        (vault.state_dir / sync_mod.SYNC_CONFIG_FILENAME).write_text(
            '{"remote_url":"test://remote","member":"../../outside"}\n',
            encoding="utf-8",
        )
        def no_op_runner(argv, cwd):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(argv, 0, "", "")

        result = push(vault, runner=no_op_runner)
        assert result.ok is False
        assert "invalid sync config" in result.detail
        assert not (tmp_path / "outside").exists()

    @pytest.mark.parametrize(
        "remote_url",
        [
            "https://user:token@example.test/repo.git",
            "https://token@example.test/repo.git",
            "https://example.test/repo.git?token=secret",
        ],
    )
    def test_remote_urls_cannot_embed_credentials(
        self, tmp_path: Path, remote_url: str
    ) -> None:
        vault = _vault(tmp_path, "v")
        with pytest.raises(ValueError, match="credentials|URL parameters"):
            write_sync_config(vault, SyncConfig(remote_url=remote_url, member="alice"))

    def test_git_error_never_echoes_the_remote_url(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, "v")
        remote = "https://example.test/private/repo.git"
        write_sync_config(vault, SyncConfig(remote_url=remote, member="alice"))
        (vault.root / "n.md").write_text("public", encoding="utf-8")

        def failing_runner(argv, cwd):  # type: ignore[no-untyped-def]
            if "push" in argv:
                return subprocess.CompletedProcess(argv, 1, "", f"failed for {remote}")
            return subprocess.CompletedProcess(argv, 0, "", "")

        result = push(vault, runner=failing_runner)

        assert result.ok is False
        assert remote not in result.detail
        assert "[REMOTE]" in result.detail


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

    def test_push_reredacts_tree_mutated_after_initial_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _vault(tmp_path, "v")
        write_sync_config(
            vault,
            SyncConfig(remote_url="test://remote", member="alice"),
        )
        (vault.root / "n.md").write_text("public", encoding="utf-8")
        real_build = sync_mod.build_share_tree

        def tainted_build(
            build_vault: VaultConfig,
            dest: Path,
            config: SyncConfig,
        ) -> sync_mod.ShareReport:
            report = real_build(build_vault, dest, config)
            staged = dest / "team" / config.member / "n.md"
            staged.write_text(
                "public <private>LATE_STAGE_SECRET</private>",
                encoding="utf-8",
            )
            return report

        observed_at_add: list[str] = []

        def sink_spy(argv, cwd):  # type: ignore[no-untyped-def]
            if list(argv)[:3] == ["git", "add", "--all"]:
                observed_at_add.append(
                    (cwd / "team" / "alice" / "n.md").read_text(
                        encoding="utf-8"
                    )
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(sync_mod, "build_share_tree", tainted_build)

        result = push(vault, runner=sink_spy)

        assert result.ok, result.detail
        assert observed_at_add == ["public [REDACTED]"]

    def test_pull_redacts_untrusted_markdown_conflict_sidecar(
        self, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path, "v")
        write_sync_config(
            vault,
            SyncConfig(remote_url="test://remote", member="bob"),
        )
        repo = vault.state_dir / sync_mod.SYNC_REPO_DIR_NAME
        (repo / ".git").mkdir(parents=True)
        incoming = repo / "team" / "alice" / "n.md"
        incoming.parent.mkdir(parents=True)
        incoming.write_text(
            "remote <private>CONFLICT_SECRET</private>",
            encoding="utf-8",
        )
        local = vault.root / "team" / "alice" / "n.md"
        local.parent.mkdir(parents=True)
        local.write_text("local edit", encoding="utf-8")

        def no_op_runner(argv, cwd):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(argv, 0, "", "")

        result = pull(vault, runner=no_op_runner)

        assert result.ok, result.detail
        sidecar = local.with_name("n.md.conflict")
        text = sidecar.read_text(encoding="utf-8")
        assert "CONFLICT_SECRET" not in text
        assert text == "remote [REDACTED]"

    @pytest.mark.skipif(
        sync_mod.os.name == "nt", reason="symlink creation needs elevation"
    )
    def test_pull_rejects_remote_symlink_without_reading_target(
        self, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path, "v")
        write_sync_config(
            vault,
            SyncConfig(remote_url="test://remote", member="bob"),
        )
        repo = vault.state_dir / sync_mod.SYNC_REPO_DIR_NAME
        (repo / ".git").mkdir(parents=True)
        outside = tmp_path / "outside.md"
        outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
        incoming = repo / "team" / "alice" / "n.md"
        incoming.parent.mkdir(parents=True)
        incoming.symlink_to(outside)

        def no_op_runner(argv, cwd):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(argv, 0, "", "")

        result = pull(vault, runner=no_op_runner)
        assert result.ok is False
        assert "remote sync file rejected" in result.detail
        assert not (vault.root / "team" / "alice" / "n.md").exists()

    @pytest.mark.skipif(
        sync_mod.os.name == "nt", reason="symlink creation needs elevation"
    )
    def test_conflict_sidecar_symlink_cannot_escape_vault(
        self, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path, "v")
        write_sync_config(
            vault,
            SyncConfig(remote_url="test://remote", member="bob"),
        )
        repo = vault.state_dir / sync_mod.SYNC_REPO_DIR_NAME
        (repo / ".git").mkdir(parents=True)
        incoming = repo / "team" / "alice" / "n.md"
        incoming.parent.mkdir(parents=True)
        incoming.write_text("remote", encoding="utf-8")
        local = vault.root / "team" / "alice" / "n.md"
        local.parent.mkdir(parents=True)
        local.write_text("local", encoding="utf-8")
        outside = tmp_path / "outside-conflict.md"
        outside.write_text("unchanged", encoding="utf-8")
        local.with_name("n.md.conflict").symlink_to(outside)

        def no_op_runner(argv, cwd):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(argv, 0, "", "")

        result = pull(vault, runner=no_op_runner)
        assert result.ok is False
        assert "conflict sidecar rejected" in result.detail
        assert outside.read_text(encoding="utf-8") == "unchanged"


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

    def test_encrypt_reredacts_plaintext_at_age_boundary(
        self, tmp_path: Path
    ) -> None:
        dest = tmp_path / "tree"
        dest.mkdir()
        plain = dest / "x.md"
        plain.write_text(
            "public <private>AGE_SINK_SECRET</private>",
            encoding="utf-8",
        )
        observed: list[str] = []

        def age_spy(argv, cwd):  # type: ignore[no-untyped-def]
            source = Path(argv[-1])
            observed.append(source.read_text(encoding="utf-8"))
            out = Path(argv[argv.index("-o") + 1])
            out.write_text("CIPHERTEXT", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        sync_mod._encrypt_tree(dest, ("age1abc",), age_spy)

        assert observed == ["public [REDACTED]"]


class TestFinalShareRedaction:
    def test_rejects_symlink_in_untrusted_share_tree(self, tmp_path: Path) -> None:
        root = tmp_path / "tree"
        root.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text(
            "outside <private>OUTSIDE_SECRET</private>",
            encoding="utf-8",
        )
        link = root / "link.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        with pytest.raises(OSError, match="escapes root|stable regular file"):
            sync_mod._redact_share_tree(root)

        assert "OUTSIDE_SECRET" in outside.read_text(encoding="utf-8")


class TestImportMarking:
    """3.1 trust-marking: imported notes are data, never instructions."""

    def _round_trip(self, tmp_path: Path) -> tuple[VaultConfig, VaultConfig, Path]:
        remote = _bare_remote(tmp_path)
        alice = _vault(tmp_path, "alice-vault")
        bob = _vault(tmp_path, "bob-vault")
        _configure(alice, remote, "alice")
        _configure(bob, remote, "bob")
        (alice.root / "fact.md").write_text("shared fact", encoding="utf-8")
        assert push(alice).ok
        assert pull(bob).ok
        return alice, bob, bob.root / "team" / "alice" / "fact.md"

    def test_imported_markdown_is_trust_marked(self, tmp_path: Path) -> None:
        from mneme_core.taint import TaintLabel, taint_for_trust

        _, _, imported = self._round_trip(tmp_path)
        text = imported.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "source: team-sync" in text
        assert 'team_member: "alice"' in text
        assert "trust: external" in text
        assert "payload_sha256: " in text
        assert "shared fact" in text
        # The recorded trust value resolves to UNTRUSTED in the taint model.
        assert taint_for_trust("external") is TaintLabel.UNTRUSTED

    def test_second_pull_idempotent(self, tmp_path: Path) -> None:
        _, bob, imported = self._round_trip(tmp_path)
        before = imported.read_bytes()
        again = pull(bob)
        assert again.ok
        assert again.imported == ()
        assert again.conflicts == ()
        assert imported.read_bytes() == before

    def test_local_edit_kept_when_remote_unchanged(self, tmp_path: Path) -> None:
        _, bob, imported = self._round_trip(tmp_path)
        edited = imported.read_text(encoding="utf-8") + "\nlocal margin note\n"
        imported.write_text(edited, encoding="utf-8", newline="")
        again = pull(bob)
        assert again.ok
        assert again.conflicts == ()
        assert "local margin note" in imported.read_text(encoding="utf-8")

    def test_remote_change_surfaces_conflict(self, tmp_path: Path) -> None:
        alice, bob, imported = self._round_trip(tmp_path)
        (alice.root / "fact.md").write_text("revised fact v2", encoding="utf-8")
        assert push(alice).ok
        again = pull(bob)
        assert again.ok
        assert again.conflicts == ("team/alice/fact.md",)
        assert "shared fact" in imported.read_text(encoding="utf-8")
        sidecar = imported.parent / "fact.md.conflict"
        assert "revised fact v2" in sidecar.read_text(encoding="utf-8")

    def test_incoming_trust_user_cannot_override_mark(self, tmp_path: Path) -> None:
        import yaml

        marked = sync_mod._mark_team_import(
            "---\ntrust: user\ntype: note\n---\nbody text",
            "mallory",
            "deadbeef",
        )
        block = marked.split("---")[1]
        data = yaml.safe_load(block)
        # YAML duplicate-key resolution keeps the LAST value: the mark wins.
        assert data["trust"] == "external"
        assert data["payload_sha256"] == "deadbeef"
        assert "body text" in marked

    def test_mark_without_frontmatter_prepends_block(self, tmp_path: Path) -> None:
        marked = sync_mod._mark_team_import("plain body", "alice", "abc123")
        assert marked.startswith("---\nsource: team-sync\n")
        assert marked.rstrip().endswith("plain body")
        assert sync_mod._imported_payload_hash(marked) == "abc123"
