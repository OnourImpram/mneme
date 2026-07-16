"""Unit tests for cce.build — build_checkpoint and write_checkpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mneme_core.cce.build import build_checkpoint, write_checkpoint
from mneme_core.cce.checkpoint import parse_markdown
from mneme_core.cce.config import CceConfig, write_config
from mneme_core.vault.config import VaultConfig


def _vault(tmp_path: Path) -> VaultConfig:
    (tmp_path / ".mneme").mkdir(parents=True, exist_ok=True)
    return VaultConfig.from_path(tmp_path)


def _enable_cce(vault: VaultConfig) -> None:
    write_config(vault.cce_config_path, CceConfig(enabled=True))


def _stage_events(vault: VaultConfig, session_id: str) -> None:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir = vault.staging_dir / "testhost" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py"},
            "captured_at": datetime.now(UTC).isoformat(),
        },
        {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/README.md"},
            "captured_at": datetime.now(UTC).isoformat(),
        },
    ]
    with (out_dir / "12-00-events.jsonl").open("a", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec) + "\n")


def _write_transcript(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "transcript.jsonl"
    line = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")
    return p


class TestBuildCheckpoint:
    def test_returns_checkpoint_with_anchor(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        cp = build_checkpoint(vault, "s1", "", None)
        assert len(cp.anchor) == 12
        assert cp.session_id == "s1"
        assert cp.prev_anchor is None
        assert cp.scope == "default"
        assert cp.schema_version == 2

    def test_files_touched_appear_as_items(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        _stage_events(vault, "s1")
        cp = build_checkpoint(vault, "s1", "", None)
        texts = [i.text for i in cp.items]
        assert any("src/main.py" in t for t in texts)
        assert any("docs/README.md" in t for t in texts)

    def test_prev_anchor_threaded(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        cp = build_checkpoint(vault, "s1", "", "deadbeef1234")
        assert cp.prev_anchor == "deadbeef1234"

    def test_redaction_applied(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        transcript = _write_transcript(
            tmp_path, "Fix the bug <private>token=secret123</private>"
        )
        cp = build_checkpoint(vault, "s1", str(transcript), None)
        for item in cp.items:
            assert "secret123" not in item.text

    def test_items_have_positive_salience(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        _stage_events(vault, "s1")
        cp = build_checkpoint(vault, "s1", "", None)
        for item in cp.items:
            assert item.salience > 0.0

    def test_empty_session_returns_checkpoint_with_no_items(
        self, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        cp = build_checkpoint(vault, "s-empty", "", None)
        assert isinstance(cp.items, tuple)

    def test_transcript_intent_captured(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        transcript = _write_transcript(tmp_path, "Implement the login feature")
        cp = build_checkpoint(vault, "s1", str(transcript), None)
        texts = [i.text for i in cp.items]
        assert any("login" in t.lower() or "implement" in t.lower() for t in texts)


    def test_configured_scope_is_persisted(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        monkeypatch.setenv("MNEME_SCOPE", "clinical")
        cp = build_checkpoint(vault, "s1", "", None)
        assert cp.scope == "clinical"
        doc_path = write_checkpoint(vault, cp)
        assert 'scope: "clinical"' in doc_path.read_text(encoding="utf-8")
        record = json.loads(vault.checkpoint_index.read_text(encoding="utf-8"))
        assert record["scope"] == "clinical"


class TestWriteCheckpoint:
    def test_markdown_file_created(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        cp = build_checkpoint(vault, "s1", "", None)
        doc_path = write_checkpoint(vault, cp)
        assert doc_path.is_file()
        content = doc_path.read_text(encoding="utf-8")
        assert "type: checkpoint" in content

    def test_index_line_appended(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        cp = build_checkpoint(vault, "s1", "", None)
        write_checkpoint(vault, cp)
        assert vault.checkpoint_index.is_file()
        lines = [
            ln
            for ln in vault.checkpoint_index.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["anchor"] == cp.anchor
        assert record["scope"] == "default"
        assert record["path"].startswith("checkpoints/")

    def test_round_trip_via_parse_markdown(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _enable_cce(vault)
        _stage_events(vault, "s1")
        cp = build_checkpoint(vault, "s1", "", None)
        doc_path = write_checkpoint(vault, cp)
        parsed = parse_markdown(doc_path.read_text(encoding="utf-8"))
        assert parsed.anchor == cp.anchor
        assert parsed.session_id == cp.session_id

    def test_pruning_removes_oldest_when_over_cap(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        # Set max_checkpoints=2 so the third write triggers pruning.
        write_config(vault.cce_config_path, CceConfig(enabled=True, max_checkpoints=2))
        cp1 = build_checkpoint(vault, "s1", "", None)
        write_checkpoint(vault, cp1)
        cp2 = build_checkpoint(vault, "s2", "", cp1.anchor)
        write_checkpoint(vault, cp2)
        cp3 = build_checkpoint(vault, "s3", "", cp2.anchor)
        write_checkpoint(vault, cp3)
        md_files = list(vault.checkpoints_dir.glob("*.md"))
        assert len(md_files) == 2

    def test_index_consistent_after_pruning(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        write_config(vault.cce_config_path, CceConfig(enabled=True, max_checkpoints=2))
        cp1 = build_checkpoint(vault, "s1", "", None)
        write_checkpoint(vault, cp1)
        cp2 = build_checkpoint(vault, "s2", "", cp1.anchor)
        write_checkpoint(vault, cp2)
        cp3 = build_checkpoint(vault, "s3", "", cp2.anchor)
        write_checkpoint(vault, cp3)
        index_lines = [
            ln
            for ln in vault.checkpoint_index.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        # Only anchors for surviving files should remain in the index.
        surviving_anchors = {json.loads(ln)["anchor"] for ln in index_lines}
        existing_md_anchors = {
            p.stem.split("-", 3)[-1] for p in vault.checkpoints_dir.glob("*.md")
        }
        assert surviving_anchors == existing_md_anchors
