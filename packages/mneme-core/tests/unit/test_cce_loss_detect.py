"""Unit tests for cce.loss_detect — detect_dropped and load_latest_checkpoint."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.cce.checkpoint import Checkpoint, WorkingSetItem, render_markdown
from mneme_core.cce.loss_detect import detect_dropped, load_latest_checkpoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(kind: str, text: str, salience: float) -> WorkingSetItem:
    return WorkingSetItem(kind=kind, text=text, salience=salience)


def _checkpoint(*items: WorkingSetItem) -> Checkpoint:
    return Checkpoint(
        anchor="abc123def456",
        created="2026-06-14T10:00:00+00:00",
        session_id="sess-001",
        prev_anchor=None,
        items=items,
    )


def _transcript_with(*texts: str, tmp_path: Path) -> Path:
    """Write a JSONL transcript file containing the given text strings."""
    p = tmp_path / "transcript.jsonl"
    with p.open("w", encoding="utf-8") as fp:
        for text in texts:
            record = {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
            fp.write(json.dumps(record) + "\n")
    return p


# ---------------------------------------------------------------------------
# detect_dropped
# ---------------------------------------------------------------------------


class TestDetectDropped:
    def test_all_items_present_returns_empty(self, tmp_path: Path) -> None:
        item_a = _item("decision", "Use atomic writes for checkpoints", 0.9)
        item_b = _item("todo", "Fix the login bug before release", 0.6)
        cp = _checkpoint(item_a, item_b)
        transcript = _transcript_with(
            "We decided to use atomic writes for checkpoints. "
            "We also need to fix the login bug before release.",
            tmp_path=tmp_path,
        )
        result = detect_dropped(cp, transcript)
        assert result == ()

    def test_missing_items_returned(self, tmp_path: Path) -> None:
        present = _item("decision", "Use atomic writes for checkpoints", 0.9)
        dropped_item = _item("todo", "Refactor the auth module completely", 0.6)
        cp = _checkpoint(present, dropped_item)
        transcript = _transcript_with(
            "We decided to use atomic writes for checkpoints.",
            tmp_path=tmp_path,
        )
        result = detect_dropped(cp, transcript)
        assert len(result) == 1
        assert result[0].text == dropped_item.text

    def test_result_sorted_by_salience_descending(self, tmp_path: Path) -> None:
        low = _item("todo", "Low priority cleanup task xyzzy", 0.3)
        high = _item("decision", "High priority architecture decision abcde", 0.9)
        medium = _item("recent_edit", "Medium salience file edit foobar", 0.6)
        cp = _checkpoint(low, high, medium)
        # Empty transcript — all items dropped.
        transcript = _transcript_with("unrelated content here", tmp_path=tmp_path)
        result = detect_dropped(cp, transcript)
        assert len(result) == 3
        saliences = [i.salience for i in result]
        assert saliences == sorted(saliences, reverse=True)
        assert result[0].salience == 0.9
        assert result[1].salience == 0.6
        assert result[2].salience == 0.3

    def test_missing_transcript_returns_empty_tuple(self, tmp_path: Path) -> None:
        item = _item("decision", "Some important decision made here", 0.9)
        cp = _checkpoint(item)
        missing = tmp_path / "nonexistent.jsonl"
        result = detect_dropped(cp, missing)
        assert result == ()

    def test_corrupt_transcript_returns_empty_tuple(self, tmp_path: Path) -> None:
        item = _item("decision", "Some important decision made here", 0.9)
        cp = _checkpoint(item)
        corrupt = tmp_path / "corrupt.jsonl"
        corrupt.write_bytes(b"\xff\xfe{broken\x00\x01")
        # detect_dropped must not raise; corrupt lines are skipped.
        result = detect_dropped(cp, corrupt)
        # With no parseable content the item is considered dropped.
        assert isinstance(result, tuple)

    def test_empty_checkpoint_returns_empty(self, tmp_path: Path) -> None:
        cp = _checkpoint()
        transcript = _transcript_with("lots of content here", tmp_path=tmp_path)
        result = detect_dropped(cp, transcript)
        assert result == ()

    def test_partial_survival_only_missing_returned(self, tmp_path: Path) -> None:
        survived_a = _item("decision", "Deploy using blue green strategy always", 0.9)
        survived_b = _item("todo", "Write tests for the new auth flow soon", 0.6)
        lost_c = _item("recent_edit", "Refactor database connection pooling layer", 0.7)
        lost_d = _item("intent", "Implement the payment gateway integration now", 0.4)
        cp = _checkpoint(survived_a, survived_b, lost_c, lost_d)
        transcript = _transcript_with(
            "We decided to deploy using blue green strategy always. "
            "We also need to write tests for the new auth flow soon.",
            tmp_path=tmp_path,
        )
        result = detect_dropped(cp, transcript)
        dropped_texts = {i.text for i in result}
        assert lost_c.text in dropped_texts
        assert lost_d.text in dropped_texts
        assert survived_a.text not in dropped_texts
        assert survived_b.text not in dropped_texts

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        item = _item("decision", "Use Atomic Writes For Checkpoints", 0.9)
        cp = _checkpoint(item)
        # Transcript has lowercase version.
        transcript = _transcript_with(
            "we decided to use atomic writes for checkpoints.",
            tmp_path=tmp_path,
        )
        result = detect_dropped(cp, transcript)
        assert result == ()

    def test_whitespace_collapsed_in_match(self, tmp_path: Path) -> None:
        item = _item("decision", "Use  atomic   writes", 0.9)
        cp = _checkpoint(item)
        transcript = _transcript_with("use atomic writes confirmed", tmp_path=tmp_path)
        result = detect_dropped(cp, transcript)
        assert result == ()

    def test_transcript_with_plain_string_content(self, tmp_path: Path) -> None:
        """Transcript records using plain string content (not list of blocks)."""
        item = _item("decision", "Finalize the deployment pipeline today", 0.9)
        cp = _checkpoint(item)
        p = tmp_path / "transcript.jsonl"
        record = {
            "message": {
                "role": "assistant",
                "content": "finalize the deployment pipeline today confirmed",
            }
        }
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = detect_dropped(cp, p)
        assert result == ()


# ---------------------------------------------------------------------------
# load_latest_checkpoint
# ---------------------------------------------------------------------------


class TestLoadLatestCheckpoint:
    def _append_checkpoint(
        self,
        tmp_path: Path,
        *,
        anchor: str,
        scope: str = "default",
        record_scope: str | None = None,
        relative_path: bool = False,
    ) -> tuple[Path, Path]:
        cp = Checkpoint(
            anchor=anchor,
            created="2026-06-14T10:00:00+00:00",
            session_id=f"sess-{anchor}",
            prev_anchor=None,
            items=(
                WorkingSetItem(
                    kind="decision", text=f"Decision for {anchor}", salience=0.9
                ),
            ),
            scope=scope,
        )
        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        doc_path = checkpoints_dir / f"2026-06-14-{anchor}.md"
        doc_path.write_text(render_markdown(cp), encoding="utf-8")

        index_dir = tmp_path / ".mneme" / "checkpoints"
        index_dir.mkdir(parents=True, exist_ok=True)
        index_path = index_dir / "index.jsonl"
        record: dict[str, object] = {
            "anchor": anchor,
            "path": doc_path.relative_to(tmp_path).as_posix()
            if relative_path
            else str(doc_path),
        }
        if record_scope is not None:
            record["scope"] = record_scope
        elif scope != "default":
            record["scope"] = scope
        with index_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record) + "\n")
        return index_path, doc_path

    def test_loads_latest_checkpoint(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(
            tmp_path, anchor="abc123def456", relative_path=True
        )
        cp = load_latest_checkpoint(index_path)
        assert cp is not None
        assert cp.anchor == "abc123def456"
        assert cp.scope == "default"
        assert len(cp.items) == 1

    def test_missing_index_returns_none(self, tmp_path: Path) -> None:
        assert load_latest_checkpoint(tmp_path / "missing" / "index.jsonl") is None

    def test_empty_index_returns_none(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.jsonl"
        index_path.write_text("", encoding="utf-8")
        assert load_latest_checkpoint(index_path) is None

    def test_corrupt_index_line_returns_none(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.jsonl"
        index_path.write_text("{not valid json\n", encoding="utf-8")
        assert load_latest_checkpoint(index_path) is None

    def test_corrupt_trailing_record_does_not_hide_previous_valid_record(
        self, tmp_path: Path
    ) -> None:
        index_path, _ = self._append_checkpoint(
            tmp_path, anchor="abc123def456", relative_path=True
        )
        with index_path.open("a", encoding="utf-8") as fp:
            fp.write("{not valid json\n")
        result = load_latest_checkpoint(index_path)
        assert result is not None
        assert result.anchor == "abc123def456"

    def test_index_with_missing_path_returns_none(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.jsonl"
        index_path.write_text(json.dumps({"anchor": "abc123def456"}) + "\n")
        assert load_latest_checkpoint(index_path) is None

    def test_index_pointing_to_missing_md_returns_none(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.jsonl"
        index_path.write_text(
            json.dumps({"anchor": "abc123def456", "path": str(tmp_path / "ghost.md")})
            + "\n",
            encoding="utf-8",
        )
        assert load_latest_checkpoint(index_path) is None

    def test_returns_last_line_when_multiple_entries(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(tmp_path, anchor="firstanchor12")
        self._append_checkpoint(tmp_path, anchor="secondanchor1")
        result = load_latest_checkpoint(index_path)
        assert result is not None
        assert result.anchor == "secondanchor1"

    def test_scope_selects_latest_visible_checkpoint(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(
            tmp_path, anchor="clinical0001", scope="clinical"
        )
        self._append_checkpoint(tmp_path, anchor="research0001", scope="research")
        clinical = load_latest_checkpoint(index_path, scope="clinical")
        research = load_latest_checkpoint(index_path, scope="research")
        assert clinical is not None and clinical.anchor == "clinical0001"
        assert research is not None and research.anchor == "research0001"

    def test_legacy_unscoped_record_is_not_visible_in_other_scope(
        self, tmp_path: Path
    ) -> None:
        index_path, _ = self._append_checkpoint(tmp_path, anchor="legacy000001")
        assert load_latest_checkpoint(index_path, scope="clinical") is None
        assert load_latest_checkpoint(index_path, scope="default") is not None

    def test_explicit_wildcard_returns_latest_across_scopes(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(
            tmp_path, anchor="clinical0001", scope="clinical"
        )
        self._append_checkpoint(tmp_path, anchor="research0001", scope="research")
        result = load_latest_checkpoint(index_path, scope="*")
        assert result is not None
        assert result.anchor == "research0001"

    def test_record_and_markdown_scope_must_both_match(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(
            tmp_path,
            anchor="mismatch0001",
            scope="clinical",
            record_scope="research",
        )
        assert load_latest_checkpoint(index_path, scope="research") is None
        assert load_latest_checkpoint(index_path, scope="clinical") is None

    def test_absolute_path_escape_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-checkpoint.md"
        outside.write_text(
            render_markdown(
                Checkpoint(
                    anchor="outside00001",
                    created="2026-06-14T10:00:00+00:00",
                    session_id="outside",
                    prev_anchor=None,
                    items=(),
                )
            ),
            encoding="utf-8",
        )
        index_path = tmp_path / ".mneme" / "checkpoints" / "index.jsonl"
        index_path.parent.mkdir(parents=True)
        index_path.write_text(
            json.dumps({"anchor": "outside00001", "path": str(outside)}) + "\n",
            encoding="utf-8",
        )
        assert load_latest_checkpoint(index_path) is None

    def test_anchor_mismatch_is_rejected(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(tmp_path, anchor="realanchor001")
        lines = index_path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record["anchor"] = "forgedanchor1"
        index_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        assert load_latest_checkpoint(index_path) is None

    def test_invalid_requested_scope_fails_closed(self, tmp_path: Path) -> None:
        index_path, _ = self._append_checkpoint(tmp_path, anchor="abc123def456")
        assert load_latest_checkpoint(index_path, scope=" clinical ") is None
