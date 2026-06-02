"""``mneme-modes`` CLI: inspect and validate domain mode registries.

Three subcommands, all read-only and deterministic:

* ``mneme-modes list``      — print sorted mode names from the resolved registry.
* ``mneme-modes show NAME`` — print a human-readable summary for one mode.
* ``mneme-modes validate``  — validate the resolved registry, print "OK" or problems.

The ``--config`` option on every subcommand points at a ``modes.json`` file.
When omitted, :func:`~mneme_core.modes.resolve_modes` looks for
``./.mneme/modes.json`` relative to the current directory.

No network calls. No LLM. Deterministic output (sorted where order matters).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .modes import Mode, resolve_modes, validate_modes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(config: Path | None) -> dict[str, Mode]:
    """Return a resolved registry, propagating ValueError as a ClickException."""
    try:
        if config is not None:
            return resolve_modes(config_path=config)
        return resolve_modes(vault_root=Path("."))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _show_mode(mode: Mode) -> None:
    """Emit a human-readable summary for *mode* via click.echo."""
    click.echo(f"name:          {mode.name}")
    click.echo(f"language:      {mode.language}")
    click.echo(f"allowed_types: {sorted(mode.allowed_types)}")
    click.echo("write:")
    click.echo(f"  default_type:  {mode.write.default_type}")
    if mode.write.required_tags:
        click.echo(f"  required_tags: {list(mode.write.required_tags)}")
    else:
        click.echo("  required_tags: []")
    click.echo("retrieval:")
    click.echo(f"  profile:          {mode.retrieval.profile}")
    click.echo(f"  default_backends: {list(mode.retrieval.default_backends)}")
    click.echo("privacy:")
    click.echo(f"  redaction_required:       {mode.privacy.redaction_required}")
    click.echo(f"  block_external_extraction:{mode.privacy.block_external_extraction}")
    click.echo(f"  block_artifact_upload:    {mode.privacy.block_artifact_upload}")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """Inspect and validate mneme domain mode registries."""


@cli.command("list")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a modes.json config file.",
)
def list_cmd(config_path: Path | None) -> None:
    """Print sorted mode names from the resolved registry."""
    registry = _load(config_path)
    for name in sorted(registry):
        click.echo(name)


@cli.command("show")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a modes.json config file.",
)
def show_cmd(name: str, config_path: Path | None) -> None:
    """Print a summary for the mode NAME.

    Exits with status 1 if the mode is not found.
    """
    registry = _load(config_path)
    mode = registry.get(name)
    if mode is None:
        click.echo(f"error: mode {name!r} not found in registry", err=True)
        sys.exit(1)
    _show_mode(mode)


@cli.command("validate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a modes.json config file.",
)
def validate_cmd(config_path: Path | None) -> None:
    """Validate the resolved registry.

    Prints "OK" and exits 0 when there are no problems.
    Prints each problem and exits 1 otherwise.
    A present-but-invalid config prints the error and exits 1 (no traceback).
    """
    try:
        if config_path is not None:
            registry = resolve_modes(config_path=config_path)
        else:
            registry = resolve_modes(vault_root=Path("."))
    except ValueError as exc:
        click.echo(str(exc))
        sys.exit(1)

    problems = validate_modes(registry)
    if not problems:
        click.echo("OK")
    else:
        for problem in problems:
            click.echo(problem)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    cli(prog_name="mneme-modes")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
