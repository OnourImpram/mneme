"""Tests for mneme_core.audit: the read-only vault audit aggregator."""

from __future__ import annotations

from pathlib import Path

from mneme_core.audit import build_audit, render_audit

_NOTE = """\
---
id: {id}
type: {type}
created: 2026-06-02T00:00:00+00:00
---

{body}
"""


def _write(path: Path, *, note_id: str, note_type: str, body: str) -> None:
    path.write_text(_NOTE.format(id=note_id, type=note_type, body=body), encoding="utf-8")


class TestBuildAudit:
    def test_counts_notes_by_type(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.md", note_id="a", note_type="topic", body="hello")
        _write(tmp_path / "b.md", note_id="b", note_type="topic", body="world")
        _write(tmp_path / "c.md", note_id="c", note_type="reference", body="ref")
        report = build_audit(tmp_path)
        assert report.note_count == 3
        assert report.type_counts["topic"] == 2
        assert report.type_counts["reference"] == 1

    def test_security_summary_flags_poison(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "poison.md",
            note_id="p",
            note_type="topic",
            body="token: ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        )
        report = build_audit(tmp_path)
        assert report.security["files_flagged"] == 1
        assert report.security["total_findings"] >= 1

    def test_note_without_frontmatter_bucketed_none(self, tmp_path: Path) -> None:
        (tmp_path / "raw.md").write_text("just text, no frontmatter\n", encoding="utf-8")
        report = build_audit(tmp_path)
        assert report.note_count == 1
        assert report.type_counts.get("(none)") == 1

    def test_empty_vault(self, tmp_path: Path) -> None:
        report = build_audit(tmp_path)
        assert report.note_count == 0
        assert report.type_counts == {}
        assert report.security["files_flagged"] == 0


class TestRenderAudit:
    def test_render_contains_sections(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.md", note_id="a", note_type="topic", body="hi")
        text = render_audit(build_audit(tmp_path))
        assert "# Vault audit" in text
        assert "Notes: 1" in text
        assert "topic: 1" in text
        assert "## Security" in text

    def test_render_empty_vault(self, tmp_path: Path) -> None:
        text = render_audit(build_audit(tmp_path))
        assert "(no notes)" in text
