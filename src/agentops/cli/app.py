from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from agentops.services.initializer import initialize_workspace
from agentops.services.reporting import generate_report_from_results
from agentops.services.runner import run_evaluation
from agentops.utils.logging import get_logger, setup_logging

app = typer.Typer(
    name="agentops",
    help="AgentOps — standardized evaluation workflows for AI projects.",
    add_completion=False,
)
eval_app = typer.Typer(
    help=(
        "Evaluation sub-commands. "
        "Use `agentops eval run --help` to see run options like "
        "`--config` (`-c`) and `--output` (`-o`)."
    )
)
app.add_typer(eval_app, name="eval")

log = get_logger(__name__)
DEFAULT_REPORT_INPUT = Path(".agentops/results/latest/results.json")


# ---------------------------------------------------------------------------
# Global callback — configures logging before any command runs
# ---------------------------------------------------------------------------

@app.callback()
def _main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable DEBUG logging."),
    ] = False,
) -> None:
    setup_logging(verbose=verbose)


# ---------------------------------------------------------------------------
# agentops init
# ---------------------------------------------------------------------------

@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", help="Overwrite starter files if they exist."),
    directory: Path = typer.Option(Path("."), "--dir", help="Workspace directory to initialise."),
) -> None:
    """Initialise an AgentOps workspace (creates .agentops/config.yaml)."""
    log.debug("cmd_init called force=%s dir=%s", force, directory)
    try:
        result = initialize_workspace(directory=directory, force=force)
    except Exception as exc:
        typer.echo(f"Error: failed to initialize workspace: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Initialized workspace: {result.workspace_dir}")
    typer.echo(
        "Summary: "
        f"created_dirs={len(result.created_dirs)}, "
        f"created_files={len(result.created_files)}, "
        f"overwritten_files={len(result.overwritten_files)}, "
        f"skipped_files={len(result.skipped_files)}"
    )

    for created in result.created_files:
        typer.echo(f" + created {created}")
    for overwritten in result.overwritten_files:
        typer.echo(f" ~ overwritten {overwritten}")
    for skipped in result.skipped_files:
        typer.echo(f" - skipped {skipped}")


# ---------------------------------------------------------------------------
# agentops eval run
# ---------------------------------------------------------------------------

@eval_app.command("run")
def cmd_eval_run(
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to run.yaml (default: .agentops/run.yaml).",
        ),
    ] = None,
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Output directory for results.")] = None,
) -> None:
    """Run an evaluation defined in a run.yaml file."""
    log.debug("cmd_eval_run called config=%s output=%s", config, output)
    try:
        run_result = run_evaluation(config_path=config, output_override=output)
    except Exception as exc:
        typer.echo(f"Error: evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Evaluation output directory: {run_result.output_dir}")
    typer.echo(f"results.json: {run_result.results_path}")
    typer.echo(f"report.md: {run_result.report_path}")

    if run_result.exit_code == 2:
        typer.echo("Threshold status: FAILED")
        raise typer.Exit(code=2)

    typer.echo("Threshold status: PASSED")


# ---------------------------------------------------------------------------
# agentops report
# ---------------------------------------------------------------------------

@app.command("report")
def cmd_report(
    results_in: Annotated[
        Optional[Path],
        typer.Option(
            "--in",
            help=(
                "Path to results.json. "
                "If omitted, uses .agentops/results/latest/results.json"
            ),
        ),
    ] = None,
    report_out: Annotated[Optional[Path], typer.Option("--out", help="Output path for report.md.")] = None,
) -> None:
    """Regenerate report.md from a results.json file."""
    resolved_results_in = results_in or DEFAULT_REPORT_INPUT
    log.debug("cmd_report called in=%s out=%s", resolved_results_in, report_out)
    try:
        report_result = generate_report_from_results(
            results_path=resolved_results_in,
            output_path=report_out,
        )
    except Exception as exc:
        typer.echo(f"Error: report generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Loaded results: {report_result.input_results_path}")
    typer.echo(f"Generated report: {report_result.output_report_path}")


def main() -> None:
    app()
