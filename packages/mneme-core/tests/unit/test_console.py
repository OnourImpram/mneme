"""Unit tests for mneme_core.console.

Coverage targets:
- audit_to_dict: shape, determinism, graph passthrough.
- read_graph_summary: happy path, absent file, malformed JSON.
- render_html_report: self-contained (no external resources), XSS-safe escaping,
  determinism, empty-vault smoke test.
- main (CliRunner): --format json returns parseable JSON; default html contains
  the audit-data element.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mneme_core.audit import AuditReport, build_audit
from mneme_core.console import (
    _cli,
    audit_to_dict,
    read_graph_summary,
    render_html_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    note_count: int = 3,
    type_counts: dict[str, int] | None = None,
    security: dict[str, object] | None = None,
) -> AuditReport:
    if type_counts is None:
        type_counts = {"note": 2, "journal": 1}
    if security is None:
        security = {"files_flagged": 0, "total_findings": 0, "by_kind": {}, "by_severity": {}}
    return AuditReport(
        note_count=note_count,
        type_counts=type_counts,
        security=security,
    )


# ---------------------------------------------------------------------------
# audit_to_dict
# ---------------------------------------------------------------------------


class TestAuditToDict:
    def test_shape(self) -> None:
        report = _make_report()
        d = audit_to_dict(report)
        assert d["note_count"] == 3
        assert "type_counts" in d
        assert "security" in d
        assert "graph" in d
        assert d["graph"] is None

    def test_type_counts_sorted(self) -> None:
        report = _make_report(type_counts={"zebra": 1, "apple": 2, "mango": 3})
        d = audit_to_dict(report)
        keys = list(d["type_counts"].keys())
        assert keys == sorted(keys)

    def test_security_passthrough(self) -> None:
        sec = {"files_flagged": 2, "total_findings": 5, "by_kind": {"secret": 5}}
        report = _make_report(security=sec)
        d = audit_to_dict(report)
        assert d["security"] is sec

    def test_graph_none_by_default(self) -> None:
        d = audit_to_dict(_make_report())
        assert d["graph"] is None

    def test_graph_dict_when_passed(self) -> None:
        g = {"nodes": 10, "edges": 4}
        d = audit_to_dict(_make_report(), graph_summary=g)
        assert d["graph"] == {"nodes": 10, "edges": 4}


# ---------------------------------------------------------------------------
# read_graph_summary
# ---------------------------------------------------------------------------


class TestReadGraphSummary:
    def test_happy_path(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        graph_data = {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"src": "a", "dst": "b"}],
        }
        (mneme_dir / "graph.json").write_text(json.dumps(graph_data), encoding="utf-8")
        result = read_graph_summary(tmp_path)
        assert result == {"nodes": 2, "edges": 1}

    def test_absent_file_returns_none(self, tmp_path: Path) -> None:
        result = read_graph_summary(tmp_path)
        assert result is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        (mneme_dir / "graph.json").write_text("{not valid json", encoding="utf-8")
        result = read_graph_summary(tmp_path)
        assert result is None

    def test_non_dict_json_returns_none(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        (mneme_dir / "graph.json").write_text("[1, 2, 3]", encoding="utf-8")
        result = read_graph_summary(tmp_path)
        assert result is None

    def test_no_raise_on_missing_dir(self, tmp_path: Path) -> None:
        # .mneme dir does not exist at all
        result = read_graph_summary(tmp_path / "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# render_html_report
# ---------------------------------------------------------------------------


class TestRenderHtmlReport:
    def _base_data(self) -> dict[str, object]:
        return audit_to_dict(_make_report())

    def test_self_contained_no_external_resources(self) -> None:
        html = render_html_report(self._base_data())
        # No http/https external links
        assert "http://" not in html
        assert "https://" not in html
        # No external script src=
        lower = html.lower()
        # <script src= would indicate an external script
        assert "<script src=" not in lower
        # No <link elements (would import external CSS)
        assert "<link" not in lower

    def test_xss_safe_script_tag_in_type_name(self) -> None:
        xss_key = "</script><img src=x onerror=alert(1)>"
        data = audit_to_dict(
            AuditReport(
                note_count=1,
                type_counts={xss_key: 1},
                security={"files_flagged": 0, "total_findings": 0, "by_kind": {}},
            )
        )
        html = render_html_report(data)
        # The raw dangerous substring must NOT appear unescaped
        assert "</script><img" not in html
        # The < must be unicode-escaped inside the JSON block
        assert "\\u003c/script\\u003e" in html

    def test_xss_safe_script_in_security_evidence(self) -> None:
        data = audit_to_dict(
            AuditReport(
                note_count=0,
                type_counts={},
                security={
                    "files_flagged": 1,
                    "total_findings": 1,
                    "by_kind": {"injection": 1},
                    "evidence": "<script>alert(1)</script>",
                },
            )
        )
        html = render_html_report(data)
        # The live <script>alert must not appear as executable markup
        assert "<script>alert" not in html
        # Escaped form must be present
        assert "\\u003cscript\\u003e" in html

    def test_deterministic(self) -> None:
        data = self._base_data()
        assert render_html_report(data) == render_html_report(data)

    def test_deterministic_with_graph(self) -> None:
        data = audit_to_dict(_make_report(), graph_summary={"nodes": 5, "edges": 3})
        assert render_html_report(data) == render_html_report(data)

    def test_empty_vault_valid_html(self, tmp_path: Path) -> None:
        report = build_audit(tmp_path)
        data = audit_to_dict(report)
        html = render_html_report(data)
        assert len(html) > 0
        assert html.strip().startswith("<!DOCTYPE html")

    def test_contains_audit_data_element(self) -> None:
        html = render_html_report(self._base_data())
        assert 'id="audit-data"' in html

    def test_custom_title(self) -> None:
        html = render_html_report(self._base_data(), title="My Custom Report")
        assert "My Custom Report" in html

    def test_graph_panel_present_when_graph_data(self) -> None:
        data = audit_to_dict(_make_report(), graph_summary={"nodes": 7, "edges": 2})
        html = render_html_report(data)
        assert '"nodes":7' in html or '"nodes": 7' in html or "nodes" in html

    def test_ampersand_escaped(self) -> None:
        data = audit_to_dict(
            AuditReport(
                note_count=1,
                type_counts={"a&b": 1},
                security={"files_flagged": 0, "total_findings": 0, "by_kind": {}},
            )
        )
        html = render_html_report(data)
        # Raw & inside JSON block must be escaped
        # (it appears in the JSON payload as &)
        assert "\\u0026" in html


# ---------------------------------------------------------------------------
# CLI via CliRunner
# ---------------------------------------------------------------------------


class TestConsoleCliRunner:
    def test_format_json_parseable(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(_cli, ["--vault", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert "note_count" in parsed
        assert "type_counts" in parsed
        assert "security" in parsed

    def test_default_format_html_contains_audit_data(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(_cli, ["--vault", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "audit-data" in result.output

    def test_out_file_written(self, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(_cli, ["--vault", str(tmp_path), "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "audit-data" in content

    def test_out_file_json(self, tmp_path: Path) -> None:
        out = tmp_path / "report.json"
        runner = CliRunner()
        result = runner.invoke(
            _cli, ["--vault", str(tmp_path), "--out", str(out), "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert "note_count" in parsed

    def test_with_markdown_notes(self, tmp_path: Path) -> None:
        """Vault with actual markdown files — audit finds them."""
        (tmp_path / "note1.md").write_text(
            "---\ntype: journal\n---\nHello world\n", encoding="utf-8"
        )
        (tmp_path / "note2.md").write_text("# No frontmatter\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(_cli, ["--vault", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["note_count"] == 2
