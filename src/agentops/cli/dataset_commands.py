"""Dataset sub-commands: dataset validate, dataset describe, dataset import."""

from __future__ import annotations

import typer

from agentops.cli._planned import _planned_command

dataset_app = typer.Typer(help="Dataset utility commands.")


@dataset_app.command("validate")
def cmd_dataset_validate() -> None:
    """Validate dataset files (planned)."""
    _planned_command("agentops dataset validate")


@dataset_app.command("describe")
def cmd_dataset_describe() -> None:
    """Describe dataset schema and shape (planned)."""
    _planned_command("agentops dataset describe")


@dataset_app.command("import")
def cmd_dataset_import() -> None:
    """Import external datasets (planned)."""
    _planned_command("agentops dataset import")
