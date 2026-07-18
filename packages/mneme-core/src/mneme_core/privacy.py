"""Canonical ``<private>`` redaction module (constraint C4).

All stores in mneme-core that write user-provided content to disk must
call :func:`redact` before writing. This single module is the only
authorised implementation; the per-module copies that previously lived
in ``compression/staging.py`` and ``kg/episode_stage.py`` have been
removed.

Semantics guaranteed (and mirrored by ``packages/mneme-mcp/src/privacy.ts``):

* **Case-insensitive** — ``<PRIVATE>``, ``<Private>``, and ``<private>``
  are all matched.
* **Attribute-tolerant** — ``<private reason="x">`` and
  ``<private  >`` (inner whitespace) are matched.
* **Fail-closed** — an opening tag with no corresponding closing tag
  redacts from the tag to the end of the text, so a truncated secret
  never leaks its visible head.
* **Single replacement token** — every match is replaced with the
  literal string ``[REDACTED]``.

The module also exposes :func:`redact_value`, a recursive walker for
dicts and lists whose call sites previously duplicated the same
pattern in ``staging._redact_value``.
"""

from __future__ import annotations

import re
from typing import Any

# Primary pattern: attribute-tolerant, case-insensitive, DOTALL so the
# body may contain newlines.  The opening tag allows optional attributes
# and inner whitespace; the closing tag is plain.
_PRIVATE_RE = re.compile(
    r"<\s*private(?:\s[^>]*)?\s*>.*?</\s*private\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Fail-closed sentinel: match only the opening tag (no trailing body).
# After the primary pass replaces all closed pairs, any remaining opener
# triggers a slice to end-of-text in :func:`redact`. Matching just the
# tag instead of ``.*`` keeps redaction linear on the PostToolUse hot
# path, with no backtracking surface on adversarial input.
_PRIVATE_OPEN_RE = re.compile(
    r"<\s*private(?:\s[^>]*)?\s*>",
    re.IGNORECASE,
)

_REPLACEMENT = "[REDACTED]"


def redact(text: str | None) -> str:
    """Replace every ``<private>...</private>`` section with ``[REDACTED]``.

    Semantics:

    * ``None`` returns ``""`` (mirrors the TypeScript counterpart that
      returns ``""`` for null input).
    * Closed pairs are replaced first (case-insensitive, attribute-tolerant).
    * Any remaining unbalanced opening tag causes everything from the
      tag to end-of-text to be replaced (fail-closed).

    Returns the redacted string. The input is not mutated.
    """
    if text is None:
        return ""
    result = _PRIVATE_RE.sub(_REPLACEMENT, text)
    match = _PRIVATE_OPEN_RE.search(result)
    if match is not None:
        result = result[: match.start()] + _REPLACEMENT
    return result


def redact_mapping_items(value: dict[Any, Any]) -> list[tuple[Any, Any, Any]]:
    """Return ordered ``(original_key, safe_key, value)`` mapping items.

    String keys use the same canonical redactor as string values. Unchanged
    keys are reserved before private keys are projected, so a redacted key
    cannot overwrite an existing visible key. Further collisions receive a
    deterministic ordinal suffix without deriving identifiers from the secret.
    """
    projected = [
        (key, redact(key) if isinstance(key, str) else key, item)
        for key, item in value.items()
    ]
    occupied: set[Any] = {
        safe_key for original_key, safe_key, _item in projected if safe_key == original_key
    }
    result: list[tuple[Any, Any, Any]] = []
    for original_key, safe_key, item in projected:
        if safe_key != original_key:
            base_key = safe_key
            suffix = 2
            while safe_key in occupied:
                safe_key = f"{base_key}#{suffix}"
                suffix += 1
            occupied.add(safe_key)
        result.append((original_key, safe_key, item))
    return result


def redact_value(value: Any) -> Any:
    """Recursively redact ``<private>`` content from any nested value.

    * ``str`` — passed through :func:`redact`.
    * ``dict`` — string keys and every value are redacted recursively.
    * ``list`` — every element is redacted recursively.
    * Everything else — returned unchanged.

    Returns a new object; the input is not mutated.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            safe_key: redact_value(item)
            for _original_key, safe_key, item in redact_mapping_items(value)
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value
