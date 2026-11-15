"""The compression provider on the current Claude generation.

Three things are pinned here:

* the default model and the pricing table follow the current generation,
  and the previous-generation rows stay for vaults configured with them;
* a response is read by block ``type``, not by position: on current models
  thinking is on by default, so a response can begin with a ``thinking``
  block and "the first block is the text" no longer holds;
* a declined or truncated call is an error, never an empty summary.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from mneme_core.compression.llm import (
    DEFAULT_MODEL,
    DEFAULT_PRICING_PER_MTOK,
    AnthropicProvider,
    LlmCallSpec,
    LlmProviderError,
    cost_for_usage,
)


def _block(kind: str, **fields: Any) -> Any:
    return types.SimpleNamespace(type=kind, **fields)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: list[Any],
    stop_reason: str = "end_turn",
    captured: dict[str, Any] | None = None,
) -> None:
    stub = types.ModuleType("anthropic")

    class _ClientStub:
        def __init__(self, **_kw: object) -> None:
            self.messages = types.SimpleNamespace(create=self._create)

        def _create(self, **kwargs: Any) -> Any:
            if captured is not None:
                captured.update(kwargs)
            return types.SimpleNamespace(
                content=content,
                stop_reason=stop_reason,
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    stub.Anthropic = _ClientStub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", stub)


class TestCurrentGeneration:
    def test_default_model_is_the_current_sonnet(self) -> None:
        assert DEFAULT_MODEL == "claude-sonnet-5-5"
        assert DEFAULT_MODEL in DEFAULT_PRICING_PER_MTOK

    @pytest.mark.parametrize(
        ("model", "rate_in", "rate_out"),
        [
            ("claude-sonnet-5-5", 2.0, 10.0),
            ("claude-opus-5-5", 4.0, 20.0),
            ("claude-haiku-5-5", 0.1, 0.5),
        ],
    )
    def test_current_generation_rates(
        self, model: str, rate_in: float, rate_out: float
    ) -> None:
        cost = cost_for_usage(tokens_in=1_000_000, tokens_out=1_000_000, model=model)
        assert cost == pytest.approx(rate_in + rate_out)

    @pytest.mark.parametrize(
        ("model", "rate_in", "rate_out"),
        [
            ("claude-sonnet-4-5", 3.0, 15.0),
            ("claude-opus-4-7", 5.0, 25.0),
            ("claude-haiku-4-5-20251001", 1.0, 5.0),
        ],
    )
    def test_previous_generation_rows_stay_at_the_published_rates(
        self, model: str, rate_in: float, rate_out: float
    ) -> None:
        assert DEFAULT_PRICING_PER_MTOK[model] == {"input": rate_in, "output": rate_out}

    def test_no_listed_model_is_cheaper_in_the_table_than_unknown(self) -> None:
        # The unknown-model fallback must stay the most expensive row, so a
        # misconfigured vault over-estimates its spend and never under-bills.
        unknown = cost_for_usage(tokens_in=1_000_000, tokens_out=1_000_000, model="x")
        for model in DEFAULT_PRICING_PER_MTOK:
            listed = cost_for_usage(tokens_in=1_000_000, tokens_out=1_000_000, model=model)
            assert listed <= unknown


class TestResponseIsReadByBlockType:
    def test_thinking_block_first_does_not_hide_the_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            content=[_block("thinking", thinking=""), _block("text", text="summary")],
        )
        result = AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))
        assert result.text == "summary"

    def test_text_blocks_are_joined_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(
            monkeypatch,
            content=[
                _block("text", text="first "),
                _block("thinking", thinking=""),
                _block("text", text="second"),
            ],
        )
        result = AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))
        assert result.text == "first second"

    def test_a_non_text_block_with_a_text_attribute_is_not_summary_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lookalike: the block carries ``text`` but is not a text block.
        _install(
            monkeypatch,
            content=[_block("tool_use", text="not a summary"), _block("text", text="real")],
        )
        result = AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))
        assert result.text == "real"


class TestDeclinedOrTruncatedIsAnError:
    def test_refusal_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, content=[], stop_reason="refusal")
        with pytest.raises(LlmProviderError, match="refusal"):
            AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))

    def test_truncated_before_any_text_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(
            monkeypatch,
            content=[_block("thinking", thinking="")],
            stop_reason="max_tokens",
        )
        with pytest.raises(LlmProviderError, match="max_tokens"):
            AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))

    def test_truncated_with_text_still_returns_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Positive control for the arm above: a partial summary is still returned.
        _install(
            monkeypatch,
            content=[_block("text", text="partial")],
            stop_reason="max_tokens",
        )
        result = AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))
        assert result.text == "partial"


class TestOutputBudget:
    def test_default_max_tokens_leaves_room_for_thinking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}
        _install(monkeypatch, content=[_block("text", text="ok")], captured=captured)
        AnthropicProvider().compress(LlmCallSpec(system="s", payload="p"))
        assert captured["max_tokens"] >= 16_000
        assert captured["model"] == DEFAULT_MODEL

    def test_pipeline_config_default_is_the_provider_default(self) -> None:
        # The pipeline passes its own configured budget to the provider, so a
        # smaller default there would undo the provider's default.
        from mneme_core.compression.config import CompressionConfig

        spec_default = LlmCallSpec(system="s", payload="p").max_tokens
        assert CompressionConfig().max_tokens == spec_default

    def test_an_explicit_config_budget_is_kept(self) -> None:
        from mneme_core.compression.config import CompressionConfig

        assert CompressionConfig(max_tokens=8000).max_tokens == 8000
