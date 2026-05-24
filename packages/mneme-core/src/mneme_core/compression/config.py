"""Operator-facing compression knobs persisted under the vault.

The compression layer is OPT-IN. The config file is the canonical
state of that choice. The pipeline reads this file before every run,
the CLI ``mneme-core compress enable`` writes it, and the CLI
``mneme-core compress disable`` clears the ``enabled`` flag. No other
module is allowed to flip the bit.

Sacred constraints honored here:

* ``compression_enabled = false`` is the default for new vaults.
* The config file is created with ``compression_enabled = false``
  by ``ensure_default_config`` if it does not exist. Surprise costs
  require an explicit human action.
* The cost cap defaults to USD 25 / month. Operators raise or lower
  it via the CLI.

File layout: ``vault/.mneme/compression.json``. JSON, not TOML, so
the file can be machine-edited without a TOML writer dep.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .llm import DEFAULT_MODEL

DEFAULT_COST_CAP_USD_MONTHLY = 25.0
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_PAYLOAD_BYTES = 100_000

SCHEMA_VERSION = 1


@dataclass
class CompressionConfig:
    """All operator-tunable knobs for the compression pipeline."""

    enabled: bool = False
    cost_cap_usd_monthly: float = DEFAULT_COST_CAP_USD_MONTHLY
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def default(cls) -> CompressionConfig:
        return cls()


def read_config(path: Path) -> CompressionConfig:
    """Read the config file. Missing or malformed files yield defaults."""
    if not path.is_file():
        return CompressionConfig.default()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CompressionConfig.default()
    if not isinstance(raw, dict):
        return CompressionConfig.default()
    defaults = asdict(CompressionConfig.default())
    merged: dict[str, Any] = {**defaults, **raw}
    try:
        return CompressionConfig(
            enabled=bool(merged.get("enabled", False)),
            cost_cap_usd_monthly=float(
                merged.get("cost_cap_usd_monthly", DEFAULT_COST_CAP_USD_MONTHLY)
            ),
            model=str(merged.get("model", DEFAULT_MODEL)),
            max_tokens=int(merged.get("max_tokens", DEFAULT_MAX_TOKENS)),
            max_payload_bytes=int(
                merged.get("max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES)
            ),
            schema_version=int(merged.get("schema_version", SCHEMA_VERSION)),
        )
    except (TypeError, ValueError):
        return CompressionConfig.default()


def write_config(path: Path, config: CompressionConfig) -> None:
    """Atomic-enough write of the config JSON. Parent dir created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")


def ensure_default_config(path: Path) -> CompressionConfig:
    """Write the default config if no file exists. Idempotent.

    Used at ``mneme install`` time so the file is present and the
    operator can see compression is off without running anything.
    """
    if path.is_file():
        return read_config(path)
    cfg = CompressionConfig.default()
    write_config(path, cfg)
    return cfg
