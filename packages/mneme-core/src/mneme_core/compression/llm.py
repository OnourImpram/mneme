"""LLM provider abstraction for the compression pipeline.

mneme v1.0 ships a single Anthropic Claude provider but exposes a
``Protocol`` so an OpenAI / Gemini / local-model provider can be
added in v1.1 without touching the pipeline orchestrator.

Defaults intentionally match the Claude Code surface: Anthropic API,
``ANTHROPIC_API_KEY`` from the environment when the operator has
not supplied one. The pipeline calls into this layer only after the
``compression_enabled = true`` config gate and the cost-cap ledger
check have both passed.

Pricing constants live here next to the provider. They are
operator-tunable knobs that vary per model and per provider, so we
co-locate them with the implementation rather than the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-4-5"

# Pricing per million tokens (USD). Operators override via
# ``LlmCallSpec.pricing_per_mtok`` when their model is not listed.
DEFAULT_PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0},
}

# Maximum size of the JSON payload we send to the provider in one call.
# Prevents accidental whole-vault sends; callers chunk above this.
DEFAULT_MAX_PAYLOAD_BYTES = 100_000


@dataclass(frozen=True)
class CompressionResult:
    """Provider-agnostic return of one compression call."""

    text: str
    tokens_in: int
    tokens_out: int
    model: str


@dataclass
class LlmCallSpec:
    """Bundle of knobs that vary per call. Passed to the provider."""

    system: str
    payload: str
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    pricing_per_mtok: dict[str, dict[str, float]] | None = None


@runtime_checkable
class LlmProvider(Protocol):
    """Minimal compression-call surface.

    Implementations must:

    * Be synchronous (the pipeline runs out-of-band; no asyncio).
    * Raise ``LlmProviderError`` (subclass of ``RuntimeError``) on
      transport, auth, or quota failures so the pipeline can render
      a stable error envelope.
    * Return token counts the caller can multiply against the
      provider's pricing table to compute USD spend.
    """

    def compress(self, spec: LlmCallSpec) -> CompressionResult:
        ...


class LlmProviderError(RuntimeError):
    """Raised by providers for transport, auth, or quota failures."""


def cost_for_usage(
    *, tokens_in: int, tokens_out: int, model: str,
    pricing_per_mtok: dict[str, dict[str, float]] | None = None,
) -> float:
    """USD cost from token counts and a pricing table.

    Falls back to ``DEFAULT_PRICING_PER_MTOK`` when no override is
    supplied. Unknown models default to the most expensive entry in
    the table so a misconfigured operator over-estimates rather than
    under-bills themselves.
    """
    table = pricing_per_mtok or DEFAULT_PRICING_PER_MTOK
    if model in table:
        rates = table[model]
    else:
        rates = max(table.values(), key=lambda r: r.get("output", 0))
    return (
        tokens_in * rates.get("input", 0) / 1_000_000
        + tokens_out * rates.get("output", 0) / 1_000_000
    )


class AnthropicProvider:
    """Anthropic Claude provider. Lazy-imports the SDK.

    Attributes:
        api_key: explicit key. When None, the SDK reads
            ``ANTHROPIC_API_KEY`` from the environment.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key

    def compress(self, spec: LlmCallSpec) -> CompressionResult:
        try:
            from anthropic import Anthropic  # noqa: WPS433
        except ImportError as exc:
            raise LlmProviderError(
                "anthropic SDK not installed. Install with mneme[compression] "
                "or mneme[full]."
            ) from exc

        client: Any = (
            Anthropic(api_key=self.api_key) if self.api_key else Anthropic()
        )
        try:
            response = client.messages.create(
                model=spec.model,
                max_tokens=spec.max_tokens,
                system=spec.system,
                messages=[{"role": "user", "content": spec.payload}],
            )
        except Exception as exc:  # noqa: BLE001 - third-party surface
            raise LlmProviderError(f"Anthropic call failed: {exc}") from exc

        text = ""
        try:
            content = response.content
            if content and len(content) > 0:
                first = content[0]
                text = getattr(first, "text", "") or ""
        except (AttributeError, IndexError, TypeError):
            text = ""
        try:
            tokens_in = int(response.usage.input_tokens)
            tokens_out = int(response.usage.output_tokens)
        except (AttributeError, TypeError, ValueError):
            tokens_in = 0
            tokens_out = 0
        return CompressionResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=spec.model,
        )
