"""Operator-facing CCE knobs persisted under the vault.

The Context Continuity Engine is OPT-IN. The config file is the canonical
state of that choice. No other module is allowed to flip the ``enabled`` bit.

File layout: ``vault/.mneme/cce.json``. JSON, not TOML, so the file can be
machine-edited without a TOML writer dep.

Sacred constraints honored here:

* ``enabled = false`` is the default for new vaults.
* Missing or corrupt config files yield the safe defaults — the engine never
  activates itself on a bad read.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_CONTEXT_THRESHOLD_PCT = 0.65
DEFAULT_REHYDRATION_TOKEN_BUDGET = 4000
DEFAULT_MAX_CHECKPOINTS = 10
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
DEFAULT_MIN_CHECKPOINT_INTERVAL_EVENTS = 20
DEFAULT_SALIENCE_WEIGHTS: dict[str, float] = {
    "recent_edit": 1.0,
    "fts5_hit": 0.7,
    "recent_file": 0.4,
    "decision": 0.9,
    "todo": 0.6,
}


@dataclass
class CceConfig:
    """All operator-tunable knobs for the Context Continuity Engine."""

    enabled: bool = False
    context_threshold_pct: float = DEFAULT_CONTEXT_THRESHOLD_PCT
    rehydration_token_budget: int = DEFAULT_REHYDRATION_TOKEN_BUDGET
    max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    min_checkpoint_interval_events: int = DEFAULT_MIN_CHECKPOINT_INTERVAL_EVENTS
    salience_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SALIENCE_WEIGHTS)
    )
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def default(cls) -> CceConfig:
        return cls()


def read_config(path: Path) -> CceConfig:
    """Read the config file. Missing or malformed files yield defaults."""
    if not path.is_file():
        return CceConfig.default()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CceConfig.default()
    if not isinstance(raw, dict):
        return CceConfig.default()
    defaults = asdict(CceConfig.default())
    merged: dict[str, Any] = {**defaults, **raw}
    try:
        raw_weights = merged.get("salience_weights", DEFAULT_SALIENCE_WEIGHTS)
        if not isinstance(raw_weights, dict):
            raw_weights = DEFAULT_SALIENCE_WEIGHTS
        salience_weights = {str(k): float(v) for k, v in raw_weights.items()}
        return CceConfig(
            enabled=bool(merged.get("enabled", False)),
            context_threshold_pct=float(
                merged.get("context_threshold_pct", DEFAULT_CONTEXT_THRESHOLD_PCT)
            ),
            rehydration_token_budget=int(
                merged.get("rehydration_token_budget", DEFAULT_REHYDRATION_TOKEN_BUDGET)
            ),
            max_checkpoints=int(
                merged.get("max_checkpoints", DEFAULT_MAX_CHECKPOINTS)
            ),
            context_window_tokens=int(
                merged.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
            ),
            min_checkpoint_interval_events=int(
                merged.get(
                    "min_checkpoint_interval_events",
                    DEFAULT_MIN_CHECKPOINT_INTERVAL_EVENTS,
                )
            ),
            salience_weights=salience_weights,
            schema_version=int(merged.get("schema_version", SCHEMA_VERSION)),
        )
    except (TypeError, ValueError):
        return CceConfig.default()


def write_config(path: Path, config: CceConfig) -> None:
    """Atomic-enough write of the config JSON. Parent dir created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")
