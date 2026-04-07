"""Shared helper for planned (stub) commands."""

from __future__ import annotations

import typer


def _planned_command(command_name: str) -> None:
    """Print a message and exit with code 1 for unimplemented commands."""
    typer.echo(
        "This command is planned but not implemented in this release:\n"
        f"  {command_name}\n"
        "Please use the currently available commands"
        " (`init`, `eval run`, `eval compare`, `report`, `config cicd`) for now."
    )
    raise typer.Exit(code=1)
