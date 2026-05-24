"""Tests for the three-level injection format selector and renderer."""

from __future__ import annotations

from mneme_core.distill.compressed_format import (
    InjectionFormat,
    InjectionInput,
    render_injection,
    select_format,
)


class TestSelectFormat:
    def test_first_injection_low_pressure_is_full(self) -> None:
        assert (
            select_format(has_been_injected=False, context_pressure=0.2)
            is InjectionFormat.FULL
        )

    def test_first_injection_high_pressure_is_keypoints(self) -> None:
        assert (
            select_format(has_been_injected=False, context_pressure=0.9)
            is InjectionFormat.KEYPOINTS
        )

    def test_reinjection_low_pressure_is_keypoints(self) -> None:
        assert (
            select_format(has_been_injected=True, context_pressure=0.2)
            is InjectionFormat.KEYPOINTS
        )

    def test_reinjection_high_pressure_is_ref(self) -> None:
        assert (
            select_format(has_been_injected=True, context_pressure=0.95)
            is InjectionFormat.REF
        )

    def test_pressure_clamped_to_unit_interval(self) -> None:
        # Out-of-range pressure values are clamped, not raise.
        assert (
            select_format(has_been_injected=False, context_pressure=2.0)
            is InjectionFormat.KEYPOINTS
        )
        assert (
            select_format(has_been_injected=False, context_pressure=-1.0)
            is InjectionFormat.FULL
        )


def _doc() -> InjectionInput:
    return InjectionInput(
        path="reference/x.md",
        title="Mneme overview",
        body="One paragraph of body content.",
        key_points=["Point A", "Point B"],
    )


class TestRender:
    def test_full_includes_body(self) -> None:
        out = render_injection(_doc(), InjectionFormat.FULL)
        assert "## Mneme overview" in out
        assert "One paragraph of body content." in out

    def test_keypoints_lists_bullets_and_path(self) -> None:
        out = render_injection(_doc(), InjectionFormat.KEYPOINTS)
        assert "- Point A" in out
        assert "- Point B" in out
        assert "reference/x.md" in out

    def test_keypoints_without_keypoints_falls_back_to_path(self) -> None:
        doc = InjectionInput(path="ref/y.md", title="Y", body="b", key_points=[])
        out = render_injection(doc, InjectionFormat.KEYPOINTS)
        assert "ref/y.md" in out
        assert "## Y" in out

    def test_ref_is_single_line(self) -> None:
        out = render_injection(_doc(), InjectionFormat.REF)
        assert out.strip() == "see vault://reference/x.md"

    def test_full_without_body_only_emits_heading(self) -> None:
        doc = InjectionInput(path="r/z.md", title="Z", body="", key_points=[])
        out = render_injection(doc, InjectionFormat.FULL)
        assert "## Z" in out
        assert "Point" not in out
