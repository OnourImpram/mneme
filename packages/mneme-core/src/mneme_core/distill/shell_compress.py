"""Compress shell tool output before storage and context injection.

The bash-output tax is invisible but real: a typical ``ls -la`` of a
node_modules directory is 4 KB. A failing test run is 30 KB. Most of
that volume is whitespace, ANSI codes, and repetition. ``compress_shell_output``
strips the volume while preserving the substantive signal.

The output of this function is the canonical form mneme stores in
staging JSONL and injects into context. Re-running the function on
already-compressed input is a near-no-op (idempotent under the
documented constraints).

Steps applied in order:

1. **ANSI escape stripping** (``\\x1b[...m`` color and cursor codes).
2. **Trailing whitespace trim** on every line.
3. **Adjacent-line deduplication** — consecutive identical lines
   collapse to one with a ``[x N more]`` suffix.
4. **Directory-listing fold** — runs of more than
   ``fold_listings_threshold`` lines that all match the
   directory-entry shape collapse with a fold marker.
5. **Stack-trace deduplication** — runs of more than 3 lines
   starting with ``  at `` collapse with a fold marker.
6. **Whitespace-run collapse** — three or more consecutive blank
   lines become a single blank line.
7. **Max-chars truncation** — the result is truncated at
   ``max_chars`` and a single ``[TRUNCATED]`` line is appended.

The stats record reports the size delta in bytes and the resulting
ratio, so callers can decide whether the compression was worth the
hook latency on a given event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 8_000
DEFAULT_FOLD_LISTINGS_THRESHOLD = 20
DEFAULT_FOLD_STACK_THRESHOLD = 3

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Loose directory-entry detector. Matches `ls -la`-ish lines:
# drwxr-xr-x  3 user group  4096 May 19 10:00 name
# and plain `ls` lines like `name.ext`.
_LISTING_RE = re.compile(
    r"^(?:[dlrwx\-]{10}\s+\S+\s+\S+\s+\S+\s+\d+\s+\S+\s+\d+\s+\S+\s+.+|"
    r"[A-Za-z0-9._\-]+\.\w{1,8})$"
)
_STACK_LINE_RE = re.compile(r"^\s{2,}at\s+")


@dataclass
class ShellCompressOpts:
    """Per-call knobs. Defaults match the v1.0 target reduction."""

    max_chars: int = DEFAULT_MAX_CHARS
    strip_ansi: bool = True
    dedupe_adjacent: bool = True
    fold_listings: bool = True
    fold_listings_threshold: int = DEFAULT_FOLD_LISTINGS_THRESHOLD
    fold_stack_traces: bool = True
    fold_stack_threshold: int = DEFAULT_FOLD_STACK_THRESHOLD
    collapse_blank_runs: bool = True


@dataclass
class CompressionStats:
    """Outcome record. ``ratio == 1.0`` means the input was already minimal."""

    original_bytes: int
    compressed_bytes: int
    ratio: float
    compressed_text: str


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _trim_lines(lines: list[str]) -> list[str]:
    return [ln.rstrip() for ln in lines]


def _dedupe_adjacent(lines: list[str]) -> list[str]:
    out: list[str] = []
    run_value: str | None = None
    run_count = 0
    for ln in lines:
        if ln == run_value:
            run_count += 1
            continue
        if run_value is not None and run_count > 1:
            out.append(f"{run_value} [x{run_count}]")
        elif run_value is not None:
            out.append(run_value)
        run_value = ln
        run_count = 1
    if run_value is not None and run_count > 1:
        out.append(f"{run_value} [x{run_count}]")
    elif run_value is not None:
        out.append(run_value)
    return out


def _fold_run(
    lines: list[str], predicate: re.Pattern[str], threshold: int, label: str
) -> list[str]:
    out: list[str] = []
    buffer: list[str] = []
    for ln in lines:
        if predicate.match(ln):
            buffer.append(ln)
            continue
        if buffer:
            if len(buffer) > threshold:
                kept = buffer[:3]
                out.extend(kept)
                out.append(f"[...{label}: {len(buffer) - 3} more entries]")
            else:
                out.extend(buffer)
            buffer = []
        out.append(ln)
    if buffer:
        if len(buffer) > threshold:
            kept = buffer[:3]
            out.extend(kept)
            out.append(f"[...{label}: {len(buffer) - 3} more entries]")
        else:
            out.extend(buffer)
    return out


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(ln)
    return out


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    return head.rstrip() + "\n[TRUNCATED]"


def compress_shell_output(
    text: str, opts: ShellCompressOpts | None = None
) -> CompressionStats:
    """Compress shell output. Returns a stats record with the new text.

    Empty input returns a zero-size record; the function never raises.
    """
    o = opts or ShellCompressOpts()
    original = text or ""
    original_bytes = len(original.encode("utf-8"))
    if not original:
        return CompressionStats(
            original_bytes=0,
            compressed_bytes=0,
            ratio=1.0,
            compressed_text="",
        )

    current = original
    if o.strip_ansi:
        current = _strip_ansi(current)

    lines = current.splitlines()
    lines = _trim_lines(lines)

    if o.dedupe_adjacent:
        lines = _dedupe_adjacent(lines)

    if o.fold_listings:
        lines = _fold_run(
            lines, _LISTING_RE, o.fold_listings_threshold, label="dir listing"
        )

    if o.fold_stack_traces:
        lines = _fold_run(
            lines, _STACK_LINE_RE, o.fold_stack_threshold, label="stack trace"
        )

    if o.collapse_blank_runs:
        lines = _collapse_blank_runs(lines)

    compressed = "\n".join(lines)
    compressed = _truncate(compressed, o.max_chars)
    compressed_bytes = len(compressed.encode("utf-8"))

    ratio = compressed_bytes / original_bytes if original_bytes > 0 else 1.0
    return CompressionStats(
        original_bytes=original_bytes,
        compressed_bytes=compressed_bytes,
        ratio=round(ratio, 4),
        compressed_text=compressed,
    )
