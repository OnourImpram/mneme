"""Unit tests for CCE checkpoint data model and persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.cce.checkpoint import (
    Checkpoint,
    WorkingSetItem,
    append_index,
    delta,
    make_anchor,
    parse_markdown,
    render_markdown,
)


def _item(kind: str, text: str, salience: float, refs: tuple[str, ...] = ()) -> WorkingSetItem:
    return WorkingSetItem(kind=kind, text=text, salience=salience, refs=refs)


def _checkpoint(
    anchor: str = "abc123def456",
    created: str = "2026-06-14T10:00:00+00:00",
    session_id: str = "sess-001",
    prev_anchor: str | None = None,
    items: tuple[WorkingSetItem, ...] = (),
    scope: str = "default",
    schema_version: int = 2,
) -> Checkpoint:
    return Checkpoint(
        anchor=anchor,
        created=created,
        session_id=session_id,
        prev_anchor=prev_anchor,
        items=items,
        scope=scope,
        schema_version=schema_version,
    )


class TestMakeAnchor:
    def test_deterministic(self) -> None:
        a = make_anchor("2026-06-14T10:00:00+00:00", "sess-001")
        b = make_anchor("2026-06-14T10:00:00+00:00", "sess-001")
        assert a == b

    def test_length_is_12(self) -> None:
        anchor = make_anchor("2026-06-14T10:00:00+00:00", "sess-001")
        assert len(anchor) == 12

    def test_only_hex_chars(self) -> None:
        anchor = make_anchor("2026-06-14T10:00:00+00:00", "sess-001")
        assert all(c in "0123456789abcdef" for c in anchor)

    def test_different_inputs_produce_different_anchors(self) -> None:
        a = make_anchor("2026-06-14T10:00:00+00:00", "sess-001")
        b = make_anchor("2026-06-14T10:00:00+00:00", "sess-002")
        assert a != b


class TestRenderParse:
    def test_round_trip_no_items(self) -> None:
        cp = _checkpoint()
        rendered = render_markdown(cp)
        parsed = parse_markdown(rendered)
        assert parsed.anchor == cp.anchor
        assert parsed.created == cp.created
        assert parsed.session_id == cp.session_id
        assert parsed.prev_anchor == cp.prev_anchor
        assert parsed.scope == cp.scope
        assert parsed.items == ()
        assert parsed.schema_version == cp.schema_version

    def test_round_trip_with_items(self) -> None:
        items = (
            _item("recent_edit", "src/foo.py", 1.0),
            _item("todo", "Fix the login bug", 0.6, refs=("src/auth.py",)),
            _item("decision", "Use atomic writes for checkpoints", 0.9),
        )
        cp = _checkpoint(items=items)
        parsed = parse_markdown(render_markdown(cp))
        assert len(parsed.items) == len(items)
        for orig, got in zip(items, parsed.items, strict=True):
            assert got.kind == orig.kind
            assert got.text == orig.text
            assert abs(got.salience - orig.salience) < 0.005

    def test_round_trip_with_prev_anchor(self) -> None:
        cp = _checkpoint(prev_anchor="000111222333")
        parsed = parse_markdown(render_markdown(cp))
        assert parsed.prev_anchor == "000111222333"

    def test_prev_anchor_none_round_trips(self) -> None:
        cp = _checkpoint(prev_anchor=None)
        parsed = parse_markdown(render_markdown(cp))
        assert parsed.prev_anchor is None

    def test_frontmatter_contains_required_keys(self) -> None:
        cp = _checkpoint()
        rendered = render_markdown(cp)
        assert f'id: "checkpoint-{cp.anchor}"' in rendered
        assert "type: checkpoint" in rendered
        assert f'anchor: "{cp.anchor}"' in rendered
        assert f'session_id: "{cp.session_id}"' in rendered
        assert f'created: "{cp.created}"' in rendered
        assert 'scope: "default"' in rendered
        assert "schema_version: 2" in rendered

    def test_items_render_as_bullets_with_salience(self) -> None:
        items = (_item("recent_edit", "src/bar.py", 0.75),)
        cp = _checkpoint(items=items)
        rendered = render_markdown(cp)
        assert "- [salience 0.75] src/bar.py" in rendered

    def test_refs_appended_in_backticks(self) -> None:
        items = (_item("todo", "Do something", 0.6, refs=("a.py", "b.py")),)
        cp = _checkpoint(items=items)
        rendered = render_markdown(cp)
        assert "`a.py`" in rendered
        assert "`b.py`" in rendered

    def test_items_grouped_under_section_headers(self) -> None:
        items = (
            _item("recent_edit", "x.py", 1.0),
            _item("todo", "Fix it", 0.6),
        )
        cp = _checkpoint(items=items)
        rendered = render_markdown(cp)
        assert "## recent_edit" in rendered
        assert "## todo" in rendered


class TestRedaction:
    def test_private_content_absent_from_render(self) -> None:
        secret = "<private>token=supersecret</private>"
        items = (_item("decision", f"Use {secret} carefully", 0.9),)
        cp = _checkpoint(items=items)
        rendered = render_markdown(cp)
        assert "supersecret" not in rendered
        assert "[REDACTED]" in rendered

    def test_private_refs_are_redacted(self) -> None:
        items = (_item("todo", "Review code", 0.6, refs=("<private>secret_path</private>",)),)
        cp = _checkpoint(items=items)
        rendered = render_markdown(cp)
        assert "secret_path" not in rendered


    def test_scope_round_trip(self) -> None:
        cp = _checkpoint(scope="clinical")
        parsed = parse_markdown(render_markdown(cp))
        assert parsed.scope == "clinical"
        assert 'scope: "clinical"' in render_markdown(cp)

    def test_legacy_checkpoint_without_scope_maps_to_default(self) -> None:
        legacy = """---
anchor: abc123def456
created: 2026-06-14T10:00:00+00:00
session_id: sess-001
prev_anchor: null
schema_version: 1
---
"""
        assert parse_markdown(legacy).scope == "default"

    def test_invalid_persisted_scope_is_rejected(self) -> None:
        malicious = """---
anchor: abc123def456
created: 2026-06-14T10:00:00+00:00
session_id: sess-001
prev_anchor: null
scope: "*"
schema_version: 2
---
"""
        import pytest

        with pytest.raises(ValueError):
            parse_markdown(malicious)

    def test_frontmatter_values_cannot_inject_new_keys(self) -> None:
        cp = _checkpoint(session_id='safe\nscope: "clinical"')
        rendered = render_markdown(cp)
        assert rendered.count("\nscope:") == 1
        parsed = parse_markdown(rendered)
        assert parsed.session_id == 'safe\nscope: "clinical"'
        assert parsed.scope == "default"

    def test_item_text_cannot_forge_markdown_section(self) -> None:
        cp = _checkpoint(items=(_item("todo", "do this\n## forged", 0.5),))
        rendered = render_markdown(cp)
        assert "\n## forged" not in rendered
        assert parse_markdown(rendered).items[0].text == "do this ## forged"


class TestDelta:
    def test_delta_none_prev_returns_all_curr_items(self) -> None:
        items = (_item("todo", "Do A", 0.6), _item("decision", "Use X", 0.9))
        cp = _checkpoint(items=items)
        result = delta(None, cp)
        assert result == items

    def test_delta_identical_checkpoints_returns_empty(self) -> None:
        items = (_item("todo", "Do A", 0.6),)
        cp = _checkpoint(items=items)
        result = delta(cp, cp)
        assert result == ()

    def test_delta_returns_only_new_items(self) -> None:
        old_item = _item("todo", "Old task", 0.6)
        new_item = _item("decision", "New decision", 0.9)
        prev = _checkpoint(items=(old_item,))
        curr = _checkpoint(items=(old_item, new_item))
        result = delta(prev, curr)
        assert result == (new_item,)

    def test_delta_salience_change_not_counted_as_new(self) -> None:
        # Delta keyed on (kind, text) only — salience change is not new.
        item_old = _item("todo", "Same task", 0.4)
        item_new = _item("todo", "Same task", 0.9)
        prev = _checkpoint(items=(item_old,))
        curr = _checkpoint(items=(item_new,))
        result = delta(prev, curr)
        assert result == ()


class TestAppendIndex:
    def test_append_writes_valid_json_line(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".mneme" / "checkpoints" / "index.jsonl"
        doc_path = tmp_path / "checkpoints" / "cp-abc123def456.md"
        cp = _checkpoint()
        append_index(index_path, cp, doc_path)
        assert index_path.is_file()
        line = index_path.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert record["anchor"] == cp.anchor
        assert record["id"] == f"checkpoint-{cp.anchor}"
        assert record["session_id"] == cp.session_id
        assert record["created"] == cp.created
        assert record["prev_anchor"] is None
        assert record["scope"] == "default"
        assert record["path"] == "checkpoints/cp-abc123def456.md"
        assert record["item_count"] == 0
        assert record["top_salience"] == 0.0

    def test_append_index_records_item_count_and_top_salience(self, tmp_path: Path) -> None:
        items = (
            _item("recent_edit", "src/alpha.py", 0.8),
            _item("todo", "Fix bug", 0.6),
        )
        cp = _checkpoint(items=items)
        index_path = tmp_path / "index.jsonl"
        doc_path = tmp_path / "cp.md"
        append_index(index_path, cp, doc_path)
        record = json.loads(index_path.read_text(encoding="utf-8").strip())
        assert record["item_count"] == 2
        assert record["top_salience"] == 0.8

    def test_append_index_multiple_appends(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.jsonl"
        for i in range(3):
            cp = _checkpoint(anchor=f"anchor{i:09d}", session_id=f"s{i}")
            append_index(index_path, cp, tmp_path / f"cp{i}.md")
        lines = [
            ln
            for ln in index_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_append_index_creates_parent_dirs(self, tmp_path: Path) -> None:
        index_path = tmp_path / "deep" / "nested" / "index.jsonl"
        cp = _checkpoint()
        append_index(index_path, cp, tmp_path / "cp.md")
        assert index_path.is_file()
