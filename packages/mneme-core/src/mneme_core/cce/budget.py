"""Token-budget estimation helpers for CCE context-fill tracking.

Zero-LLM, zero-network. Both functions are pure (no side effects) and
cheap enough to call on the PostToolUse hot path.

The ``estimate_tokens`` heuristic (len // 4) is intentionally rough — the
true figure depends on the model's tokeniser. The goal is a fast proxy
that avoids triggering or blocking a checkpoint by more than ~25 %, which
is sufficient for the 65 % threshold signal.
"""

from __future__ import annotations

from pathlib import Path

# Mirror session_summary's cap so we never block on a huge transcript.
_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024


def estimate_tokens(text: str) -> int:
    """Cheap heuristic: one token per four characters, minimum 1.

    Args:
        text: any string whose token count should be estimated.

    Returns:
        Estimated token count, always >= 1.
    """
    return max(1, len(text) // 4)


def estimate_context_fill(
    transcript_path: Path,
    context_window_tokens: int,
) -> float:
    """Estimate how full the context window is, as a fraction in [0.0, 1.0].

    Reads up to ``_MAX_TRANSCRIPT_BYTES`` from the head of the transcript
    JSONL, sums the estimated token counts of all message text content, and
    divides by ``context_window_tokens``.

    Tolerates missing or corrupt transcripts by returning 0.0.

    Args:
        transcript_path: path to the Claude Code session transcript JSONL.
        context_window_tokens: operator-configured total context window size.

    Returns:
        Fill fraction in [0.0, 1.0].
    """
    if context_window_tokens <= 0:
        return 0.0

    try:
        if not transcript_path.is_file():
            return 0.0
        blob = transcript_path.read_bytes()[:_MAX_TRANSCRIPT_BYTES]
    except OSError:
        return 0.0

    import json

    total_tokens = 0
    for raw_line in blob.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        # Extract text from message.content (list of typed blocks or plain str).
        message = rec.get("message")
        content: object = None
        if isinstance(message, dict):
            content = message.get("content")
        elif "content" in rec:
            content = rec["content"]
        if isinstance(content, str):
            total_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        total_tokens += estimate_tokens(text)

    fill = total_tokens / context_window_tokens
    return max(0.0, min(1.0, fill))
