"""Unit tests for _extract_keypoints in fts5/indexer.py.

RED phase: these tests are written before the implementation exists.
They cover heading exclusion, max_points cap, empty body, and paragraph splitting.
"""

from __future__ import annotations

# Intentional private-API import: keypoint extraction is tested in isolation.
from mneme_core.fts5.indexer import _extract_keypoints


class TestExtractKeypoints:
    def test_empty_body_returns_empty_list(self) -> None:
        assert _extract_keypoints("") == []

    def test_whitespace_only_body_returns_empty_list(self) -> None:
        assert _extract_keypoints("   \n\n  \n") == []

    def test_single_paragraph_returns_first_sentence(self) -> None:
        body = "This is the first sentence. And this is the second."
        result = _extract_keypoints(body)
        assert result == ["This is the first sentence"]

    def test_heading_lines_are_excluded(self) -> None:
        body = "# Main heading\n\nThis is a paragraph."
        result = _extract_keypoints(body)
        assert result == ["This is a paragraph"]
        assert not any(kp.startswith("#") for kp in result)

    def test_heading_with_two_hashes_excluded(self) -> None:
        body = "## Section heading\n\nContent paragraph here."
        result = _extract_keypoints(body)
        assert result == ["Content paragraph here"]

    def test_heading_followed_by_only_heading_paragraphs(self) -> None:
        body = "# Heading one\n\n## Heading two\n\n### Heading three"
        result = _extract_keypoints(body)
        assert result == []

    def test_max_points_cap_defaults_to_5(self) -> None:
        paragraphs = "\n\n".join(
            f"Paragraph {i} with some content here." for i in range(10)
        )
        result = _extract_keypoints(paragraphs)
        assert len(result) == 5

    def test_max_points_cap_custom_value(self) -> None:
        paragraphs = "\n\n".join(
            f"Paragraph {i} content." for i in range(8)
        )
        result = _extract_keypoints(paragraphs, max_points=3)
        assert len(result) == 3

    def test_max_points_1(self) -> None:
        paragraphs = "\n\n".join(f"Item {i}." for i in range(5))
        result = _extract_keypoints(paragraphs, max_points=1)
        assert len(result) == 1
        assert result[0] == "Item 0"

    def test_paragraph_split_yields_multiple_keypoints(self) -> None:
        body = "First paragraph. More text.\n\nSecond paragraph. More text.\n\nThird paragraph."
        result = _extract_keypoints(body, max_points=5)
        assert len(result) == 3
        assert result[0] == "First paragraph"
        assert result[1] == "Second paragraph"
        assert result[2] == "Third paragraph"

    def test_long_first_sentence_is_truncated_to_80_chars(self) -> None:
        long_sentence = "A" * 100 + ". Short follow up."
        result = _extract_keypoints(long_sentence)
        assert len(result) == 1
        assert len(result[0]) <= 80

    def test_paragraph_without_period_uses_full_text_up_to_80(self) -> None:
        # No period: split(".")[0] returns full string, truncated at 80
        body = "B" * 90
        result = _extract_keypoints(body)
        assert len(result) == 1
        assert len(result[0]) == 80

    def test_empty_paragraph_after_split_is_skipped(self) -> None:
        # Three newlines create an "empty" paragraph between two real ones
        body = "First real paragraph.\n\n\n\nSecond real paragraph."
        result = _extract_keypoints(body)
        # Empty paragraphs produce empty stripped first sentences -> skipped
        assert all(kp for kp in result)
        assert "First real paragraph" in result
        assert "Second real paragraph" in result

    def test_heading_indented_with_space_is_excluded(self) -> None:
        # First non-space char is #
        body = "  # Indented heading\n\nReal content."
        result = _extract_keypoints(body)
        assert result == ["Real content"]

    def test_returns_list_type(self) -> None:
        result = _extract_keypoints("Some content.")
        assert isinstance(result, list)

    def test_strips_whitespace_from_keypoints(self) -> None:
        body = "  Sentence with leading space. More text."
        result = _extract_keypoints(body)
        assert result[0] == result[0].strip()
