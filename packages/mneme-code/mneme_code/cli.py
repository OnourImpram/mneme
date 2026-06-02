"""Command-line interface for mneme-code.

Subcommands
-----------
parse-trace [--input FILE] [--vault VAULT_ROOT] [--write] [--source LABEL]
    Read a CPython traceback from --input (or stdin), parse it, print a
    redacted structured summary to stdout.  With --write, write the
    failure_to_markdown output into
    ``<vault>/code-failures/<failure_id>.md`` via atomic_write_text.

No new dependencies: argparse only (stdlib).

Redaction invariant: all content is redacted by parse_traceback before
any output or write.  The CLI may call ``datetime.now(UTC)`` — the library
functions never do.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from mneme_code.failure import failure_from_traceback, failure_to_markdown
from mneme_code.stacktrace import parse_traceback


def _cmd_parse_trace(args: argparse.Namespace) -> int:
    """Implementation of the ``parse-trace`` subcommand.

    Returns an exit code (0 = success, 1 = not a traceback / error).
    """
    # Read input.
    input_path: str | None = getattr(args, "input", None)
    if input_path is not None:
        try:
            text = Path(input_path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"mneme-code: cannot read input file: {exc}", file=sys.stderr)
            return 1
    else:
        text = sys.stdin.read()

    # Parse (redaction happens inside parse_traceback).
    parsed = parse_traceback(text)
    if parsed is None:
        print("not a recognizable traceback", file=sys.stderr)
        return 1

    # Inject observed_at at the CLI boundary — the library stays pure.
    observed_at = datetime.now(UTC)
    source_label: str | None = getattr(args, "source", None)

    failure = failure_from_traceback(parsed, observed_at=observed_at, source_label=source_label)

    # Print redacted summary.
    print(f"failure_id:   {failure.failure_id}")
    print(f"exc_type:     {failure.exc_type}")
    print(f"exc_message:  {failure.exc_message}")
    print(f"frames:       {len(failure.frames)}")
    print(f"content_hash: {failure.content_hash}")
    if failure.source_label is not None:
        print(f"source_label: {failure.source_label}")
    for i, frame in enumerate(failure.frames):
        print(f"  [{i}] {frame.file_path}:{frame.line} in {frame.function}")

    # Optional vault write.
    write: bool = getattr(args, "write", False)
    vault_raw: str | None = getattr(args, "vault", None)
    if write:
        if vault_raw is None:
            print("mneme-code: --write requires --vault", file=sys.stderr)
            return 1
        vault_root = Path(vault_raw).resolve()
        out_dir = vault_root / "code-failures"
        out_path = out_dir / f"{failure.failure_id}.md"
        note = failure_to_markdown(failure)
        try:
            from mneme_core.vault.atomic_write import atomic_write_text

            out_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(out_path, note)
            print(f"written: {out_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"mneme-code: write failed: {exc}", file=sys.stderr)
            return 1

    return 0


def main() -> None:
    """Entry point for the mneme-code console script."""
    parser = argparse.ArgumentParser(
        prog="mneme-code",
        description="mneme-code: deterministic Python code-failure memory layer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # parse-trace subcommand
    pt = subparsers.add_parser(
        "parse-trace",
        help="Parse a CPython traceback and print a redacted summary.",
    )
    pt.add_argument(
        "--input",
        metavar="FILE",
        default=None,
        help="Path to a file containing the traceback text (default: stdin).",
    )
    pt.add_argument(
        "--vault",
        metavar="VAULT_ROOT",
        default=None,
        help="Vault root directory (required with --write).",
    )
    pt.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Write the failure note into <vault>/code-failures/<failure_id>.md.",
    )
    pt.add_argument(
        "--source",
        metavar="LABEL",
        default=None,
        help="Optional source label (e.g. 'ci-run-42').",
    )

    args = parser.parse_args()

    if args.command == "parse-trace":
        sys.exit(_cmd_parse_trace(args))
