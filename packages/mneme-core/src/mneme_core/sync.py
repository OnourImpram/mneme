"""Self-hosted team vault sync: redaction-before-share over plain git.

mneme-sync gives a team shared memory **without a vendor**: the shared
medium is any git remote the team already trusts (self-hosted Gitea,
a bare repo on a server, a private GitHub repo). No cloud service, no
account, no telemetry.

The privacy contract extends Core Invariant 3 from redaction-before-
*store* to **redaction-before-share**:

1. ``push`` never touches the operator's vault repo. It builds a
   separate SHARE TREE under ``<state>/sync-repo/`` by copying vault
   markdown through :func:`mneme_core.privacy.redact`, skipping the
   operator's ``exclude`` globs, and never copying ``.mneme/`` state.
2. After the copy, the share tree is re-scanned; if a ``<private>``
   span somehow survived, the push **aborts** (fail closed).
3. ``pull`` imports teammates' files under ``team/<member>/`` inside
   the local vault and never overwrites a local file: a same-path
   collision with different content is written as a ``.conflict``
   sidecar and surfaced in the report — deterministic merge, no silent
   overwrite, no LLM.

Optional end-to-end encryption hooks onto the external ``age`` binary
(config ``encrypt.recipients``); when configured, every staged file is
encrypted before commit so even the remote host never sees plaintext.
The git and age invocations run through an injectable *runner* so the
logic is fully testable without a network.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .privacy import redact
from .vault.config import VaultConfig

SYNC_CONFIG_FILENAME = "sync.json"
SYNC_REPO_DIR_NAME = "sync-repo"
TEAM_DIR_NAME = "team"

#: Subprocess runner signature: (argv, cwd) -> CompletedProcess.
Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]


def _default_runner(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is built from config, never shell
        list(argv),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        # An explicit null stdin keeps git non-interactive and avoids the
        # Windows invalid-handle error when the parent has no console.
        stdin=subprocess.DEVNULL,
    )


@dataclass(frozen=True)
class SyncConfig:
    """Resolved team-sync settings. Absent file means sync is unconfigured."""

    remote_url: str = ""
    branch: str = "vault-sync"
    member: str = "me"
    exclude: tuple[str, ...] = ()
    encrypt_recipients: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.remote_url)


def load_sync_config(vault: VaultConfig) -> SyncConfig:
    """Read ``sync.json`` from the state dir. Never raises."""
    path = vault.state_dir / SYNC_CONFIG_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return SyncConfig()
    if not isinstance(raw, dict):
        return SyncConfig()
    encrypt = raw.get("encrypt")
    recipients: tuple[str, ...] = ()
    if isinstance(encrypt, dict) and isinstance(encrypt.get("recipients"), list):
        recipients = tuple(str(r) for r in encrypt["recipients"])
    return SyncConfig(
        remote_url=str(raw.get("remote_url") or ""),
        branch=str(raw.get("branch") or "vault-sync"),
        member=str(raw.get("member") or "me"),
        exclude=tuple(str(g) for g in raw.get("exclude", []) if str(g)),
        encrypt_recipients=recipients,
    )


def write_sync_config(vault: VaultConfig, config: SyncConfig) -> Path:
    """Persist *config* as sync.json and return its path."""
    path = vault.state_dir / SYNC_CONFIG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "remote_url": config.remote_url,
        "branch": config.branch,
        "member": config.member,
        "exclude": list(config.exclude),
    }
    if config.encrypt_recipients:
        payload["encrypt"] = {"recipients": list(config.encrypt_recipients)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class ShareReport:
    """Outcome of building (and verifying) one share tree."""

    files_shared: int
    files_excluded: int
    redactions_applied: int
    leaked_paths: tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.leaked_paths


def _is_excluded(rel_posix: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pat) for pat in patterns)


def build_share_tree(
    vault: VaultConfig,
    dest: Path,
    config: SyncConfig,
) -> ShareReport:
    """Copy vault markdown into *dest* with redaction-before-share.

    Only ``*.md`` files are shared. ``.mneme/`` state, the local
    ``team/`` import area, and the operator's ``exclude`` globs are
    never copied. Every file body passes :func:`redact`; the tree is
    then re-scanned and any surviving ``<private>`` opener marks the
    report unsafe so the caller refuses to push (fail closed).
    """
    root = vault.root.resolve()
    state_prefix = vault.state_dir.resolve()
    shared = excluded = redactions = 0
    member_dir = dest / TEAM_DIR_NAME / config.member
    for md_path in sorted(root.rglob("*.md")):
        try:
            resolved = md_path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        if resolved.is_relative_to(state_prefix):
            continue
        rel = resolved.relative_to(root).as_posix()
        if rel.startswith(f"{TEAM_DIR_NAME}/"):
            continue  # never re-share teammates' imports
        if _is_excluded(rel, config.exclude):
            excluded += 1
            continue
        try:
            body = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        redacted = redact(body)
        if redacted != body:
            redactions += 1
        target = member_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redacted, encoding="utf-8", newline="")
        shared += 1

    leaked: list[str] = []
    for staged in sorted(member_dir.rglob("*.md")) if member_dir.is_dir() else []:
        text = staged.read_text(encoding="utf-8", errors="replace")
        if redact(text) != text:
            leaked.append(staged.relative_to(dest).as_posix())
    return ShareReport(
        files_shared=shared,
        files_excluded=excluded,
        redactions_applied=redactions,
        leaked_paths=tuple(leaked),
    )


def _encrypt_tree(
    dest: Path,
    recipients: tuple[str, ...],
    runner: Runner,
) -> int:
    """Encrypt every staged ``*.md`` to ``*.md.age`` via the external binary.

    The plaintext is removed after each successful encryption so the
    commit only ever contains ciphertext. Raises ``RuntimeError`` on
    the first failure (fail closed — no mixed-plaintext pushes).
    """
    count = 0
    args_recipients: list[str] = []
    for r in recipients:
        args_recipients.extend(["-r", r])
    for plain in sorted(dest.rglob("*.md")):
        out = plain.with_suffix(plain.suffix + ".age")
        proc = runner(["age", *args_recipients, "-o", str(out), str(plain)], dest)
        if proc.returncode != 0:
            raise RuntimeError(f"age encryption failed for {plain.name}: {proc.stderr.strip()}")
        plain.unlink()
        count += 1
    return count


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one push or pull."""

    ok: bool
    detail: str
    report: ShareReport | None = None
    imported: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def _git(runner: Runner, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return runner(["git", *args], repo)


def _ensure_sync_repo(vault: VaultConfig, config: SyncConfig, runner: Runner) -> Path:
    repo = vault.state_dir / SYNC_REPO_DIR_NAME
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        _git(runner, repo, "init", "--initial-branch", config.branch)
        _git(runner, repo, "remote", "add", "origin", config.remote_url)
    return repo


def push(
    vault: VaultConfig,
    *,
    runner: Runner = _default_runner,
) -> SyncResult:
    """Stage, verify, commit, and push the operator's share tree.

    The local vault repo is never touched; only the dedicated sync
    repo under the state dir is written. An unsafe share tree (any
    surviving private span) aborts before any git object is created.
    """
    config = load_sync_config(vault)
    if not config.configured:
        return SyncResult(False, "sync not configured: write .mneme/sync.json first")
    repo = _ensure_sync_repo(vault, config, runner)

    member_dir = repo / TEAM_DIR_NAME / config.member
    if member_dir.exists():
        shutil.rmtree(member_dir)
    report = build_share_tree(vault, repo, config)
    if not report.safe:
        if member_dir.exists():
            shutil.rmtree(member_dir)
        return SyncResult(
            False,
            f"aborted: private spans survived in {len(report.leaked_paths)} file(s)",
            report=report,
        )
    if config.encrypt_recipients:
        try:
            _encrypt_tree(member_dir, config.encrypt_recipients, runner)
        except (RuntimeError, OSError) as exc:
            shutil.rmtree(member_dir, ignore_errors=True)
            return SyncResult(False, str(exc), report=report)

    _git(runner, repo, "add", "--all", f"{TEAM_DIR_NAME}/{config.member}")
    commit = _git(
        runner,
        repo,
        "-c", "user.name=mneme-sync",
        "-c", f"user.email={config.member}@mneme-sync.local",
        "commit",
        "-m",
        f"sync: {config.member} share tree ({report.files_shared} files)",
    )
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        return SyncResult(False, f"commit failed: {commit.stderr.strip()}", report=report)
    pushed = _git(runner, repo, "push", "origin", f"HEAD:{config.branch}")
    if pushed.returncode != 0:
        return SyncResult(False, f"push failed: {pushed.stderr.strip()}", report=report)
    return SyncResult(True, f"pushed {report.files_shared} file(s)", report=report)


def pull(
    vault: VaultConfig,
    *,
    runner: Runner = _default_runner,
) -> SyncResult:
    """Fetch the team branch and import teammates' trees under ``team/``.

    Deterministic merge policy: a file is imported only when it is new
    or its local copy under ``team/`` is byte-identical; a differing
    local copy gets a ``<name>.conflict`` sidecar with the incoming
    content and the path is reported. Local files are never
    overwritten, the operator resolves conflicts in markdown.
    """
    config = load_sync_config(vault)
    if not config.configured:
        return SyncResult(False, "sync not configured: write .mneme/sync.json first")
    repo = _ensure_sync_repo(vault, config, runner)
    fetched = _git(runner, repo, "fetch", "origin", config.branch)
    if fetched.returncode != 0:
        return SyncResult(False, f"fetch failed: {fetched.stderr.strip()}")
    reset = _git(runner, repo, "checkout", "-B", config.branch, f"origin/{config.branch}")
    if reset.returncode != 0:
        return SyncResult(False, f"checkout failed: {reset.stderr.strip()}")

    imported: list[str] = []
    conflicts: list[str] = []
    team_root = repo / TEAM_DIR_NAME
    if team_root.is_dir():
        for src in sorted(team_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(repo)
            parts = rel.parts
            if len(parts) >= 2 and parts[1] == config.member:
                continue  # own share tree round-trips; vault stays canonical
            local = vault.root / rel
            if local.exists():
                if local.read_bytes() == src.read_bytes():
                    continue
                sidecar = local.with_name(local.name + ".conflict")
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, sidecar)
                conflicts.append(rel.as_posix())
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, local)
            imported.append(rel.as_posix())
    return SyncResult(
        True,
        f"imported {len(imported)} file(s), {len(conflicts)} conflict(s)",
        imported=tuple(imported),
        conflicts=tuple(conflicts),
    )
