"""Build and verify Mneme release-candidate artifacts without publishing.

The verifier intentionally uses repository sources only for the build step. Every
install smoke targets the artifacts produced during the current run, and all
mutable client, vault, migration, and package-manager state lives below temporary
directories. It never creates a tag, release, or registry publication.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import textwrap
import tomllib
import venv
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast

import validate_claude_plugin

Status = Literal["verified", "failed", "unavailable"]
Outcome = Literal["pass", "failed", "incomplete"]

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MODULE_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class PythonDistribution:
    name: str
    source: str
    module: str
    entry_points: tuple[str, ...]


PYTHON_DISTRIBUTIONS = (
    PythonDistribution(
        "mneme-core",
        "packages/mneme-core",
        "mneme_core",
        ("mneme-core", "mneme-audit", "mneme-modes", "mneme-console"),
    ),
    PythonDistribution(
        "mneme-cc-plugin",
        "packages/mneme-cc-plugin",
        "mneme_cc_plugin",
        ("mneme",),
    ),
    PythonDistribution(
        "mneme-graph",
        "packages/mneme-graph",
        "mneme_graph",
        ("mneme-graph",),
    ),
    PythonDistribution(
        "mneme-code",
        "packages/mneme-code",
        "mneme_code",
        ("mneme-code",),
    ),
)

CLAUDE_PLUGIN_INPUTS = (
    ".claude-plugin",
    ".mcp.json",
    "plugin.json",
    "pyproject.toml",
    "src",
    "hooks",
    "commands",
    "skills",
    "README.md",
)
FORBIDDEN_ARCHIVE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 600,
    ) -> CommandResult: ...


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 600,
) -> CommandResult:
    """Run a bounded non-interactive command and capture its output."""

    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            env=merged_env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(124, stdout, f"{stderr}\ncommand timed out after {timeout}s")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    required: bool = True


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    path: str
    size: int
    sha256: str


@dataclass
class VerificationReport:
    expected_version: str
    repo_root: str
    artifact_dir: str
    checks: list[CheckResult] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    @property
    def outcome(self) -> Outcome:
        if any(check.required and check.status == "failed" for check in self.checks):
            return "failed"
        if any(check.required and check.status == "unavailable" for check in self.checks):
            return "incomplete"
        return "pass"

    def to_json(self) -> dict[str, object]:
        return {
            "schema": "mneme-package-rc-verification/1",
            "expected_version": self.expected_version,
            "repo_root": self.repo_root,
            "artifact_dir": self.artifact_dir,
            "generated_at": self.generated_at,
            "outcome": self.outcome,
            "checks": [asdict(check) for check in self.checks],
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
        }


@dataclass(frozen=True)
class DistributionMetadata:
    name: str
    version: str
    license: str
    requires_python: str
    requires_dist: tuple[str, ...]


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_tree(root: Path) -> tuple[tuple[str, int, str], ...]:
    """Return a deterministic regular-file inventory for a temporary fixture tree."""

    if not root.exists():
        return ()
    entries: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"fixture tree contains a symbolic link: {path}")
        if path.is_file():
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    path.stat().st_size,
                    _sha256(path),
                )
            )
    return tuple(entries)


def _brief(result: CommandResult, limit: int = 800) -> str:
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if not text:
        return f"exit {result.returncode}"
    return text[-limit:]


def _metadata_from_message(message: Message) -> DistributionMetadata:
    return DistributionMetadata(
        name=message.get("Name", ""),
        version=message.get("Version", ""),
        license=message.get("License-Expression", message.get("License", "")),
        requires_python=message.get("Requires-Python", ""),
        requires_dist=tuple(message.get_all("Requires-Dist", [])),
    )


def _safe_archive_name(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or WINDOWS_DRIVE_RE.match(normalized):
        return "absolute or empty archive member"
    if any(part in {"", ".", ".."} for part in path.parts):
        return "archive member contains an unsafe path segment"
    return None


def validate_tar_safety(path: Path) -> list[str]:
    """Return archive path and link violations without extracting it."""

    errors: list[str] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                problem = _safe_archive_name(member.name)
                if problem is not None:
                    errors.append(f"{member.name}: {problem}")
                if member.issym() or member.islnk():
                    errors.append(f"{member.name}: links are not allowed")
    except (OSError, tarfile.TarError) as exc:
        errors.append(f"cannot read tar archive: {exc}")
    return errors


def _validate_zip_safety(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                problem = _safe_archive_name(member.filename)
                if problem is not None:
                    errors.append(f"{member.filename}: {problem}")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    errors.append(f"{member.filename}: links are not allowed")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"cannot read wheel archive: {exc}")
    return errors


def _wheel_metadata(path: Path) -> tuple[DistributionMetadata, set[str], str | None]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError(f"wheel must contain one METADATA file, found {len(metadata_names)}")
        message = BytesParser(policy=default).parsebytes(archive.read(metadata_names[0]))
        init_names = [name for name in names if name.count("/") == 1 and name.endswith("/__init__.py")]
        module_version: str | None = None
        for init_name in init_names:
            match = MODULE_VERSION_RE.search(archive.read(init_name).decode("utf-8", errors="replace"))
            if match is not None:
                module_version = match.group(1)
                break
        return _metadata_from_message(message), names, module_version


def _sdist_metadata(path: Path) -> tuple[DistributionMetadata, set[str]]:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        metadata_members = [
            member for member in members if member.isfile() and member.name.endswith("/PKG-INFO")
        ]
        if len(metadata_members) != 1:
            raise ValueError(f"sdist must contain one PKG-INFO file, found {len(metadata_members)}")
        handle = archive.extractfile(metadata_members[0])
        if handle is None:
            raise ValueError("sdist PKG-INFO is unreadable")
        message = BytesParser(policy=default).parsebytes(handle.read())
        return _metadata_from_message(message), names


def _contains_build_debris(names: set[str]) -> bool:
    return any(
        any(part in FORBIDDEN_ARCHIVE_PARTS for part in PurePosixPath(name).parts)
        or name.endswith((".pyc", ".pyo"))
        for name in names
    )


def _entry_points_from_wheel(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        candidates = [name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt")]
        if not candidates:
            return set()
        text = archive.read(candidates[0]).decode("utf-8")
    in_console_scripts = False
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_console_scripts = line == "[console_scripts]"
        elif in_console_scripts and "=" in line:
            names.add(line.split("=", 1)[0].strip())
    return names


def _prepare_empty_dir(path: Path) -> str | None:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        return f"artifact output directory is not empty: {path}"
    return None


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_script(root: Path, name: str) -> Path:
    if os.name == "nt":
        executable = root / "Scripts" / f"{name}.exe"
        if executable.exists():
            return executable
        return root / "Scripts" / f"{name}.cmd"
    return root / "bin" / name


def _offline_pip_env() -> dict[str, str]:
    return {
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _offline_node_env() -> dict[str, str]:
    return {
        "CI": "true",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "npm_config_offline": "true",
    }


def _build_interpreter_dependency_path() -> str:
    """Return the selected process environment's third-party package path."""

    return sysconfig.get_path("purelib")


def _safe_extract_tar(path: Path, destination: Path) -> None:
    violations = validate_tar_safety(path)
    if violations:
        raise ValueError("; ".join(violations))
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            target = (destination / PurePosixPath(member.name)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"archive member escapes destination: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unreadable archive member: {member.name}")
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o644
    return info


def build_claude_plugin_tarball(plugin_root: Path, output: Path) -> None:
    """Create a deterministic, cache-free Claude plugin source artifact."""

    for relative in CLAUDE_PLUGIN_INPUTS:
        if not (plugin_root / relative).exists():
            raise FileNotFoundError(f"Claude plugin input is missing: {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative in CLAUDE_PLUGIN_INPUTS:
                    source = plugin_root / relative
                    candidates = [source]
                    if source.is_dir():
                        candidates.extend(sorted(source.rglob("*")))
                    for candidate in candidates:
                        rel_path = candidate.relative_to(plugin_root)
                        if any(part in FORBIDDEN_ARCHIVE_PARTS for part in rel_path.parts):
                            continue
                        if candidate.suffix in {".pyc", ".pyo"}:
                            continue
                        if candidate.is_symlink():
                            raise ValueError(f"Claude plugin input may not be a symlink: {rel_path}")
                        archive.add(
                            candidate,
                            arcname=rel_path.as_posix(),
                            recursive=False,
                            filter=_tar_filter,
                        )


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], payload)


def read_persisted_profile(path: Path) -> str | None:
    """Read the actual vault profile, rejecting absent or non-string values."""

    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    value = payload.get("profile")
    return value if isinstance(value, str) else None


class PackageRcVerifier:
    def __init__(
        self,
        repo_root: Path,
        expected_version: str,
        artifact_dir: Path,
        *,
        python: Path | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.expected_version = expected_version
        self.artifact_dir = artifact_dir.resolve()
        self.python = (python or Path(sys.executable)).resolve()
        self.runner = runner
        self.report = VerificationReport(
            expected_version=expected_version,
            repo_root=str(self.repo_root),
            artifact_dir=str(self.artifact_dir),
        )
        self._wheels: dict[str, Path] = {}
        self._sdists: dict[str, Path] = {}
        self._npm_tarball: Path | None = None
        self._npm_package_manifest: dict[str, Any] | None = None
        self._claude_tarball: Path | None = None

    def _check(
        self,
        name: str,
        status: Status,
        detail: str,
        *,
        required: bool = True,
    ) -> None:
        self.report.checks.append(CheckResult(name, status, detail, required))

    def _artifact(self, kind: str, path: Path) -> None:
        self.report.artifacts.append(
            ArtifactRecord(kind, str(path.resolve()), path.stat().st_size, _sha256(path))
        )

    def run(self) -> VerificationReport:
        if SEMVER_RE.fullmatch(self.expected_version) is None:
            self._check(
                "expected_version",
                "failed",
                "expected version must be strict MAJOR.MINOR.PATCH semver",
            )
            return self.report
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._build_and_validate_python()
        self._verify_python_install_uninstall()
        self._build_and_validate_npm()
        self._build_and_validate_claude_plugin()
        self._verify_plugin_install_and_client_lifecycle()
        self._validate_registry_metadata()
        self._verify_npm_install_and_migration()
        self._record_mcp_publisher_availability()
        self._write_checksums()
        return self.report

    def _build_and_validate_python(self) -> None:
        output = self.artifact_dir / "python"
        problem = _prepare_empty_dir(output)
        if problem is not None:
            self._check("python_artifact_build", "failed", problem)
            return
        version_check = self.runner(
            [str(self.python), "-m", "build", "--version"], cwd=self.repo_root, timeout=30
        )
        if version_check.returncode != 0:
            self._check(
                "python_artifact_build",
                "unavailable",
                "the selected interpreter lacks the local 'build' frontend: "
                + _brief(version_check),
            )
            return
        failures: list[str] = []
        for spec in PYTHON_DISTRIBUTIONS:
            result = self.runner(
                [
                    str(self.python),
                    "-m",
                    "build",
                    "--no-isolation",
                    "--wheel",
                    "--sdist",
                    "--outdir",
                    str(output),
                    str(self.repo_root / spec.source),
                ],
                cwd=self.repo_root,
                env={"PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
            )
            if result.returncode != 0:
                failures.append(f"{spec.name}: {_brief(result)}")
        if failures:
            self._check("python_artifact_build", "failed", " | ".join(failures))
            return
        self._check("python_artifact_build", "verified", "built four wheels and four sdists")
        self._validate_python_artifacts(output)

    def _validate_python_artifacts(self, output: Path) -> None:
        wheels = sorted(output.glob("*.whl"))
        sdists = sorted(output.glob("*.tar.gz"))
        discovered_wheels: dict[str, list[tuple[Path, DistributionMetadata]]] = {}
        discovered_sdists: dict[str, list[tuple[Path, DistributionMetadata]]] = {}
        parse_errors: list[str] = []
        for wheel in wheels:
            try:
                metadata, _, _ = _wheel_metadata(wheel)
                discovered_wheels.setdefault(_normalize_distribution_name(metadata.name), []).append(
                    (wheel, metadata)
                )
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                parse_errors.append(f"{wheel.name}: {exc}")
        for sdist in sdists:
            try:
                metadata, _ = _sdist_metadata(sdist)
                discovered_sdists.setdefault(_normalize_distribution_name(metadata.name), []).append(
                    (sdist, metadata)
                )
            except (OSError, ValueError, tarfile.TarError) as exc:
                parse_errors.append(f"{sdist.name}: {exc}")
        if parse_errors:
            self._check("python_artifact_inventory", "failed", " | ".join(parse_errors))
        else:
            self._check(
                "python_artifact_inventory",
                "verified",
                f"discovered {len(wheels)} wheels and {len(sdists)} sdists",
            )

        for spec in PYTHON_DISTRIBUTIONS:
            normalized = _normalize_distribution_name(spec.name)
            wheel_matches = discovered_wheels.get(normalized, [])
            sdist_matches = discovered_sdists.get(normalized, [])
            errors: list[str] = []
            if len(wheel_matches) != 1:
                errors.append(f"expected one wheel, found {len(wheel_matches)}")
            if len(sdist_matches) != 1:
                errors.append(f"expected one sdist, found {len(sdist_matches)}")
            if errors:
                self._check(f"python_artifact:{spec.name}", "failed", "; ".join(errors))
                continue
            wheel, wheel_meta = wheel_matches[0]
            sdist, sdist_meta = sdist_matches[0]
            try:
                _, wheel_names, module_version = _wheel_metadata(wheel)
                _, sdist_names = _sdist_metadata(sdist)
            except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
                self._check(f"python_artifact:{spec.name}", "failed", str(exc))
                continue
            safety_errors = _validate_zip_safety(wheel) + validate_tar_safety(sdist)
            errors.extend(safety_errors)
            if wheel_meta.version != self.expected_version:
                errors.append(
                    f"wheel version {wheel_meta.version!r} does not match {self.expected_version!r}"
                )
            if sdist_meta.version != self.expected_version:
                errors.append(
                    f"sdist version {sdist_meta.version!r} does not match {self.expected_version!r}"
                )
            if wheel_meta != sdist_meta:
                errors.append("wheel and sdist core metadata differ")
            if wheel_meta.license != "Apache-2.0":
                errors.append(f"license is {wheel_meta.license!r}, expected Apache-2.0")
            if ">=3.11" not in wheel_meta.requires_python.replace(" ", ""):
                errors.append(f"Requires-Python does not preserve >=3.11: {wheel_meta.requires_python!r}")
            expected_init = f"{spec.module}/__init__.py"
            if expected_init not in wheel_names:
                errors.append(f"wheel is missing {expected_init}")
            if module_version is not None and module_version != self.expected_version:
                errors.append(
                    f"public {spec.module}.__version__ is {module_version!r}, "
                    f"expected {self.expected_version!r}"
                )
            if _contains_build_debris(wheel_names | sdist_names):
                errors.append("artifact contains cache or bytecode build debris")
            actual_entry_points = _entry_points_from_wheel(wheel)
            missing_entry_points = set(spec.entry_points) - actual_entry_points
            if missing_entry_points:
                errors.append(f"missing console scripts: {sorted(missing_entry_points)}")
            if spec.name == "mneme-graph":
                requirements = {requirement.replace(" ", "").lower() for requirement in wheel_meta.requires_dist}
                if not any(
                    requirement.startswith("tree-sitter<0.26,>=0.25")
                    or requirement.startswith("tree-sitter>=0.25,<0.26")
                    for requirement in requirements
                ):
                    errors.append("tree-sitter>=0.25,<0.26 is not preserved in wheel metadata")
            self._artifact("python-wheel", wheel)
            self._artifact("python-sdist", sdist)
            install_usable = (
                not safety_errors
                and wheel_meta.version == self.expected_version
                and sdist_meta.version == self.expected_version
                and expected_init in wheel_names
                and not _contains_build_debris(wheel_names | sdist_names)
            )
            if install_usable:
                self._wheels[spec.name] = wheel
                self._sdists[spec.name] = sdist
            if errors:
                self._check(f"python_artifact:{spec.name}", "failed", "; ".join(errors))
                continue
            self._check(
                f"python_artifact:{spec.name}",
                "verified",
                "metadata, runtime version, entry points, archive safety, wheel, and sdist agree",
            )

    def _verify_python_install_uninstall(self) -> None:
        if len(self._wheels) != len(PYTHON_DISTRIBUTIONS):
            self._check(
                "python_clean_install_uninstall",
                "unavailable",
                "all four validated wheels are required",
            )
            return
        with tempfile.TemporaryDirectory(prefix="mneme-rc-python-") as temp_dir:
            root = Path(temp_dir)
            env_root = root / "venv"
            try:
                venv.EnvBuilder(with_pip=True, clear=True).create(env_root)
            except (OSError, subprocess.SubprocessError) as exc:
                self._check("python_clean_install_uninstall", "failed", f"venv creation: {exc}")
                return
            python = _venv_python(env_root)
            wheel_paths = [str(self._wheels[spec.name]) for spec in PYTHON_DISTRIBUTIONS]
            install = self.runner(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    *wheel_paths,
                ],
                cwd=root,
                env=_offline_pip_env(),
            )
            if install.returncode != 0:
                self._check("python_clean_install_uninstall", "failed", _brief(install))
                return
            import_script = textwrap.dedent(
                f"""
                import importlib
                import importlib.metadata
                import json
                import pathlib
                import sys

                expected = {self.expected_version!r}
                pairs = {[(spec.name, spec.module) for spec in PYTHON_DISTRIBUTIONS]!r}
                observed = {{}}
                prefix = pathlib.Path(sys.prefix).resolve()
                for dist_name, module_name in pairs:
                    version = importlib.metadata.version(dist_name)
                    if version != expected:
                        raise SystemExit(f"{{dist_name}} metadata version {{version}} != {{expected}}")
                    module = importlib.import_module(module_name)
                    module_path = pathlib.Path(module.__file__).resolve()
                    if prefix not in module_path.parents:
                        raise SystemExit(f"{{module_name}} loaded outside the clean venv: {{module_path}}")
                    public_version = getattr(module, "__version__", None)
                    if public_version != expected:
                        raise SystemExit(
                            f"{{module_name}}.__version__ {{public_version!r}} != {{expected}}"
                        )
                    observed[dist_name] = {{
                        "module_path": str(module_path),
                        "public_version": public_version,
                    }}
                print(json.dumps(observed, sort_keys=True))
                """
            )
            imported = self.runner([str(python), "-c", import_script], cwd=root)
            if imported.returncode != 0:
                self._check("python_clean_install_uninstall", "failed", _brief(imported))
                return
            uninstall = self.runner(
                [
                    str(python),
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    *[spec.name for spec in PYTHON_DISTRIBUTIONS],
                ],
                cwd=root,
                env=_offline_pip_env(),
            )
            if uninstall.returncode != 0:
                self._check("python_clean_install_uninstall", "failed", _brief(uninstall))
                return
            absence_script = textwrap.dedent(
                f"""
                import importlib.metadata
                import importlib.util

                pairs = {[(spec.name, spec.module) for spec in PYTHON_DISTRIBUTIONS]!r}
                for dist_name, module_name in pairs:
                    try:
                        importlib.metadata.distribution(dist_name)
                    except importlib.metadata.PackageNotFoundError:
                        pass
                    else:
                        raise SystemExit(f"distribution remains installed: {{dist_name}}")
                    if importlib.util.find_spec(module_name) is not None:
                        raise SystemExit(f"module remains importable: {{module_name}}")
                """
            )
            absent = self.runner([str(python), "-c", absence_script], cwd=root)
            if absent.returncode != 0:
                self._check("python_clean_install_uninstall", "failed", _brief(absent))
                return
            self._check(
                "python_clean_install_uninstall",
                "verified",
                "four local wheels installed with --no-index --no-deps, imported from the fresh venv, then fully uninstalled",
            )
            self._check(
                "python_third_party_dependency_resolution",
                "unavailable",
                "offline artifact verification deliberately does not resolve third-party dependencies",
                required=False,
            )

    def _build_and_validate_npm(self) -> None:
        output = self.artifact_dir / "npm"
        problem = _prepare_empty_dir(output)
        if problem is not None:
            self._check("npm_artifact_build", "failed", problem)
            return
        pnpm = shutil.which("pnpm")
        npm = shutil.which("npm")
        if pnpm is None or npm is None:
            self._check(
                "npm_artifact_build",
                "unavailable",
                "pnpm and npm are both required to build and pack the local npm artifact",
            )
            return
        build = self.runner(
            [pnpm, "--filter", "mneme-mcp-server", "build"], cwd=self.repo_root
        )
        if build.returncode != 0:
            self._check("npm_artifact_build", "failed", _brief(build))
            return
        pack = self.runner(
            [npm, "pack", "--json", "--pack-destination", str(output)],
            cwd=self.repo_root / "packages/mneme-mcp",
            env=_offline_node_env(),
        )
        if pack.returncode != 0:
            self._check("npm_artifact_build", "failed", _brief(pack))
            return
        tarballs = list(output.glob("*.tgz"))
        if len(tarballs) != 1:
            self._check(
                "npm_artifact_build", "failed", f"expected one npm tarball, found {len(tarballs)}"
            )
            return
        tarball = tarballs[0]
        errors = validate_tar_safety(tarball)
        try:
            with tarfile.open(tarball, "r:gz") as archive:
                names = {member.name for member in archive.getmembers()}
                package_handle = archive.extractfile("package/package.json")
                if package_handle is None:
                    raise ValueError("package/package.json is missing")
                package_payload = json.loads(package_handle.read())
                if not isinstance(package_payload, dict):
                    raise ValueError("package/package.json must be an object")
        except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
            errors.append(str(exc))
            names = set()
            package_payload = {}
        required = {
            "package/package.json",
            "package/dist/index.js",
            "package/dist/cli/index.js",
            "package/README.md",
            "package/LICENSE",
        }
        missing = required - names
        if missing:
            errors.append(f"missing npm members: {sorted(missing)}")
        if any(name.startswith(("package/src/", "package/tests/", "package/node_modules/")) for name in names):
            errors.append("npm tarball contains source, tests, or node_modules")
        if package_payload.get("name") != "mneme-mcp-server":
            errors.append("npm package name is not mneme-mcp-server")
        if package_payload.get("version") != self.expected_version:
            errors.append(
                f"npm package version {package_payload.get('version')!r} does not match {self.expected_version!r}"
            )
        engines = package_payload.get("engines")
        if not isinstance(engines, dict) or engines.get("node") != ">=22":
            errors.append("npm artifact must require Node >=22")
        if package_payload.get("license") != "Apache-2.0":
            errors.append("npm artifact license must be Apache-2.0")
        if errors:
            self._check("npm_artifact", "failed", "; ".join(errors))
            return
        self._npm_tarball = tarball
        self._npm_package_manifest = package_payload
        self._artifact("npm-tarball", tarball)
        self._check(
            "npm_artifact_build",
            "verified",
            "built the local npm tarball from TypeScript output",
        )
        self._check(
            "npm_artifact",
            "verified",
            "name, version, Node engine, license, binaries, and archive boundaries verified",
        )

    def _build_and_validate_claude_plugin(self) -> None:
        output = self.artifact_dir / "claude"
        problem = _prepare_empty_dir(output)
        if problem is not None:
            self._check("claude_plugin_tarball", "failed", problem)
            return
        tarball = output / f"mneme-cc-plugin-{self.expected_version}.tar.gz"
        plugin_root = self.repo_root / "packages/mneme-cc-plugin"
        try:
            build_claude_plugin_tarball(plugin_root, tarball)
        except (OSError, ValueError) as exc:
            self._check("claude_plugin_tarball", "failed", str(exc))
            return
        errors = validate_tar_safety(tarball)
        with tempfile.TemporaryDirectory(prefix="mneme-rc-claude-") as temp_dir:
            temp_root = Path(temp_dir)
            extracted = temp_root / "packages/mneme-cc-plugin"
            try:
                _safe_extract_tar(tarball, extracted)
                marketplace_root = temp_root / ".claude-plugin"
                marketplace_root.mkdir(parents=True)
                shutil.copy2(
                    self.repo_root / ".claude-plugin/marketplace.json",
                    marketplace_root / "marketplace.json",
                )
                errors.extend(validate_claude_plugin.validate_plugin(extracted, temp_root))
                native = _load_json_object(extracted / ".claude-plugin/plugin.json")
                legacy = _load_json_object(extracted / "plugin.json")
                if native.get("version") != self.expected_version:
                    errors.append("native Claude manifest version does not match target")
                if legacy.get("version") != self.expected_version:
                    errors.append("legacy Claude manifest version does not match target")
                if not (extracted / "src/mneme_cc_plugin/__init__.py").is_file():
                    errors.append("Claude tarball is missing Python package source")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        if errors:
            self._check("claude_plugin_tarball", "failed", "; ".join(errors))
            return
        self._claude_tarball = tarball
        self._artifact("claude-plugin-tarball", tarball)
        self._check(
            "claude_plugin_tarball",
            "verified",
            "deterministic tarball includes native and legacy manifests, MCP config, Python source, hooks, commands, and skills",
        )

    def _verify_plugin_install_and_client_lifecycle(self) -> None:
        core_wheel = self._wheels.get("mneme-core")
        plugin_tarball = self._claude_tarball
        if core_wheel is None or plugin_tarball is None:
            for name in (
                "claude_plugin_clean_install",
                "client_install_uninstall",
                "profile_switching",
            ):
                self._check(name, "unavailable", "validated core wheel and Claude tarball are required")
            return
        with tempfile.TemporaryDirectory(prefix="mneme-rc-plugin-install-") as temp_dir:
            root = Path(temp_dir)
            env_root = root / "venv"
            try:
                venv.EnvBuilder(with_pip=True, clear=True).create(env_root)
            except (OSError, subprocess.SubprocessError) as exc:
                for name in (
                    "claude_plugin_clean_install",
                    "client_install_uninstall",
                    "profile_switching",
                ):
                    self._check(name, "failed", f"venv creation: {exc}")
                return
            python = _venv_python(env_root)
            install = self.runner(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    str(core_wheel),
                    str(plugin_tarball),
                ],
                cwd=root,
                env={
                    **_offline_pip_env(),
                    "PYTHONPATH": _build_interpreter_dependency_path(),
                },
            )
            if install.returncode != 0:
                detail = _brief(install)
                status: Status = "unavailable" if "hatchling" in detail.lower() else "failed"
                self._check("claude_plugin_clean_install", status, detail)
                self._check("client_install_uninstall", "unavailable", "plugin install did not complete")
                self._check("profile_switching", "unavailable", "plugin install did not complete")
                return
            hook_script = textwrap.dedent(
                """
                import importlib
                import pathlib
                import sys

                modules = [
                    "mneme_cc_plugin.hooks.post_tool_use",
                    "mneme_cc_plugin.hooks.session_start",
                    "mneme_cc_plugin.hooks.stop",
                    "mneme_cc_plugin.hooks.pre_compact",
                    "mneme_cc_plugin.hooks.session_end",
                ]
                prefix = pathlib.Path(sys.prefix).resolve()
                package = importlib.import_module("mneme_cc_plugin")
                package_path = pathlib.Path(package.__file__).resolve()
                if prefix not in package_path.parents:
                    raise SystemExit(f"plugin loaded outside artifact venv: {package_path}")
                for name in modules:
                    importlib.import_module(name)
                """
            )
            dependency_env = {"PYTHONPATH": _build_interpreter_dependency_path()}
            hooks = self.runner(
                [str(python), "-c", hook_script], cwd=root, env=dependency_env
            )
            mneme = _venv_script(env_root, "mneme")
            version = self.runner(
                [str(mneme), "--version"], cwd=root, env=dependency_env
            )
            if hooks.returncode != 0 or version.returncode != 0:
                self._check(
                    "claude_plugin_clean_install",
                    "failed",
                    _brief(hooks if hooks.returncode != 0 else version),
                )
            else:
                self._check(
                    "claude_plugin_clean_install",
                    "verified",
                    "local Claude tarball installed without an index, all hook modules imported, and mneme --version ran; only third-party dependencies came from the selected build interpreter",
                )

            home = root / "home"
            home.mkdir()
            vault = root / "vault"
            mcp_config = root / "mcp.json"
            mcp_config.write_text(
                json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}) + "\n",
                encoding="utf-8",
            )
            lifecycle_env = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "PYTHONPATH": _build_interpreter_dependency_path(),
            }
            common = [
                str(mneme),
                "install",
                "--client",
                "mcp",
                "--config",
                str(mcp_config),
                "--vault",
                str(vault),
                "--skip-python",
                "--skip-node",
            ]
            first = self.runner([*common, "--profile", "lite"], cwd=root, env=lifecycle_env)
            second = self.runner(
                [*common, "--upgrade-profile", "standard"], cwd=root, env=lifecycle_env
            )
            lifecycle_errors: list[str] = []
            profile_errors: list[str] = []
            if first.returncode != 0:
                lifecycle_errors.append(f"initial install: {_brief(first)}")
            if second.returncode != 0:
                profile_errors.append(f"profile switch command: {_brief(second)}")
            try:
                config_payload = _load_json_object(mcp_config)
                servers = config_payload.get("mcpServers")
                if not isinstance(servers, dict):
                    lifecycle_errors.append("MCP config lost mcpServers object")
                else:
                    if "mneme" not in servers:
                        lifecycle_errors.append("MCP config lacks mneme after install")
                    if "other" not in servers:
                        lifecycle_errors.append("MCP install clobbered unrelated server")
                persisted_profile = read_persisted_profile(vault / ".mneme/config.toml")
                if persisted_profile != "standard":
                    profile_errors.append(
                        "--upgrade-profile standard did not persist standard in .mneme/config.toml "
                        f"(observed {persisted_profile!r})"
                    )
            except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                lifecycle_errors.append(str(exc))

            uninstall = self.runner(
                [str(mneme), "uninstall", "--client", "mcp", "--config", str(mcp_config)],
                cwd=root,
                env=lifecycle_env,
            )
            uninstall_again = self.runner(
                [str(mneme), "uninstall", "--client", "mcp", "--config", str(mcp_config)],
                cwd=root,
                env=lifecycle_env,
            )
            if uninstall.returncode != 0 or uninstall_again.returncode != 0:
                lifecycle_errors.append(
                    "uninstall round trip failed: "
                    + _brief(uninstall if uninstall.returncode != 0 else uninstall_again)
                )
            try:
                post_payload = _load_json_object(mcp_config)
                post_servers = post_payload.get("mcpServers")
                if not isinstance(post_servers, dict):
                    lifecycle_errors.append("uninstall produced invalid mcpServers")
                else:
                    if "mneme" in post_servers:
                        lifecycle_errors.append("uninstall left mneme registered")
                    if "other" not in post_servers:
                        lifecycle_errors.append("uninstall removed unrelated server")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                lifecycle_errors.append(str(exc))

            if lifecycle_errors:
                self._check("client_install_uninstall", "failed", "; ".join(lifecycle_errors))
            else:
                self._check(
                    "client_install_uninstall",
                    "verified",
                    "artifact CLI installed and idempotently uninstalled the MCP client stanza while preserving unrelated config",
                )
            if profile_errors:
                self._check("profile_switching", "failed", "; ".join(profile_errors))
            else:
                self._check(
                    "profile_switching",
                    "verified",
                    "lite to standard profile switch persisted in the isolated vault",
                )

            uninstall_package = self.runner(
                [
                    str(python),
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    "mneme-cc-plugin",
                    "mneme-core",
                ],
                cwd=root,
                env=_offline_pip_env(),
            )
            if uninstall_package.returncode != 0:
                self._check(
                    "claude_plugin_package_uninstall",
                    "failed",
                    _brief(uninstall_package),
                )
            else:
                self._check(
                    "claude_plugin_package_uninstall",
                    "verified",
                    "Claude plugin source artifact and local core wheel uninstalled from the temp venv",
                )

    def _validate_registry_metadata(self) -> None:
        errors: list[str] = []
        try:
            payload = _load_json_object(self.repo_root / "server.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._check("mcp_registry_metadata", "failed", str(exc))
            return
        npm_manifest = self._npm_package_manifest
        if npm_manifest is None:
            errors.append("validated npm artifact manifest is unavailable")
            npm_manifest = {}
        expected_identity = npm_manifest.get("mcpName")
        if not isinstance(expected_identity, str) or not expected_identity:
            errors.append("validated npm artifact manifest lacks a non-empty mcpName")
        elif payload.get("name") != expected_identity:
            errors.append("registry name does not match the npm artifact MCP identity")
        if payload.get("version") != self.expected_version:
            errors.append("registry top-level version does not match target")
        schema = payload.get("$schema")
        if not isinstance(schema, str) or not schema.startswith("https://"):
            errors.append("registry schema URL must be HTTPS")
        packages = payload.get("packages")
        if not isinstance(packages, list) or len(packages) != 1 or not isinstance(packages[0], dict):
            errors.append("registry metadata must declare exactly one package")
        else:
            package = packages[0]
            expected = {
                "registryType": "npm",
                "registryBaseUrl": "https://registry.npmjs.org",
                "identifier": "mneme-mcp-server",
                "version": self.expected_version,
                "runtimeHint": "npx",
                "transport": {"type": "stdio"},
            }
            if package != expected:
                errors.append("registry package metadata does not exactly match the npm artifact contract")
        if self._npm_tarball is None:
            errors.append("validated npm artifact is unavailable for registry cross-check")
        if errors:
            self._check("mcp_registry_metadata", "failed", "; ".join(errors))
            return
        registry_dir = self.artifact_dir / "registry"
        problem = _prepare_empty_dir(registry_dir)
        if problem is not None:
            self._check("mcp_registry_metadata", "failed", problem)
            return
        registry_artifact = registry_dir / "server.json"
        shutil.copy2(self.repo_root / "server.json", registry_artifact)
        self._artifact("mcp-registry-metadata", registry_artifact)
        self._check(
            "mcp_registry_metadata",
            "verified",
            "server.json identity, target version, npm package, runtime hint, and stdio transport agree",
        )

    def _verify_npm_install_and_migration(self) -> None:
        tarball = self._npm_tarball
        pnpm = shutil.which("pnpm")
        node = shutil.which("node")
        if tarball is None or pnpm is None or node is None:
            reason = "validated npm tarball, pnpm, and node are required"
            for name in (
                "npm_clean_install_uninstall",
                "claude_mem_migration_idempotency",
                "claude_mem_migration_destructive_guard",
            ):
                self._check(name, "unavailable", reason)
            self._record_rollback_unavailable()
            return
        with tempfile.TemporaryDirectory(prefix="mneme-rc-node-") as temp_dir:
            root = Path(temp_dir)
            artifact_package = root / "packages/mneme-mcp"
            consumer = root / "packages/consumer"
            consumer.mkdir(parents=True)
            unpacked = root / "unpacked"
            try:
                _safe_extract_tar(tarball, unpacked)
                shutil.move(str(unpacked / "package"), artifact_package)
                shutil.copy2(self.repo_root / "pnpm-lock.yaml", root / "pnpm-lock.yaml")
                shutil.copy2(
                    self.repo_root / "pnpm-workspace.yaml", root / "pnpm-workspace.yaml"
                )
                shutil.copy2(self.repo_root / "package.json", root / "package.json")
            except (OSError, ValueError) as exc:
                detail = f"cannot materialize temp workspace from local artifact: {exc}"
                for name in (
                    "npm_clean_install_uninstall",
                    "claude_mem_migration_idempotency",
                    "claude_mem_migration_destructive_guard",
                ):
                    self._check(name, "failed", detail)
                self._record_rollback_unavailable()
                return
            (consumer / "package.json").write_text(
                json.dumps(
                    {
                        "name": "mneme-package-rc-smoke",
                        "version": "0.0.0",
                        "private": True,
                        "type": "module",
                        "dependencies": {"mneme-mcp-server": "workspace:*"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            install = self.runner(
                [pnpm, "install", "--offline", "--no-frozen-lockfile"],
                cwd=root,
                env=_offline_node_env(),
                timeout=900,
            )
            if install.returncode != 0:
                detail = _brief(install)
                unavailable_markers = (
                    "ERR_PNPM_NO_OFFLINE",
                    "not found in the store",
                    "offline mode",
                )
                status: Status = (
                    "unavailable" if any(marker.lower() in detail.lower() for marker in unavailable_markers) else "failed"
                )
                for name in (
                    "npm_clean_install_uninstall",
                    "claude_mem_migration_idempotency",
                    "claude_mem_migration_destructive_guard",
                ):
                    self._check(name, status, detail)
                self._record_rollback_unavailable()
                return
            installed_root = consumer / "node_modules/mneme-mcp-server"
            cli = installed_root / "dist/cli/index.js"
            if not cli.is_file() or installed_root.resolve() != artifact_package.resolve():
                self._check(
                    "npm_clean_install_uninstall",
                    "failed",
                    "temp consumer did not link the safely extracted local artifact or lacks dist/cli/index.js",
                )
                self._check(
                    "claude_mem_migration_idempotency", "unavailable", "migration CLI is missing"
                )
                self._check(
                    "claude_mem_migration_destructive_guard", "unavailable", "migration CLI is missing"
                )
                self._record_rollback_unavailable()
                return
            help_result = self.runner([node, str(cli), "--help"], cwd=consumer)
            if help_result.returncode != 0:
                self._check("npm_clean_install_uninstall", "failed", _brief(help_result))
            else:
                self._check(
                    "npm_clean_install_uninstall",
                    "verified",
                    "local npm tarball was safely materialized as a temp workspace package; frozen repository lock data and the local pnpm store supplied dependencies offline; packaged CLI executed",
                )

            fixture_script = artifact_package / "create_fixture.mjs"
            fixture_script.write_text(
                textwrap.dedent(
                    """
                    import Database from "better-sqlite3";

                    const path = process.argv[2];
                    const db = new Database(path);
                    db.exec(`CREATE TABLE observations (
                      id INTEGER PRIMARY KEY,
                      memory_session_id TEXT,
                      project TEXT,
                      text TEXT,
                      type TEXT,
                      title TEXT,
                      subtitle TEXT,
                      facts TEXT,
                      narrative TEXT,
                      concepts TEXT,
                      files_read TEXT,
                      files_modified TEXT,
                      prompt_number INTEGER,
                      discovery_tokens INTEGER,
                      created_at TEXT,
                      created_at_epoch INTEGER,
                      content_hash TEXT,
                      generated_by_model TEXT,
                      agent_type TEXT,
                      agent_id TEXT,
                      metadata TEXT
                    )`);
                    db.prepare(`INSERT INTO observations (
                      id, memory_session_id, project, text, type, title, subtitle,
                      facts, narrative, concepts, files_read, files_modified,
                      prompt_number, discovery_tokens, created_at, created_at_epoch,
                      content_hash, generated_by_model, agent_type, agent_id, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
                      .run(1, "session-A", "fixture", "Body", "discovery", "Title", "",
                        "Fact", "Narrative", "concept", "[]", "[]", 1, 10,
                        "2026-04-01T08:00:00Z", 1775376000, "fixture-hash",
                        "fixture", "main", "agent-1", "{}");
                    db.close();
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source_db = root / "claude-mem.db"
            fixture = self.runner(
                [node, str(fixture_script), str(source_db)], cwd=artifact_package
            )
            if fixture.returncode != 0:
                detail = _brief(fixture)
                self._check("claude_mem_migration_idempotency", "failed", detail)
                self._check("claude_mem_migration_destructive_guard", "failed", detail)
            else:
                vault = root / "vault"
                (vault / ".mneme").mkdir(parents=True)
                base = [
                    node,
                    str(cli),
                    "migrate-from-claude-mem",
                    "--source",
                    str(source_db),
                    "--vault",
                    str(vault),
                    "--archive",
                    "preserve",
                ]
                first = self.runner(base, cwd=consumer)
                export_root = vault / "imported/claude-mem"
                try:
                    first_tree = _fingerprint_tree(export_root)
                except (OSError, ValueError) as exc:
                    first_tree = ()
                    self._check(
                        "claude_mem_migration_rollback",
                        "failed",
                        f"cannot fingerprint the first migration tree: {exc}",
                    )
                second = self.runner(base, cwd=consumer)
                errors: list[str] = []
                first_payload: dict[str, Any] = {}
                second_payload: dict[str, Any] = {}
                try:
                    parsed_first = json.loads(first.stdout)
                    parsed_second = json.loads(second.stdout)
                    if isinstance(parsed_first, dict):
                        first_payload = parsed_first
                    if isinstance(parsed_second, dict):
                        second_payload = parsed_second
                    if first.returncode != 0 or not first_payload:
                        errors.append(f"first migration failed: {_brief(first)}")
                    elif first_payload.get("observations", {}).get("migrated") != 1:
                        errors.append("first migration did not migrate exactly one observation")
                    if second.returncode != 0 or not second_payload:
                        errors.append(f"second migration failed: {_brief(second)}")
                    else:
                        observations = second_payload.get("observations", {})
                        if observations.get("migrated") != 0:
                            errors.append("second migration migrated duplicate observations")
                        if observations.get("rewritten") != 0:
                            errors.append("second migration rewrote unchanged observations")
                        if observations.get("skippedDedup") != 1:
                            errors.append("second migration did not report one deduplicated observation")
                except (AttributeError, json.JSONDecodeError) as exc:
                    errors.append(f"migration output is not valid stats JSON: {exc}")
                if errors:
                    self._check(
                        "claude_mem_migration_idempotency", "failed", "; ".join(errors)
                    )
                else:
                    self._check(
                        "claude_mem_migration_idempotency",
                        "verified",
                        "packaged migration moved one fixture record and the second run produced only a dedup skip",
                    )

                if not any(
                    check.name == "claude_mem_migration_rollback"
                    for check in self.report.checks
                ):
                    rollback_errors: list[str] = []
                    manifest_value = second_payload.get("rollbackManifestPath")
                    if not isinstance(manifest_value, str) or not manifest_value:
                        rollback_errors.append(
                            "second migration did not return a rollback manifest path"
                        )
                    elif not first_tree:
                        rollback_errors.append(
                            "first migration tree was empty or unavailable"
                        )
                    else:
                        rollback_command = [
                            node,
                            str(cli),
                            "rollback",
                            "--vault",
                            str(vault),
                            "--manifest",
                            manifest_value,
                        ]
                        rollback = self.runner(rollback_command, cwd=consumer)
                        repeated = self.runner(rollback_command, cwd=consumer)
                        try:
                            rollback_payload = json.loads(rollback.stdout)
                            repeated_payload = json.loads(repeated.stdout)
                            if (
                                rollback.returncode != 0
                                or not isinstance(rollback_payload, dict)
                                or rollback_payload.get("status") != "ok"
                                or rollback_payload.get("alreadyRolledBack") is not False
                            ):
                                rollback_errors.append(
                                    f"rollback command failed: {_brief(rollback)}"
                                )
                            if (
                                repeated.returncode != 0
                                or not isinstance(repeated_payload, dict)
                                or repeated_payload.get("status") != "ok"
                                or repeated_payload.get("alreadyRolledBack") is not True
                            ):
                                rollback_errors.append(
                                    "repeated rollback was not an idempotent success"
                                )
                        except json.JSONDecodeError as exc:
                            rollback_errors.append(
                                f"rollback output is not valid JSON: {exc}"
                            )
                        try:
                            if _fingerprint_tree(export_root) != first_tree:
                                rollback_errors.append(
                                    "rollback did not restore the exact first-run file tree"
                                )
                        except (OSError, ValueError) as exc:
                            rollback_errors.append(
                                f"cannot fingerprint the restored migration tree: {exc}"
                            )
                    if rollback_errors:
                        self._check(
                            "claude_mem_migration_rollback",
                            "failed",
                            "; ".join(rollback_errors),
                        )
                    else:
                        self._check(
                            "claude_mem_migration_rollback",
                            "verified",
                            "packaged rollback restored the exact prior SHA256 file inventory and a repeated rollback was idempotent",
                        )

                guarded_source = root / "claude-mem-guard.db"
                shutil.copy2(source_db, guarded_source)
                guarded_vault = root / "guard-vault"
                (guarded_vault / ".mneme").mkdir(parents=True)
                guard = self.runner(
                    [
                        node,
                        str(cli),
                        "migrate-from-claude-mem",
                        "--source",
                        str(guarded_source),
                        "--vault",
                        str(guarded_vault),
                        "--archive",
                        "move",
                    ],
                    cwd=consumer,
                )
                guard_ok = guard.returncode == 1 and guarded_source.is_file()
                try:
                    guard_payload = json.loads(guard.stdout)
                    guard_ok = guard_ok and isinstance(guard_payload, dict)
                    guard_ok = guard_ok and "confirmDelete" in " ".join(
                        cast(list[str], guard_payload.get("errors", []))
                    )
                except (json.JSONDecodeError, TypeError):
                    guard_ok = False
                if guard_ok:
                    self._check(
                        "claude_mem_migration_destructive_guard",
                        "verified",
                        "archive=move without explicit confirmation failed closed and preserved the source DB",
                    )
                else:
                    self._check(
                        "claude_mem_migration_destructive_guard", "failed", _brief(guard)
                    )

            remove = self.runner(
                [pnpm, "remove", "mneme-mcp-server"],
                cwd=consumer,
                env=_offline_node_env(),
            )
            if remove.returncode != 0 or installed_root.exists():
                self._check("npm_package_uninstall", "failed", _brief(remove))
            else:
                self._check(
                    "npm_package_uninstall",
                    "verified",
                    "mneme-mcp-server was removed from the isolated temp project",
                )
            self._record_rollback_unavailable()

    def _record_rollback_unavailable(self) -> None:
        if any(check.name == "claude_mem_migration_rollback" for check in self.report.checks):
            return
        self._check(
            "claude_mem_migration_rollback",
            "unavailable",
            "packaged rollback verification could not run because a prerequisite artifact or migration smoke was unavailable",
        )

    def _record_mcp_publisher_availability(self) -> None:
        publisher = shutil.which("mcp-publisher")
        if publisher is None:
            self._check(
                "official_mcp_publisher_validation",
                "unavailable",
                "mcp-publisher is not installed; local server.json contract checks still ran",
                required=False,
            )
            return
        version = self.runner([publisher, "--version"], cwd=self.repo_root, timeout=30)
        if version.returncode == 0:
            self._check(
                "official_mcp_publisher_validation",
                "unavailable",
                "mcp-publisher is present, but this no-network verifier does not assume an undocumented offline validation subcommand",
                required=False,
            )
        else:
            self._check(
                "official_mcp_publisher_validation",
                "unavailable",
                "mcp-publisher was found but did not execute: " + _brief(version),
                required=False,
            )

    def _write_checksums(self) -> None:
        if not self.report.artifacts:
            self._check("artifact_checksums", "unavailable", "no validated artifacts to hash")
            return
        checksum_path = self.artifact_dir / "SHA256SUMS"
        lines = []
        for artifact in sorted(self.report.artifacts, key=lambda item: item.path):
            path = Path(artifact.path)
            relative = path.relative_to(self.artifact_dir).as_posix()
            lines.append(f"{artifact.sha256}  {relative}")
        checksum_path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
        self._artifact("checksum-manifest", checksum_path)
        self._check(
            "artifact_checksums",
            "verified",
            f"wrote SHA256SUMS for {len(lines)} inventoried artifacts",
        )


def _print_report(report: VerificationReport) -> None:
    print(json.dumps(report.to_json(), indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Mneme repository root.",
    )
    parser.add_argument(
        "--expected-version",
        required=True,
        help="Strict MAJOR.MINOR.PATCH version expected in every artifact.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Persist artifacts here. Without this option, a temporary directory is used.",
    )
    parser.add_argument("--report-json", type=Path, help="Optional JSON report output path.")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter with build and hatchling available.",
    )
    args = parser.parse_args(argv)

    def execute(artifact_dir: Path) -> int:
        verifier = PackageRcVerifier(
            args.repo_root,
            args.expected_version,
            artifact_dir,
            python=args.python,
        )
        report = verifier.run()
        _print_report(report)
        if args.report_json is not None:
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(
                json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if report.outcome == "pass":
            return 0
        if report.outcome == "incomplete":
            return 2
        return 1

    if args.artifact_dir is not None:
        return execute(args.artifact_dir)
    with tempfile.TemporaryDirectory(prefix="mneme-package-rc-") as temp_dir:
        return execute(Path(temp_dir))


if __name__ == "__main__":
    raise SystemExit(main())
