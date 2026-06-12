"""Unit tests for the deterministic session summary (distill.session_summary).

The summary is the default-on, zero-LLM Stop-path distillation: it reads
the already-redacted staging JSONL records plus (optionally) the head of
the Claude Code transcript, and renders a localized extractive summary.
It must never raise on the Stop path and must never emit private content.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mneme_core.distill.session_summary import (
    SummaryConfig,
    build_session_summary,
    load_summary_config,
)
from mneme_core.vault.config import VaultConfig


def _vault(tmp_path: Path) -> VaultConfig:
    (tmp_path / ".mneme").mkdir(parents=True, exist_ok=True)
    return VaultConfig.from_path(tmp_path)


def _stage_events(vault: VaultConfig, records: list[dict]) -> None:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir = vault.staging_dir / "testhost" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "12-00-events.jsonl"
    with out_file.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _records_for(session_id: str) -> list[dict]:
    base = datetime.now(UTC).isoformat()
    return [
        {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/alpha.py"},
            "captured_at": base,
        },
        {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/beta.md"},
            "captured_at": base,
        },
        {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "captured_at": base,
        },
        {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "ruff check ."},
            "captured_at": base,
        },
    ]


def _write_transcript(tmp_path: Path, text: str) -> Path:
    transcript = tmp_path / "transcript.jsonl"
    line = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
    return transcript


class TestSummaryConfig:
    def test_default_is_deterministic_on(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        cfg = load_summary_config(vault)
        assert cfg.deterministic is True
        assert cfg.language == "en"

    def test_config_file_overrides(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        (vault.state_dir / "summary.json").write_text(
            json.dumps({"deterministic": False, "language": "tr"}),
            encoding="utf-8",
        )
        cfg = load_summary_config(vault)
        assert cfg.deterministic is False
        assert cfg.language == "tr"

    def test_malformed_config_falls_back_to_defaults(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        (vault.state_dir / "summary.json").write_text("{not json", encoding="utf-8")
        cfg = load_summary_config(vault)
        assert cfg.deterministic is True


class TestBuildSessionSummary:
    def test_summary_contains_files_and_tool_counts(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _stage_events(vault, _records_for("s1"))
        summary = build_session_summary(vault, "s1", "")
        assert summary is not None
        assert "src/alpha.py" in summary
        assert "docs/beta.md" in summary
        assert "Bash" in summary and "2" in summary

    def test_other_sessions_are_excluded(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _stage_events(vault, _records_for("s1"))
        _stage_events(
            vault,
            [
                {
                    "session_id": "s2",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "secret/other-session.py"},
                    "captured_at": datetime.now(UTC).isoformat(),
                }
            ],
        )
        summary = build_session_summary(vault, "s1", "")
        assert summary is not None
        assert "other-session.py" not in summary

    def test_transcript_intent_is_included_and_redacted(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _stage_events(vault, _records_for("s1"))
        transcript = _write_transcript(
            tmp_path, "Fix the login bug <private>token=abc123</private> today"
        )
        summary = build_session_summary(vault, "s1", str(transcript))
        assert summary is not None
        assert "Fix the login bug" in summary
        assert "abc123" not in summary
        assert "[REDACTED]" in summary

    def test_turkish_template_used_for_tr(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        (vault.state_dir / "summary.json").write_text(
            json.dumps({"language": "tr"}), encoding="utf-8"
        )
        _stage_events(vault, _records_for("s1"))
        summary = build_session_summary(vault, "s1", "")
        assert summary is not None
        assert "Dokunulan dosyalar" in summary

    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        (vault.state_dir / "summary.json").write_text(
            json.dumps({"deterministic": False}), encoding="utf-8"
        )
        _stage_events(vault, _records_for("s1"))
        assert build_session_summary(vault, "s1", "") is None

    def test_no_data_returns_none(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        assert build_session_summary(vault, "s1", "") is None

    def test_never_raises_on_garbage_staging(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = vault.staging_dir / "h" / day
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "09-00-events.jsonl").write_text(
            "{broken json\n\x00\xff\n", encoding="utf-8", errors="ignore"
        )
        # Must not raise; with no valid records it returns None.
        assert build_session_summary(vault, "s1", "") is None

    def test_unknown_language_falls_back_to_english(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        (vault.state_dir / "summary.json").write_text(
            json.dumps({"language": "xx"}), encoding="utf-8"
        )
        _stage_events(vault, _records_for("s1"))
        summary = build_session_summary(vault, "s1", "")
        assert summary is not None
        assert "Files touched" in summary

    def test_file_list_is_capped(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        base = datetime.now(UTC).isoformat()
        records = [
            {
                "session_id": "s1",
                "tool_name": "Edit",
                "tool_input": {"file_path": f"src/file_{i}.py"},
                "captured_at": base,
            }
            for i in range(SummaryConfig().max_files + 5)
        ]
        _stage_events(vault, records)
        summary = build_session_summary(vault, "s1", "")
        assert summary is not None
        listed = [ln for ln in summary.splitlines() if "src/file_" in ln]
        assert len(listed) <= SummaryConfig().max_files
