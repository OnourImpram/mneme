"""Tests for CCE Phase 3: self-heal block in SessionStart and final checkpoint
in PreCompact.

All tests use direct handler invocation (no subprocess) and a tmp-path vault.
CCE is disabled by default in VaultConfig, so every test that exercises CCE
behavior must explicitly enable it via ``_enable_cce``.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mneme_core.cce.checkpoint import Checkpoint, WorkingSetItem, render_markdown
from mneme_core.cce.config import CceConfig, write_config
from mneme_core.vault.config import VaultConfig

from mneme_cc_plugin.hooks import pre_compact, session_start

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


def _capture(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


def _enable_cce(vault: VaultConfig, rehydration_token_budget: int = 4000) -> None:
    write_config(
        vault.cce_config_path,
        CceConfig(enabled=True, rehydration_token_budget=rehydration_token_budget),
    )


def _write_checkpoint(
    vault: VaultConfig,
    items: tuple[WorkingSetItem, ...],
    anchor: str = "abc123def456",
) -> None:
    """Write a checkpoint markdown + index entry into the vault."""
    cp = Checkpoint(
        anchor=anchor,
        created="2026-06-14T10:00:00+00:00",
        session_id="sess-001",
        prev_anchor=None,
        items=items,
    )
    vault.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    doc_path = vault.checkpoints_dir / f"2026-06-14-{anchor}.md"
    doc_path.write_text(render_markdown(cp), encoding="utf-8")

    vault.checkpoint_index.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "anchor": anchor,
        "id": f"checkpoint-{anchor}",
        "created": cp.created,
        "session_id": cp.session_id,
        "prev_anchor": None,
        "path": str(doc_path),
        "item_count": len(items),
        "top_salience": max((i.salience for i in items), default=0.0),
    }
    with vault.checkpoint_index.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record) + "\n")


def _stamp_precompact(vault: VaultConfig, ts: str | None = None) -> str:
    """Write last_precompact_at into state.json and return the timestamp."""
    stamp = ts or datetime.now(UTC).isoformat()
    state_path = vault.state_dir / "state.json"
    try:
        state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    state["last_precompact_at"] = stamp
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return stamp


def _write_transcript(vault: VaultConfig, *texts: str) -> Path:
    """Write a minimal JSONL transcript under the vault and return its path."""
    p = vault.root / "transcript.jsonl"
    with p.open("w", encoding="utf-8") as fp:
        for text in texts:
            rec = {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                }
            }
            fp.write(json.dumps(rec) + "\n")
    return p


def _context_from(monkeypatch: pytest.MonkeyPatch, buf: io.StringIO) -> str:
    out = json.loads(buf.getvalue())
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


# ---------------------------------------------------------------------------
# SessionStart — CCE disabled (default): behavior unchanged
# ---------------------------------------------------------------------------


class TestSessionStartCceDisabled:
    def test_no_self_heal_block_when_cce_disabled(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """CCE disabled (default) → preamble must not contain self-heal block."""
        # Set up a checkpoint and compaction signal so that if the gate
        # were absent, the block would fire.
        _write_checkpoint(
            vault,
            items=(WorkingSetItem(kind="decision", text="Important decision xyzzy", salience=0.9),),
        )
        _stamp_precompact(vault)
        buf = _capture(monkeypatch)
        session_start.handle({"source": "compact"}, vault)
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" not in ctx

    def test_no_self_heal_when_no_compaction(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """Even with CCE enabled, no block when there is no compaction signal."""
        _enable_cce(vault)
        _write_checkpoint(
            vault,
            items=(WorkingSetItem(kind="decision", text="Decision abcde", salience=0.9),),
        )
        # No last_precompact_at in state, no source=="compact" in event.
        buf = _capture(monkeypatch)
        session_start.handle({}, vault)
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" not in ctx


# ---------------------------------------------------------------------------
# SessionStart — CCE enabled, compaction via source=="compact"
# ---------------------------------------------------------------------------


class TestSessionStartSelfHealSourceCompact:
    def test_self_heal_block_injected_via_source_event(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision",
            text="Use atomic writes for all checkpoint persistence",
            salience=0.9,
        )
        _write_checkpoint(vault, items=(item,))
        # Transcript does NOT contain the item text → it is "dropped".
        transcript = _write_transcript(vault, "unrelated content about something else")
        buf = _capture(monkeypatch)
        session_start.handle(
            {"source": "compact", "transcript_path": str(transcript)}, vault
        )
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" in ctx
        assert "atomic writes" in ctx

    def test_self_heal_omitted_when_all_items_survived(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision",
            text="use atomic writes for all checkpoint persistence",
            salience=0.9,
        )
        _write_checkpoint(vault, items=(item,))
        # Transcript DOES contain the item text → nothing dropped.
        transcript = _write_transcript(
            vault, "we decided to use atomic writes for all checkpoint persistence"
        )
        buf = _capture(monkeypatch)
        session_start.handle(
            {"source": "compact", "transcript_path": str(transcript)}, vault
        )
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" not in ctx

    def test_self_heal_budget_capped(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """Items exceeding rehydration_token_budget are truncated.

        We use a budget of 20 tokens (~80 chars).  Each item text is
        ~57 chars (~14 tokens), so exactly one item fits and the rest
        produce the truncation note.
        """
        _enable_cce(vault, rehydration_token_budget=20)
        items = tuple(
            WorkingSetItem(
                kind="decision",
                text=f"Very important decision number {i} that must be remembered",
                salience=float(10 - i) / 10,
            )
            for i in range(5)
        )
        _write_checkpoint(vault, items=items)
        transcript = _write_transcript(vault, "completely unrelated content here xyz")
        buf = _capture(monkeypatch)
        session_start.handle(
            {"source": "compact", "transcript_path": str(transcript)}, vault
        )
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" in ctx
        # Not all 5 items fit in 20 tokens; the truncation note must appear.
        assert "more checkpoint item" in ctx

    def test_self_heal_block_fits_within_max_chars(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision", text="Decision that survived partial compaction", salience=0.9
        )
        _write_checkpoint(vault, items=(item,))
        transcript = _write_transcript(vault, "unrelated filler")
        buf = _capture(monkeypatch)
        session_start.handle(
            {"source": "compact", "transcript_path": str(transcript)}, vault
        )
        ctx = _context_from(monkeypatch, buf)
        assert len(ctx) <= session_start.MAX_CHARS

    def test_base_preamble_blocks_preserved(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """Existing preamble blocks (date, etc.) must still appear."""
        _enable_cce(vault)
        item = WorkingSetItem(kind="todo", text="Extra task that was dropped xyz", salience=0.6)
        _write_checkpoint(vault, items=(item,))
        transcript = _write_transcript(vault, "unrelated filler")
        buf = _capture(monkeypatch)
        session_start.handle(
            {"source": "compact", "transcript_path": str(transcript)}, vault
        )
        ctx = _context_from(monkeypatch, buf)
        assert "## Vault Context" in ctx
        assert "Recovered from compaction" in ctx


# ---------------------------------------------------------------------------
# SessionStart — CCE enabled, compaction via last_precompact_at delta
# ---------------------------------------------------------------------------


class TestSessionStartSelfHealPrecompactDelta:
    def test_self_heal_fires_via_precompact_delta(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """Fallback: no source=='compact', but last_precompact_at is set."""
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision",
            text="Rollback strategy decided via precompact delta path",
            salience=0.9,
        )
        _write_checkpoint(vault, items=(item,))
        _stamp_precompact(vault)  # sets last_precompact_at, no last_rehydrated yet
        transcript = _write_transcript(vault, "unrelated content abcde")
        buf = _capture(monkeypatch)
        session_start.handle({"transcript_path": str(transcript)}, vault)
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" in ctx
        assert "Rollback strategy" in ctx

    def test_marker_prevents_double_firing(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """After first rehydration the marker is written; second call must not inject."""
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision",
            text="Double fire prevention decision abcde",
            salience=0.9,
        )
        _write_checkpoint(vault, items=(item,))
        _stamp_precompact(vault)
        transcript = _write_transcript(vault, "unrelated content xyz")

        # First call — should inject and write the marker.
        buf1 = _capture(monkeypatch)
        session_start.handle({"transcript_path": str(transcript)}, vault)
        ctx1 = _context_from(monkeypatch, buf1)
        assert "Recovered from compaction" in ctx1

        # Second call with the same stamp — marker now matches, no injection.
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        buf2 = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf2)
        session_start.handle({"transcript_path": str(transcript)}, vault)
        ctx2 = json.loads(buf2.getvalue()).get("hookSpecificOutput", {}).get(
            "additionalContext", ""
        )
        assert "Recovered from compaction" not in ctx2

    def test_no_self_heal_when_marker_matches_stamp(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """last_rehydrated_precompact_at == last_precompact_at → no block."""
        _enable_cce(vault)
        item = WorkingSetItem(
            kind="decision", text="Already rehydrated decision abcde", salience=0.9
        )
        _write_checkpoint(vault, items=(item,))
        stamp = "2026-06-14T10:00:00+00:00"
        state = {
            "last_precompact_at": stamp,
            "last_rehydrated_precompact_at": stamp,
        }
        state_path = vault.state_dir / "state.json"
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        transcript = _write_transcript(vault, "unrelated filler xyz")
        buf = _capture(monkeypatch)
        session_start.handle({"transcript_path": str(transcript)}, vault)
        ctx = _context_from(monkeypatch, buf)
        assert "Recovered from compaction" not in ctx


# ---------------------------------------------------------------------------
# SessionStart — no vault
# ---------------------------------------------------------------------------


class TestSessionStartNoVault:
    def test_no_vault_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = _capture(monkeypatch)
        session_start.handle({"source": "compact"}, None)
        out = json.loads(buf.getvalue())
        assert "hookSpecificOutput" not in out


# ---------------------------------------------------------------------------
# PreCompact — CCE disabled (default): behavior unchanged
# ---------------------------------------------------------------------------


class TestPreCompactCceDisabled:
    def test_stamps_last_precompact_at_cce_disabled(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """CCE disabled → no checkpoint file, but stamp still written."""
        _capture(monkeypatch)
        pre_compact.handle({}, vault)
        state = json.loads(
            (vault.state_dir / "state.json").read_text(encoding="utf-8")
        )
        assert "last_precompact_at" in state
        # No checkpoint markdown should exist.
        if vault.checkpoints_dir.exists():
            assert not list(vault.checkpoints_dir.glob("*.md"))

    def test_no_checkpoint_written_when_cce_disabled(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        pre_compact.handle({"session_id": "s1", "transcript_path": ""}, vault)
        if vault.checkpoints_dir.exists():
            assert list(vault.checkpoints_dir.glob("*.md")) == []


# ---------------------------------------------------------------------------
# PreCompact — CCE enabled
# ---------------------------------------------------------------------------


class TestPreCompactCceEnabled:
    def _stage_events(self, vault: VaultConfig, session_id: str) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = vault.staging_dir / "testhost" / day
        out_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/important.py"},
            "captured_at": datetime.now(UTC).isoformat(),
        }
        with (out_dir / "12-00-events.jsonl").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record) + "\n")

    def test_checkpoint_written_before_stamp(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        self._stage_events(vault, "s1")
        _capture(monkeypatch)
        pre_compact.handle({"session_id": "s1", "transcript_path": ""}, vault)
        md_files = list(vault.checkpoints_dir.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "type: checkpoint" in content

    def test_index_entry_written(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        self._stage_events(vault, "s2")
        _capture(monkeypatch)
        pre_compact.handle({"session_id": "s2", "transcript_path": ""}, vault)
        assert vault.checkpoint_index.is_file()
        lines = [
            ln
            for ln in vault.checkpoint_index.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "anchor" in record

    def test_stamp_still_written_after_checkpoint(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        self._stage_events(vault, "s3")
        _capture(monkeypatch)
        pre_compact.handle({"session_id": "s3", "transcript_path": ""}, vault)
        state = json.loads(
            (vault.state_dir / "state.json").read_text(encoding="utf-8")
        )
        assert "last_precompact_at" in state

    def test_checkpoint_failure_does_not_block_stamp(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        """If CCE checkpoint write fails the stamp must still be written."""
        _enable_cce(vault)
        from mneme_cc_plugin.hooks import pre_compact as pc_mod

        def _raise(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated checkpoint failure")

        monkeypatch.setattr(pc_mod, "_write_cce_checkpoint", _raise)
        buf = _capture(monkeypatch)
        pre_compact.handle({"session_id": "s4", "transcript_path": ""}, vault)
        # The stamp must have been written.
        state = json.loads(
            (vault.state_dir / "state.json").read_text(encoding="utf-8")
        )
        assert "last_precompact_at" in state
        # And the hook must still emit a valid continue envelope.
        assert json.loads(buf.getvalue())["continue"] is True

    def test_emit_still_called_on_checkpoint_error(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _enable_cce(vault)
        from mneme_cc_plugin.hooks import pre_compact as pc_mod

        def _raise_oserror(*_a: object, **_kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(pc_mod, "_write_cce_checkpoint", _raise_oserror)
        buf = _capture(monkeypatch)
        # Must not raise; emit must produce a valid response.
        pre_compact.handle({}, vault)
        assert json.loads(buf.getvalue())["continue"] is True

    def test_no_vault_still_emits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = _capture(monkeypatch)
        pre_compact.handle({"session_id": "s-none"}, None)
        assert json.loads(buf.getvalue())["continue"] is True
