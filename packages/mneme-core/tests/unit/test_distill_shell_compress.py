"""Tests for the shell-output compressor."""

from __future__ import annotations

from mneme_core.distill.shell_compress import (
    ShellCompressOpts,
    compress_shell_output,
)


class TestEmptyInput:
    def test_empty_string_yields_zero_stats(self) -> None:
        stats = compress_shell_output("")
        assert stats.original_bytes == 0
        assert stats.compressed_bytes == 0
        assert stats.compressed_text == ""

    def test_none_safe(self) -> None:
        # The function tolerates falsy input.
        stats = compress_shell_output("")  # noqa: F841 - just smoke
        assert stats.compressed_text == ""


class TestAnsiStrip:
    def test_strips_color_codes(self) -> None:
        text = "\x1b[31merror\x1b[0m: thing"
        stats = compress_shell_output(text)
        assert "error: thing" in stats.compressed_text
        assert "\x1b" not in stats.compressed_text

    def test_can_disable(self) -> None:
        text = "\x1b[31mred\x1b[0m"
        opts = ShellCompressOpts(strip_ansi=False)
        stats = compress_shell_output(text, opts)
        assert "\x1b" in stats.compressed_text


class TestDedupeAdjacent:
    def test_collapses_repeated_lines(self) -> None:
        text = "foo\nfoo\nfoo\nbar"
        stats = compress_shell_output(text)
        assert "foo [x3]" in stats.compressed_text
        assert "bar" in stats.compressed_text

    def test_single_run_unchanged(self) -> None:
        text = "alpha\nbeta\ngamma"
        stats = compress_shell_output(text)
        assert "alpha" in stats.compressed_text
        assert "beta" in stats.compressed_text
        assert "gamma" in stats.compressed_text
        assert "[x" not in stats.compressed_text


class TestFoldListings:
    def test_collapses_long_directory_listing(self) -> None:
        entries = [f"file_{i:03d}.txt" for i in range(40)]
        text = "\n".join(entries)
        stats = compress_shell_output(text)
        assert "[...dir listing:" in stats.compressed_text
        # First three entries kept.
        assert "file_000.txt" in stats.compressed_text
        assert "file_002.txt" in stats.compressed_text
        # Some interior entry collapsed away.
        assert "file_020.txt" not in stats.compressed_text

    def test_threshold_respected(self) -> None:
        # Below threshold should not fold.
        text = "\n".join(f"file_{i}.txt" for i in range(5))
        stats = compress_shell_output(text)
        assert "[...dir listing" not in stats.compressed_text
        assert "file_4.txt" in stats.compressed_text


class TestFoldStackTraces:
    def test_collapses_repeated_stack_frames(self) -> None:
        frames = [f"  at fn{i} (file.js:{i}:1)" for i in range(10)]
        text = "Error: boom\n" + "\n".join(frames)
        stats = compress_shell_output(text)
        assert "[...stack trace:" in stats.compressed_text


class TestBlankRunCollapse:
    def test_three_or_more_blanks_collapse(self) -> None:
        text = "alpha\n\n\n\n\nbeta"
        stats = compress_shell_output(text)
        # Single blank line between alpha and beta.
        assert stats.compressed_text.count("\n\n") <= 1


class TestTruncation:
    def test_truncates_at_max_chars(self) -> None:
        big = "x" * 20_000
        opts = ShellCompressOpts(max_chars=1_000)
        stats = compress_shell_output(big, opts)
        assert len(stats.compressed_text) <= 1_100  # 1000 + TRUNCATED tail
        assert "[TRUNCATED]" in stats.compressed_text


class TestCompressionRatio:
    def test_reduces_bytes_on_typical_input(self) -> None:
        # Synthetic noisy input: blanks + repeats + listing.
        entries = [f"\x1b[31mfile_{i}.txt\x1b[0m" for i in range(50)]
        text = "\n\n\n".join(entries + entries[:5])
        stats = compress_shell_output(text)
        assert stats.compressed_bytes < stats.original_bytes
        assert 0.0 < stats.ratio < 1.0

    def test_ratio_field_populated(self) -> None:
        stats = compress_shell_output("a\nb\nc")
        assert isinstance(stats.ratio, float)
        assert stats.ratio > 0
