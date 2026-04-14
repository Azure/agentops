"""Evaluation sub-commands: eval run, eval compare."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentops.utils.logging import get_logger

log = get_logger(__name__)

eval_app = typer.Typer(
    help=(
        "Evaluation sub-commands. "
        "Use `agentops eval run --help` to see run options like "
        "`--config` (`-c`) and `--output` (`-o`)."
    )
)


@eval_app.command("run")
def cmd_eval_run(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to run.yaml (default: .agentops/run.yaml).",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for results."),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
) -> None:
    """Run an evaluation defined in a run.yaml file."""
    from agentops.services.runner import run_evaluation

    if report_format not in ("md", "html", "all"):
        typer.echo("Error: --format must be md, html, or all.", err=True)
        raise typer.Exit(code=1)

    log.debug(
        "cmd_eval_run called config=%s output=%s format=%s",
        config,
        output,
        report_format,
    )
    try:
        run_result = run_evaluation(
            config_path=config, output_override=output, report_format=report_format
        )
    except Exception as exc:
        typer.echo(f"Error: evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Evaluation output directory: {run_result.output_dir}")
    typer.echo(f"results.json: {run_result.results_path}")
    typer.echo(f"report: {run_result.report_path}")

    if run_result.exit_code == 2:
        typer.echo("Threshold status: FAILED")
        raise typer.Exit(code=2)

    typer.echo("Threshold status: PASSED")


@eval_app.command("compare")
def cmd_eval_compare(
    runs: Annotated[
        str,
        typer.Option(
            "--runs", help="Comma-separated run ids (example: ID1,ID2 or ID1,ID2,ID3)."
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for comparison results."),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
) -> None:
    """Compare two or more past evaluation runs."""
    from agentops.services.comparison import run_comparison

    if report_format not in ("md", "html", "all"):
        typer.echo("Error: --format must be md, html, or all.", err=True)
        raise typer.Exit(code=1)

    parts = [p.strip() for p in runs.split(",")]
    if len(parts) < 2:
        typer.echo(
            "Error: --runs must contain at least two comma-separated run ids.", err=True
        )
        raise typer.Exit(code=1)

    log.debug(
        "cmd_eval_compare called runs=%s output=%s format=%s",
        parts,
        output,
        report_format,
    )
    try:
        result = run_comparison(
            run_ids=parts,
            output_dir=output,
            report_format=report_format,
        )
    except Exception as exc:
        typer.echo(f"Error: comparison failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"comparison.json: {result.comparison_json_path}")
    if result.comparison_md_path:
        typer.echo(f"comparison.md: {result.comparison_md_path}")
    if result.comparison_html_path:
        typer.echo(f"comparison.html: {result.comparison_html_path}")

    if result.has_regressions:
        typer.echo("Comparison verdict: REGRESSIONS DETECTED")
        raise typer.Exit(code=2)

    typer.echo("Comparison verdict: NO REGRESSIONS")
