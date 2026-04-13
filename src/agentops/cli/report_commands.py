"""Report sub-commands: report, report show, report export."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
def cmd_report_show(
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
    report_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Report format to display: md or html."),
    ] = "md",
) -> None:
    """Display an existing evaluation report."""
    import webbrowser

    resolved_in = (results_in or DEFAULT_REPORT_INPUT).resolve()
    results_dir = resolved_in.parent

    if report_format == "html":
        html_path = results_dir / "report.html"
        if not html_path.exists():
            typer.echo(
                f"Error: report.html not found in {results_dir}. "
                "Regenerate with: agentops report --format html",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Opening {html_path} in browser...")
        webbrowser.open(html_path.as_uri())
    else:
        md_path = results_dir / "report.md"
        if not md_path.exists():
            typer.echo(
                f"Error: report.md not found in {results_dir}. "
                "Regenerate with: agentops report",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(md_path.read_text(encoding="utf-8"))


@report_app.command("export")
def cmd_report_export(
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
    output: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Output file path."),
    ] = None,
    export_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Export format: json, csv, or md."),
    ] = "json",
) -> None:
    """Export evaluation results to JSON, CSV, or Markdown."""
    import csv
    import io
    import json

    resolved_in = (results_in or DEFAULT_REPORT_INPUT).resolve()

    if not resolved_in.exists():
        typer.echo(f"Error: results.json not found: {resolved_in}", err=True)
        raise typer.Exit(code=1)

    data = json.loads(resolved_in.read_text(encoding="utf-8"))

    if export_format == "json":
        content = json.dumps(data, indent=2)
    elif export_format == "csv":
        metrics = data.get("metrics", [])
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["metric", "value"])
        for m in metrics:
            writer.writerow([m.get("name", ""), m.get("value", "")])

        # Add row-level metrics if available
        row_metrics = data.get("row_metrics", [])
        if row_metrics:
            writer.writerow([])
            # Collect all metric names across rows
            metric_names: list[str] = []
            for row in row_metrics:
                for m in row.get("metrics", []):
                    if m["name"] not in metric_names:
                        metric_names.append(m["name"])
            writer.writerow(["row_index"] + metric_names)
            for row in row_metrics:
                values = {m["name"]: m["value"] for m in row.get("metrics", [])}
                writer.writerow(
                    [row.get("row_index", "")]
                    + [values.get(n, "") for n in metric_names]
                )
        content = buf.getvalue()
    elif export_format == "md":
        md_path = resolved_in.parent / "report.md"
        if not md_path.exists():
            typer.echo(
                "Error: report.md not found. Regenerate with: agentops report",
                err=True,
            )
            raise typer.Exit(code=1)
        content = md_path.read_text(encoding="utf-8")
    else:
        typer.echo("Error: --format must be json, csv, or md.", err=True)
        raise typer.Exit(code=1)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        typer.echo(f"Exported to {output}")
    else:
        typer.echo(content)
