"""Report sub-commands: report, report show, report export."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentops.cli._planned import _planned_command
from agentops.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_REPORT_INPUT = Path(".agentops/results/latest/results.json")

report_app = typer.Typer(help="Reporting commands.", invoke_without_command=True)


@report_app.callback(invoke_without_command=True)
def cmd_report(
    ctx: typer.Context,
    results_in: Annotated[
        Path | None,
        typer.Option(
            "--in",
            help=(
                "Path to results.json. "
                "If omitted, uses .agentops/results/latest/results.json"
            ),
        ),
    ] = None,
    report_out: Annotated[
        Path | None,
        typer.Option("--out", help="Output path for report."),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
) -> None:
    """Regenerate report from a results.json file."""
    from agentops.services.reporting import generate_report_from_results

    if ctx.invoked_subcommand is not None:
        return

    if report_format not in ("md", "html", "all"):
        typer.echo("Error: --format must be md, html, or all.", err=True)
        raise typer.Exit(code=1)

    resolved_results_in = results_in or DEFAULT_REPORT_INPUT
    log.debug(
        "cmd_report called in=%s out=%s format=%s",
        resolved_results_in,
        report_out,
        report_format,
    )
    try:
        report_result = generate_report_from_results(
            results_path=resolved_results_in,
            output_path=report_out,
            report_format=report_format,
        )
    except Exception as exc:
        typer.echo(f"Error: report generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Loaded results: {report_result.input_results_path}")
    typer.echo(f"Generated report: {report_result.output_report_path}")
    if report_result.html_report_path:
        typer.echo(f"Generated report: {report_result.html_report_path}")


@report_app.command("show")
def cmd_report_show() -> None:
    """View reports in table format (planned)."""
    _planned_command("agentops report show")


@report_app.command("export")
def cmd_report_export() -> None:
    """Export reports in JSON/Markdown/CSV formats (planned)."""
    _planned_command("agentops report export")
