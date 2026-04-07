"""Dataset sub-commands: dataset validate, dataset describe, dataset import."""

from __future__ import annotations

from pathlib import Path

import typer

from agentops.cli._planned import _planned_command

dataset_app = typer.Typer(help="Dataset utility commands.")


@dataset_app.command("validate")
def cmd_dataset_validate(
    dataset: Path = typer.Argument(help="Path to dataset YAML config file."),
) -> None:
    """Validate a dataset config and its JSONL data file."""
    from agentops.services.validate import validate_dataset

    try:
        result = validate_dataset(dataset_path=dataset)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Files checked: {result.files_checked}")
    for issue in result.issues:
        marker = "ERROR" if issue.severity == "error" else "WARN"
        typer.echo(f"  [{marker}] {issue.file.name}: {issue.message}")

    if result.passed:
        typer.echo("Validation: PASSED")
    else:
        typer.echo("Validation: FAILED")
        raise typer.Exit(code=1)


@dataset_app.command("describe")
def cmd_dataset_describe(
    dataset: Path = typer.Argument(help="Path to dataset YAML config file."),
) -> None:
    """Describe dataset schema, fields, and row count."""
    from agentops.services.validate import describe_dataset

    try:
        desc = describe_dataset(dataset_path=dataset)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Dataset: {desc.name}")
    if desc.description:
        typer.echo(f"Description: {desc.description}")
    typer.echo(f"Source: {desc.source_type}")
    typer.echo(f"Format: {desc.format_type}")
    typer.echo(f"Input field: {desc.input_field}")
    typer.echo(f"Expected field: {desc.expected_field}")
    if desc.context_field:
        typer.echo(f"Context field: {desc.context_field}")
    if desc.data_path:
        typer.echo(f"Data file: {desc.data_path}")
    typer.echo(f"Rows: {desc.row_count}")
    if desc.fields:
        typer.echo(f"Fields: {', '.join(desc.fields)}")
    if desc.metadata:
        typer.echo(f"Metadata: {desc.metadata}")


@dataset_app.command("import")
def cmd_dataset_import() -> None:
    """Import external datasets (planned)."""
    _planned_command("agentops dataset import")
