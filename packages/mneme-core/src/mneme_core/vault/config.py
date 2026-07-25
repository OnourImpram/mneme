"""Vault location resolution and derived path accessors.

The vault is the user-owned directory of markdown files that mneme reads,
writes, and indexes. This module owns the canonical resolution order so
every other module agrees on where the vault lives.

Resolution order, highest priority first:

1. ``MNEME_VAULT`` environment variable.
2. The path passed explicitly to ``VaultConfig.resolve(explicit=...)``.
3. The ``vault`` key in ``~/.mneme/config.toml``.
4. The nearest ancestor of the current working directory that contains
   a ``.mneme/`` marker directory.
5. ``~/mneme-vault`` if it exists.

If none of the above resolve to a usable path, ``VaultNotFoundError`` is
raised with a message instructing the user how to fix the situation.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..scope import DEFAULT_SCOPE, concrete_scope_or_none

CONFIG_FILENAME = "config.toml"
MARKER_DIR = ".mneme"
DEFAULT_VAULT_NAME = "mneme-vault"


class VaultNotFoundError(RuntimeError):
    """Raised when no vault can be located via the resolution order."""


@dataclass(frozen=True)
class VaultConfig:
    """Immutable handle for a vault root and its derived paths.

    Instances are produced by ``VaultConfig.resolve()`` or
    ``VaultConfig.from_path()``. Once constructed, the root is fixed and
    every consumer in the codebase agrees on the same path.
    """

    root: Path

    @property
    def state_dir(self) -> Path:
        """Derived state and index directory inside the vault."""
        return self.root / MARKER_DIR

    @property
    def fts5_db(self) -> Path:
        return self.state_dir / "fts5.sqlite"

    @property
    def telemetry_dir(self) -> Path:
        return self.state_dir / "telemetry"

    @property
    def audit_log_dir(self) -> Path:
        return self.state_dir / "audit"

    @property
    def staging_dir(self) -> Path:
        return self.state_dir / "staging"

    @property
    def kg_queue_dir(self) -> Path:
        """Per-host JSONL queue staged by PostToolUse for the KG worker."""
        return self.state_dir / "kg-queue"

    @property
    def kg_processed_dir(self) -> Path:
        """Archive root for queue files the worker has successfully drained."""
        return self.state_dir / "kg-processed"

    @property
    def kg_active_flag(self) -> Path:
        """Presence-gate: KG staging only runs when this file exists."""
        return self.state_dir / "kg-active.flag"

    @property
    def kg_credentials_path(self) -> Path:
        """Neo4j credentials JSON consumed by the out-of-band worker."""
        return self.state_dir / "neo4j-credentials.json"

    @property
    def kg_community_refresh_flag(self) -> Path:
        """Flag written by Stop telling the next worker drain to also rebuild communities."""
        return self.state_dir / "kg-community-refresh.flag"

    @property
    def kg_worker_log(self) -> Path:
        """Worker run telemetry, one JSON object per drain attempt."""
        return self.telemetry_dir / "kg-worker.jsonl"

    @property
    def kg_cost_ledger(self) -> Path:
        """Configurable cost ledger for opt-in compression and KG LLM spend."""
        return self.state_dir / "cost-ledger.jsonl"

    @property
    def compression_config_path(self) -> Path:
        """Phase F opt-in config JSON. Default is ``enabled = false``."""
        return self.state_dir / "compression.json"

    @property
    def compression_pause_flag(self) -> Path:
        """Phase F pause sentinel. When present, the pipeline aborts."""
        return self.state_dir / "compression-paused.flag"

    @property
    def compression_daily_log_dir(self) -> Path:
        """Phase F target directory for auto-captured observation blocks."""
        return self.root / "sessions"

    @property
    def patterns_dir(self) -> Path:
        """Phase F.6 directory for reusable action-pattern documents."""
        return self.root / "patterns"

    @property
    def trajectories_dir(self) -> Path:
        """Phase F.6 directory for per-session trajectory transcripts."""
        return self.root / "trajectories"

    @property
    def cce_config_path(self) -> Path:
        """CCE opt-in config JSON. Default is ``enabled = false``."""
        return self.state_dir / "cce.json"

    @property
    def checkpoints_dir(self) -> Path:
        """CCE markdown checkpoint documents (git-visible, human-readable)."""
        return self.root / "checkpoints"

    @property
    def checkpoint_index(self) -> Path:
        """CCE JSONL index for fast checkpoint lookup."""
        return self.state_dir / "checkpoints" / "index.jsonl"

    def default_scope(
        self,
        *,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> str:
        """Resolve a concrete configured default without widening access."""
        environment = os.environ if env is None else env
        env_scope = environment.get("MNEME_SCOPE")
        if env_scope:
            return concrete_scope_or_none(env_scope) or DEFAULT_SCOPE

        cfg = _read_config_toml(home)
        if cfg is not None:
            configured_scope = cfg.get("default_scope")
            if isinstance(configured_scope, str) and configured_scope:
                return concrete_scope_or_none(configured_scope) or DEFAULT_SCOPE

        return DEFAULT_SCOPE

    @classmethod
    def resolve(
        cls,
        explicit: Path | None = None,
        *,
        cwd: Path | None = None,
        home: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> VaultConfig:
        """Resolve the vault root via the documented priority order."""
        candidate = _resolve_path(explicit, cwd=cwd, home=home, env=env)
        if candidate is None:
            raise VaultNotFoundError(
                "Could not locate an mneme vault. Set MNEME_VAULT, pass --vault, "
                f"add a `vault` key to ~/.mneme/{CONFIG_FILENAME}, place a "
                f"{MARKER_DIR}/ marker in or above the current directory, or "
                f"create ~/{DEFAULT_VAULT_NAME}."
            )
        return cls(root=candidate.resolve())

    @classmethod
    def from_path(cls, path: Path) -> VaultConfig:
        """Construct directly from an explicit path. Skips resolution order."""
        return cls(root=path.expanduser().resolve())


def _resolve_path(
    explicit: Path | None,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    environment = os.environ if env is None else env
    env_vault = environment.get("MNEME_VAULT")
    if env_vault:
        return Path(env_vault).expanduser()

    if explicit is not None:
        return explicit.expanduser()

    resolved_home = Path.home() if home is None else home
    cfg = _read_config_toml(resolved_home)
    if cfg and "vault" in cfg:
        return Path(str(cfg["vault"])).expanduser()

    here = (Path.cwd() if cwd is None else cwd).resolve()
    home_boundary = resolved_home.resolve()
    for parent in [here, *here.parents]:
        # ~/.mneme is the global configuration directory. Treating it as a
        # marker would make every working directory below home select all of
        # home as the vault.
        if parent == home_boundary:
            break
        if (parent / MARKER_DIR).is_dir():
            return parent

    default = resolved_home / DEFAULT_VAULT_NAME
    if default.exists():
        return default

    return None


def _read_config_toml(home: Path | None = None) -> dict[str, Any] | None:
    cfg_path = (Path.home() if home is None else home) / MARKER_DIR / CONFIG_FILENAME
    if not cfg_path.is_file():
        return None
    with cfg_path.open("rb") as f:
        return tomllib.load(f)
