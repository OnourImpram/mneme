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
4. Every imported markdown file is **trust-marked**: its frontmatter
   gains ``source: team-sync``, ``team_member``, ``trust: external``
   (which :func:`mneme_core.taint.taint_for_trust` maps to
   ``UNTRUSTED`` — teammate notes are data, never instructions), and
   ``payload_sha256`` of the incoming bytes. Re-pulls compare that
   recorded hash, so an unchanged remote payload is idempotent even
   though the local file carries the mark, and local edits never
   trigger conflict noise. The body also passes :func:`redact` on
   import — defence in depth on the receiving side. The mark keys are
   appended at the END of an existing frontmatter block: YAML keeps
   the last duplicate key, so an incoming ``trust: user`` cannot
   override the mark.

Optional end-to-end encryption hooks onto the external ``age`` binary
(config ``encrypt.recipients``); when configured, every staged file is
encrypted before commit so even the remote host never sees plaintext.
The git and age invocations run through an injectable *runner* so the
logic is fully testable without a network.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .privacy import redact
from .vault.atomic_write import atomic_write_bytes, atomic_write_text
from .vault.config import VaultConfig

SYNC_CONFIG_FILENAME = "sync.json"
SYNC_REPO_DIR_NAME = "sync-repo"
TEAM_DIR_NAME = "team"
_MAX_SYNC_FILE_BYTES = 64 * 1024 * 1024
_MEMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

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


def _validate_sync_config(config: SyncConfig) -> None:
    if _MEMBER_RE.fullmatch(config.member) is None or config.member in {".", ".."}:
        raise ValueError("sync member must be a safe path identifier")
    branch = config.branch
    if (
        _BRANCH_RE.fullmatch(branch) is None
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
        or "@{" in branch
    ):
        raise ValueError("sync branch is not a safe git reference")
    if "\x00" in config.remote_url or "\n" in config.remote_url:
        raise ValueError("sync remote URL contains forbidden control characters")
    parsed = urlsplit(config.remote_url)
    if parsed.scheme in {"http", "https"} and parsed.username is not None:
        raise ValueError("sync remote URL must not contain embedded credentials")
    if parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("sync remote URL must not contain credentials or URL parameters")


def _safe_git_error(stderr: str, remote_url: str) -> str:
    """Return bounded git stderr without echoing configured remote material."""
    safe = redact(stderr)
    if remote_url:
        safe = safe.replace(remote_url, "[REMOTE]")
    safe = re.sub(
        r"https?://[^\s/@:]+:[^\s/@]+@",
        "https://[CREDENTIAL-REDACTED]@",
        safe,
        flags=re.IGNORECASE,
    )
    return safe[:2048]


def _read_stable_regular(path: Path, root: Path) -> bytes:
    """Read one bounded regular file without following an escaping link."""
    root_resolved = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise OSError(f"sync path escapes trusted root: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        path_stat = path.lstat()
        file_stat = os.fstat(fd)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            path.is_symlink()
            or bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)
            or not stat.S_ISREG(file_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (file_stat.st_dev, file_stat.st_ino)
        ):
            raise OSError(f"sync path is not a stable regular file: {path}")
        if file_stat.st_size > _MAX_SYNC_FILE_BYTES:
            raise OSError(f"sync file exceeds the configured size limit: {path}")
        chunks: list[bytes] = []
        remaining = _MAX_SYNC_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_SYNC_FILE_BYTES:
            raise OSError(f"sync file exceeds the configured size limit: {path}")
        return payload
    finally:
        os.close(fd)


def load_sync_config(vault: VaultConfig) -> SyncConfig:
    """Read ``sync.json`` from the state dir. Never raises."""
    path = vault.state_dir / SYNC_CONFIG_FILENAME
    try:
        raw = json.loads(_read_stable_regular(path, vault.root).decode("utf-8"))
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
    _validate_sync_config(config)
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
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        vault_root=vault.root,
    )
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
    _validate_sync_config(config)
    root = vault.root.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve(strict=True)
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
            body = _read_stable_regular(md_path, root).decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        redacted = redact(body)
        if redacted != body:
            redactions += 1
        target = member_dir / rel
        atomic_write_text(target, redacted, vault_root=dest_root)
        shared += 1

    leaked: list[str] = []
    for staged in sorted(member_dir.rglob("*.md")) if member_dir.is_dir() else []:
        text = _read_stable_regular(staged, dest_root).decode(
            "utf-8", errors="replace"
        )
        if redact(text) != text:
            leaked.append(staged.relative_to(dest).as_posix())
    return ShareReport(
        files_shared=shared,
        files_excluded=excluded,
        redactions_applied=redactions,
        leaked_paths=tuple(leaked),
    )


def _redact_share_file(path: Path, root: Path) -> bool:
    """Re-redact one staged markdown file immediately before its sink."""
    root_resolved = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise OSError(f"staged share path escapes root: {path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    open_fd: int | None = fd
    try:
        path_stat = path.lstat()
        file_stat = os.fstat(fd)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            path.is_symlink()
            or bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)
            or not stat.S_ISREG(file_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (file_stat.st_dev, file_stat.st_ino)
        ):
            raise OSError(f"staged share path is not a stable regular file: {path}")
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fp:
            open_fd = None
            text = fp.read()
    finally:
        if open_fd is not None:
            os.close(open_fd)

    redacted = redact(text)
    # Replacing from a same-directory exclusive temp file prevents this write
    # from following a symlink swapped in after the descriptor-bound read.
    atomic_write_text(path, redacted, vault_root=root_resolved)
    return redacted != text


def _redact_share_tree(root: Path) -> int:
    """Treat the staged share tree as untrusted and sanitize it in place."""
    count = 0
    for path in sorted(root.rglob("*.md")) if root.is_dir() else []:
        count += int(_redact_share_file(path, root))
    return count


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
        # The share tree can be modified after its initial build and scan.
        # Sanitize each plaintext file at the age process boundary.
        _redact_share_file(plain, dest)
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


#: Frontmatter trust value for team imports; taint_for_trust maps it
#: to UNTRUSTED so imported notes can never become instructions.
TEAM_IMPORT_TRUST = "external"


def _payload_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mark_team_import(text: str, member: str, payload_hash: str) -> str:
    """Inject team-sync provenance keys into *text*'s frontmatter.

    The keys go at the END of an existing block because YAML resolves
    duplicate keys to the last occurrence — placed first, an incoming
    ``trust: user`` line would silently win over the mark. Files
    without a frontmatter block get a fresh one prepended.
    """
    inject = [
        "source: team-sync",
        f"team_member: {json.dumps(member)}",
        f"trust: {TEAM_IMPORT_TRUST}",
        f"payload_sha256: {payload_hash}",
    ]
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[:idx] + inject + lines[idx:])
        # Unterminated block: treat as body and prepend a fresh one.
    return "\n".join(["---", *inject, "---", "", text])


def _imported_payload_hash(text: str) -> str | None:
    """Read the recorded ``payload_sha256`` from a marked import."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    value: str | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("payload_sha256:"):
            # Keep scanning: the mark appends at the end of the block,
            # so the LAST occurrence is authoritative (YAML semantics).
            value = stripped.split(":", 1)[1].strip().strip('"') or None
    return value


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
    try:
        _validate_sync_config(config)
    except ValueError as exc:
        return SyncResult(False, f"invalid sync config: {exc}")
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
    else:
        try:
            # Reapply redaction after build/verification and immediately before
            # git snapshots the share tree. The staging directory is untrusted.
            _redact_share_tree(member_dir)
        except OSError as exc:
            shutil.rmtree(member_dir, ignore_errors=True)
            return SyncResult(False, f"final share redaction failed: {exc}", report=report)

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
        return SyncResult(
            False,
            f"commit failed: {_safe_git_error(commit.stderr.strip(), config.remote_url)}",
            report=report,
        )
    pushed = _git(runner, repo, "push", "origin", f"HEAD:{config.branch}")
    if pushed.returncode != 0:
        return SyncResult(
            False,
            f"push failed: {_safe_git_error(pushed.stderr.strip(), config.remote_url)}",
            report=report,
        )
    return SyncResult(True, f"pushed {report.files_shared} file(s)", report=report)


def pull(
    vault: VaultConfig,
    *,
    runner: Runner = _default_runner,
) -> SyncResult:
    """Fetch the team branch and import teammates' trees under ``team/``.

    Deterministic merge policy: a file is imported only when it is new
    or the remote payload is unchanged (recorded ``payload_sha256`` for
    marked markdown imports, byte identity otherwise); a changed remote
    payload gets a ``<name>.conflict`` sidecar with the incoming
    content and the path is reported. Local files are never
    overwritten, the operator resolves conflicts in markdown. Imported
    markdown is redacted on arrival and trust-marked (``source:
    team-sync``, ``trust: external``, ``payload_sha256``) so retrieval
    treats it as data, never instructions, and re-pulls stay idempotent
    even after local edits.
    """
    config = load_sync_config(vault)
    if not config.configured:
        return SyncResult(False, "sync not configured: write .mneme/sync.json first")
    try:
        _validate_sync_config(config)
    except ValueError as exc:
        return SyncResult(False, f"invalid sync config: {exc}")
    repo = _ensure_sync_repo(vault, config, runner)
    fetched = _git(runner, repo, "fetch", "origin", config.branch)
    if fetched.returncode != 0:
        return SyncResult(
            False,
            f"fetch failed: {_safe_git_error(fetched.stderr.strip(), config.remote_url)}",
        )
    reset = _git(runner, repo, "checkout", "-B", config.branch, f"origin/{config.branch}")
    if reset.returncode != 0:
        return SyncResult(
            False,
            f"checkout failed: {_safe_git_error(reset.stderr.strip(), config.remote_url)}",
        )

    imported: list[str] = []
    conflicts: list[str] = []
    team_root = repo / TEAM_DIR_NAME
    if team_root.is_dir():
        team_root_resolved = team_root.resolve(strict=True)
        if not team_root_resolved.is_relative_to(repo.resolve(strict=True)):
            return SyncResult(False, "remote team tree escapes the sync repository")
        for src in sorted(team_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(repo)
            parts = rel.parts
            if len(parts) < 3 or _MEMBER_RE.fullmatch(parts[1]) is None:
                return SyncResult(False, "remote sync tree contains an invalid member path")
            if len(parts) >= 2 and parts[1] == config.member:
                continue  # own share tree round-trips; vault stays canonical
            local = vault.root / rel
            try:
                incoming = _read_stable_regular(src, team_root_resolved)
            except OSError as exc:
                return SyncResult(False, f"remote sync file rejected: {exc}")
            is_markdown = src.suffix.lower() == ".md"
            if local.exists():
                try:
                    local_bytes = _read_stable_regular(local, vault.root)
                except OSError as exc:
                    return SyncResult(False, f"local sync target rejected: {exc}")
                if is_markdown:
                    recorded = _imported_payload_hash(
                        local_bytes.decode("utf-8", errors="replace")
                    )
                    if recorded == _payload_sha256(incoming):
                        continue  # remote payload unchanged; local edits stay
                if local_bytes == incoming:
                    continue  # legacy unmarked import, still identical
                sidecar = local.with_name(local.name + ".conflict")
                try:
                    if is_markdown:
                        # A remote conflict is still an untrusted persistence sink.
                        # Never copy markdown bytes around the canonical redactor.
                        atomic_write_text(
                            sidecar,
                            redact(incoming.decode("utf-8", errors="replace")),
                            vault_root=vault.root,
                        )
                    else:
                        atomic_write_bytes(sidecar, incoming, vault_root=vault.root)
                except OSError as exc:
                    return SyncResult(False, f"conflict sidecar rejected: {exc}")
                conflicts.append(rel.as_posix())
                continue
            try:
                if is_markdown:
                    member_name = parts[1] if len(parts) >= 2 else "unknown"
                    body = redact(incoming.decode("utf-8", errors="replace"))
                    marked = _mark_team_import(
                        body, member_name, _payload_sha256(incoming)
                    )
                    atomic_write_text(local, marked, vault_root=vault.root)
                else:
                    atomic_write_bytes(local, incoming, vault_root=vault.root)
            except OSError as exc:
                return SyncResult(False, f"local sync write rejected: {exc}")
            imported.append(rel.as_posix())
    return SyncResult(
        True,
        f"imported {len(imported)} file(s), {len(conflicts)} conflict(s)",
        imported=tuple(imported),
        conflicts=tuple(conflicts),
    )
