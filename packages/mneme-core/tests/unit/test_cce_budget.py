"""Unit tests for cce.budget — token estimation and context-fill helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.cce.budget import estimate_context_fill, estimate_tokens


class TestEstimateTokens:
    def test_empty_string_returns_one(self) -> None:
        assert estimate_tokens("") == 1

    def test_four_chars_returns_one(self) -> None:
        assert estimate_tokens("abcd") == 1

    def test_eight_chars_returns_two(self) -> None:
        assert estimate_tokens("abcdefgh") == 2

    def test_long_text(self) -> None:
        text = "x" * 400
        assert estimate_tokens(text) == 100

    def test_minimum_is_one(self) -> None:
        assert estimate_tokens("a") == 1


class TestEstimateContextFill:
    def _write_transcript(self, path: Path, messages: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for msg in messages:
                fp.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        result = estimate_context_fill(tmp_path / "missing.jsonl", 200_000)
        assert result == 0.0

    def test_empty_file_returns_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text("", encoding="utf-8")
        assert estimate_context_fill(p, 200_000) == 0.0

    def test_corrupt_lines_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text("{broken json\nnot json either\n", encoding="utf-8")
        assert estimate_context_fill(p, 200_000) == 0.0

    def test_plain_string_content(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        # 400 chars of text → 100 tokens
        text = "a" * 400
        msg = {"message": {"role": "user", "content": text}}
        self._write_transcript(p, [msg])
        fill = estimate_context_fill(p, 1000)
        assert fill == pytest.approx(0.1, abs=0.01)

    def test_typed_block_content(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        text = "b" * 400  # 100 tokens
        msg = {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            }
        }
        self._write_transcript(p, [msg])
        fill = estimate_context_fill(p, 1000)
        assert fill == pytest.approx(0.1, abs=0.01)

    def test_fill_clamped_to_one(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        text = "c" * 40_000  # 10_000 tokens
        msg = {"message": {"role": "user", "content": text}}
        self._write_transcript(p, [msg])
        fill = estimate_context_fill(p, 100)  # tiny window
        assert fill == 1.0

    def test_zero_context_window_returns_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text(json.dumps({"message": {"content": "hello"}}) + "\n", encoding="utf-8")
        assert estimate_context_fill(p, 0) == 0.0

    def test_multiple_messages_summed(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        # Two messages, each 400 chars → 100 tokens each → 200 total
        msgs = [
            {"message": {"role": "user", "content": "a" * 400}},
            {"message": {"role": "assistant", "content": "b" * 400}},
        ]
        self._write_transcript(p, msgs)
        fill = estimate_context_fill(p, 2000)
        assert fill == pytest.approx(0.1, abs=0.01)
