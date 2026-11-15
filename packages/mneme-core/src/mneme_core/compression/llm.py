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

from ..privacy import redact

DEFAULT_MODEL = "claude-sonnet-5-5"

# Pricing per million tokens (USD). Operators override via
# ``LlmCallSpec.pricing_per_mtok`` when their model is not listed.
# Rates are the base input and output rates on the vendor pricing page,
# read on 2026-11-15; cache and batch rates are not modelled here.
DEFAULT_PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    # Current generation.
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
    # Rate for prompts up to 100,000 tokens (0.5 / 2.5 above that). One
    # compression call sends at most ``DEFAULT_MAX_PAYLOAD_BYTES``.
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5},
    # Previous generation, kept for vaults configured with these ids.
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}

# Output budget of one call. On current models thinking is on by default
# and counts toward ``max_tokens``, so the budget has to cover thinking
# plus the summary; a budget sized for the summary alone can be spent
# before any text is written.
DEFAULT_MAX_TOKENS = 16_000

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
    max_tokens: int = DEFAULT_MAX_TOKENS
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


def response_text(response: Any) -> str:
    """Concatenate the text blocks of a Messages API response, in order.

    Blocks are read by ``type``, not by position: a response can begin
    with a ``thinking`` block, whose text is empty by default. A block
    that carries no ``type`` is treated as text when it has ``text``.
    """
    parts: list[str] = []
    try:
        for block in response.content or []:
            if getattr(block, "type", "text") != "text":
                continue
            text = getattr(block, "text", "")
            if isinstance(text, str):
                parts.append(text)
    except (AttributeError, TypeError):
        return ""
    return "".join(parts)


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
                # Keep this final guard even though the built-in pipeline also
                # redacts. AnthropicProvider is public and can be called by a
                # different orchestrator with an untrusted LlmCallSpec.
                system=redact(spec.system),
                messages=[{"role": "user", "content": redact(spec.payload)}],
            )
        except Exception as exc:  # noqa: BLE001 - third-party surface
            raise LlmProviderError(f"Anthropic call failed: {exc}") from exc

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            # A declined request carries no summary; an empty string here
            # would be stored as if the compression had succeeded.
            raise LlmProviderError(
                "Anthropic declined the request (stop_reason: refusal)."
            )
        text = response_text(response)
        if stop_reason == "max_tokens" and not text:
            raise LlmProviderError(
                "Anthropic stopped at max_tokens before writing any text "
                f"(max_tokens={spec.max_tokens}); raise max_tokens."
            )
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
