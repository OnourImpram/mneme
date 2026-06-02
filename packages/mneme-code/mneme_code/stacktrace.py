"""Deterministic CPython traceback parser with redact-before-store invariant.

Parses the standard CPython traceback format::

    Traceback (most recent call last):
      File "PATH", line N, in FUNC
        code line
    ExcType: message

Redaction invariant (C4): ``mneme_core.privacy.redact`` is applied to
``exc_message``, every ``code_context``, and every ``file_path`` *before*
the dataclasses are constructed.  No raw user content ever reaches a field.

``parse_traceback`` returns ``None`` for any input that is not a recognisable
CPython traceback and never raises.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass

from mneme_core.privacy import redact

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Opening line of a standard CPython traceback.
_TRACEBACK_HEADER = re.compile(r"^Traceback \(most recent call last\):\s*$", re.MULTILINE)

# Each frame header: '  File "PATH", line N, in FUNC'
# Captures: path (group 1), line number (group 2), function name (group 3).
_FRAME_HEADER = re.compile(
    r'^\s{2}File "([^"]+)", line (\d+), in (.+?)\s*$',
    re.MULTILINE,
)

# Trailing exception line: 'ExcType: message' or just 'ExcType' (no message).
# Dotted types are supported (e.g. 'pkg.mod.Error').
_EXC_LINE = re.compile(
    r"^([\w][\w.]*(?:[\w]+)?)(?::\s*(.*))?\s*$",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """A single stack frame extracted from a CPython traceback.

    All string fields have already been passed through ``redact()``
    before construction; callers must not bypass this by constructing
    ``Frame`` directly with raw user content.
    """

    file_path: str
    line: int
    function: str
    code_context: str | None


@dataclass(frozen=True)
class ParsedTraceback:
    """The parsed, redacted representation of a CPython traceback.

    Attributes:
        exc_type    Exception class name (may be dotted, e.g. ``pkg.Error``).
        exc_message Redacted exception message (empty string if none).
        frames      Tuple of :class:`Frame` objects, innermost last.
    """

    exc_type: str
    exc_message: str
    frames: tuple[Frame, ...]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_traceback(text: str) -> ParsedTraceback | None:
    """Parse a standard CPython traceback text into a :class:`ParsedTraceback`.

    Returns ``None`` if *text* is not a recognisable CPython traceback.
    Never raises.

    Redaction invariant: ``redact()`` is applied to ``exc_message``,
    every ``code_context``, and every ``file_path`` before any dataclass
    is constructed.

    Args:
        text: Raw traceback text (may include leading/trailing whitespace
              or surrounding log lines; the parser searches for the
              ``Traceback (most recent call last):`` header).
    """
    try:
        return _parse_traceback_inner(text)
    except Exception:  # noqa: BLE001
        return None


def _parse_traceback_inner(text: str) -> ParsedTraceback | None:
    """Inner parser — may raise; wrapped by ``parse_traceback``."""
    # Must contain the standard header.
    header_match = _TRACEBACK_HEADER.search(text)
    if header_match is None:
        return None

    # Work only with text from the header onwards.
    body = text[header_match.start():]
    lines = body.splitlines()

    # Collect frame header positions.
    # Each frame header is '  File "PATH", line N, in FUNC'.
    # The optional next line (indented more) is the code context.
    frames: list[Frame] = []
    i = 1  # skip the 'Traceback ...' header line itself
    while i < len(lines):
        fh = _FRAME_HEADER.match(lines[i])
        if fh is None:
            i += 1
            continue

        raw_path = fh.group(1)
        raw_line = int(fh.group(2))
        raw_func = fh.group(3).strip()

        # Optional next line: code context (must be indented by >= 4 spaces).
        code_ctx: str | None = None
        if i + 1 < len(lines):
            candidate = lines[i + 1]
            # Code context lines are indented with at least 4 spaces and are
            # NOT themselves frame headers.
            if candidate.startswith("    ") and not _FRAME_HEADER.match(candidate):
                code_ctx = redact(candidate.strip()) or None
                i += 2
            else:
                i += 1
        else:
            i += 1

        frames.append(
            Frame(
                file_path=redact(raw_path),
                line=raw_line,
                function=raw_func,
                code_context=code_ctx,
            )
        )

    if not frames:
        return None

    # The exception line is the last non-blank, NON-INDENTED line of the body.
    # Code-context lines are indented (>= 4 spaces) and the real exception line
    # sits at column 0, so skipping indented lines stops a truncated traceback
    # (which ends in an indented code line like ``pass``) from being misread as
    # the exception. A bare Python keyword is also rejected — no exception class
    # is named after a keyword.
    exc_type = ""
    exc_message = ""
    for raw_line_text in reversed(lines):
        if raw_line_text[:1] in (" ", "\t"):
            continue
        stripped = raw_line_text.strip()
        if not stripped:
            continue
        if _FRAME_HEADER.match(raw_line_text):
            continue
        if stripped.startswith("Traceback (most recent call last)"):
            continue
        m = _EXC_LINE.match(stripped)
        if m and m.group(1) not in keyword.kwlist:
            exc_type = m.group(1)
            exc_message = redact(m.group(2) or "")
            break

    if not exc_type:
        return None

    return ParsedTraceback(
        exc_type=exc_type,
        exc_message=exc_message,
        frames=tuple(frames),
    )
