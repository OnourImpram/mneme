"""Tests for the Phase F LLM provider abstraction."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from mneme_core.compression.llm import (
    DEFAULT_MODEL,
    DEFAULT_PRICING_PER_MTOK,
    AnthropicProvider,
    CompressionResult,
    LlmCallSpec,
    LlmProvider,
    LlmProviderError,
    cost_for_usage,
)


class TestCostForUsage:
    def test_known_model_uses_table_rates(self) -> None:
        cost = cost_for_usage(
            tokens_in=1_000_000,
            tokens_out=0,
            model="claude-sonnet-4-5",
        )
        assert cost == pytest.approx(3.0)

    def test_output_dominates_when_model_unknown(self) -> None:
        # Falls back to the most expensive entry in the table so the
        # operator over-estimates rather than under-bills themselves.
        cost = cost_for_usage(
            tokens_in=1_000_000,
            tokens_out=1_000_000,
            model="unknown-model-x",
        )
        # Opus output is the highest output rate, so unknown model
        # should use opus pricing.
        expected = (
            DEFAULT_PRICING_PER_MTOK["claude-opus-4-7"]["input"]
            + DEFAULT_PRICING_PER_MTOK["claude-opus-4-7"]["output"]
        )
        assert cost == pytest.approx(expected)

    def test_explicit_pricing_overrides_default(self) -> None:
        cost = cost_for_usage(
            tokens_in=1_000_000,
            tokens_out=0,
            model="my-cheap-model",
            pricing_per_mtok={"my-cheap-model": {"input": 0.5, "output": 1.0}},
        )
        assert cost == pytest.approx(0.5)


class TestProtocolShape:
    def test_anthropic_provider_satisfies_protocol(self) -> None:
        provider = AnthropicProvider()
        assert isinstance(provider, LlmProvider)


class TestAnthropicProviderImportPath:
    def test_raises_provider_error_when_sdk_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the import to fail.
        monkeypatch.setitem(sys.modules, "anthropic", None)
        provider = AnthropicProvider()
        with pytest.raises(LlmProviderError, match="anthropic SDK not installed"):
            provider.compress(
                LlmCallSpec(system="s", payload="p", model=DEFAULT_MODEL)
            )

    def test_raises_provider_error_when_call_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = types.ModuleType("anthropic")

        class _ClientStub:
            def __init__(self, **_kw: object) -> None:
                self.messages = types.SimpleNamespace(
                    create=self._boom,
                )

            def _boom(self, **_kw: object) -> None:
                raise RuntimeError("network down")

        stub.Anthropic = _ClientStub  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "anthropic", stub)

        provider = AnthropicProvider()
        with pytest.raises(LlmProviderError, match="network down"):
            provider.compress(
                LlmCallSpec(system="s", payload="p", model=DEFAULT_MODEL)
            )

    def test_success_path_returns_compression_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = types.ModuleType("anthropic")

        @dataclass
        class _Content:
            text: str

        @dataclass
        class _Usage:
            input_tokens: int
            output_tokens: int

        @dataclass
        class _Response:
            content: list[_Content]
            usage: _Usage

        class _ClientStub:
            def __init__(self, **_kw: object) -> None:
                self.messages = types.SimpleNamespace(
                    create=self._ok,
                )

            def _ok(self, **_kw: object) -> _Response:
                return _Response(
                    content=[_Content(text="compressed markdown")],
                    usage=_Usage(input_tokens=120, output_tokens=42),
                )

        stub.Anthropic = _ClientStub  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "anthropic", stub)

        provider = AnthropicProvider(api_key="sk-test-not-real-redacted")
        result = provider.compress(
            LlmCallSpec(system="s", payload="p", model="claude-sonnet-4-5")
        )
        assert isinstance(result, CompressionResult)
        assert result.text == "compressed markdown"
        assert result.tokens_in == 120
        assert result.tokens_out == 42
        assert result.model == "claude-sonnet-4-5"

    def test_redacts_spec_at_anthropic_client_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}
        stub = types.ModuleType("anthropic")

        class _ClientStub:
            def __init__(self, **_kw: object) -> None:
                self.messages = types.SimpleNamespace(create=self._ok)

            def _ok(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(text="compressed")],
                    usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
                )

        stub.Anthropic = _ClientStub  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "anthropic", stub)

        AnthropicProvider().compress(
            LlmCallSpec(
                system="rules <private>SYSTEM_SECRET</private>",
                payload="data <private>PAYLOAD_SECRET</private>",
            )
        )

        outbound = str(captured)
        assert "SYSTEM_SECRET" not in outbound
        assert "PAYLOAD_SECRET" not in outbound
        assert outbound.count("[REDACTED]") == 2

    def test_robust_to_missing_usage_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = types.ModuleType("anthropic")

        class _ClientStub:
            def __init__(self, **_kw: object) -> None:
                self.messages = types.SimpleNamespace(
                    create=self._ok,
                )

            def _ok(self, **_kw: object) -> Any:
                return types.SimpleNamespace(content=[], usage=None)

        stub.Anthropic = _ClientStub  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "anthropic", stub)
        provider = AnthropicProvider()
        result = provider.compress(
            LlmCallSpec(system="s", payload="p", model=DEFAULT_MODEL)
        )
        assert result.text == ""
        assert result.tokens_in == 0
        assert result.tokens_out == 0
