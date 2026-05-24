"""PostToolUse hook: stage tool events for the FTS5 indexer.

For each captured tool invocation, write a JSON staging record into
the vault's staging directory. The indexer picks up staged records on
its next pass and folds them into the FTS5 index. The hook itself
performs no LLM call and writes no markdown into the vault - that is
the Stop hook's job.

The full ``capture_event`` implementation lives in
``mneme_core.compression.staging``. This module is a thin Claude Code
adapter that constructs a StagingConfig pointing at the resolved vault
and forwards the event payload.
"""

from __future__ import annotations

import sys
from typing import Any

from mneme_core.compression.staging import StagingConfig, capture_event
from mneme_core.distill.shell_compress import (
    ShellCompressOpts,
    compress_shell_output,
)
from mneme_core.kg import kg_config_from_vault, stage_event
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

# Bash tool responses can land verbose stdout under any of these keys
# depending on Claude Code version. Walk known keys and compress any
# string longer than the threshold before staging. Keys not present in
# the event are silently skipped.
_SHELL_OUTPUT_KEYS = ("stdout", "stderr", "output", "result", "text", "content")
_COMPRESS_MIN_BYTES = 256


def _compress_bash_payload(event: dict[str, Any]) -> None:
    """Apply distill.shell_compress in-place to Bash tool response strings.

    Codex Pass 2 review fix: docs/HOOKS advertised PostToolUse-time
    shell output compression but the hook forwarded raw events. This
    function compresses long stdout/stderr fields before they reach
    the staging JSONL, matching the documented behavior.
    """
    if event.get("tool_name") != "Bash":
        return
    resp = event.get("tool_response")
    if not isinstance(resp, dict):
        return
    opts = ShellCompressOpts()
    for key in _SHELL_OUTPUT_KEYS:
        value = resp.get(key)
        if not isinstance(value, str):
            continue
        if len(value.encode("utf-8")) < _COMPRESS_MIN_BYTES:
            continue
        stats = compress_shell_output(value, opts)
        if stats.compressed_bytes < stats.original_bytes:
            resp[key] = stats.compressed_text


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="PostToolUse")
        return

    # Compress before staging so both the FTS index and any later
    # compression call see the reduced payload.
    try:
        _compress_bash_payload(event)
    except Exception as exc:
        sys.stderr.write(f"[mneme:PostToolUse] shell compress skipped: {exc}\n")

    config = StagingConfig(
        staging_dir=vault.staging_dir,
        audit_dir=vault.audit_log_dir,
    )
    try:
        capture_event(event, config)
    except Exception as exc:
        sys.stderr.write(f"[mneme:PostToolUse] capture skipped: {exc}\n")

    # KG staging is gated by kg_active_flag inside stage_event itself.
    # Cheap no-op for lite/standard profiles where the flag is absent.
    try:
        stage_event(event, kg_config_from_vault(vault))
    except Exception as exc:
        sys.stderr.write(f"[mneme:PostToolUse] kg stage skipped: {exc}\n")

    emit(hook_event_name="PostToolUse")


def main() -> int:
    return run_hook(handle, hook_event_name="PostToolUse")


if __name__ == "__main__":
    sys.exit(main())
