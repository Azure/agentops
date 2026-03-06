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
run_app = typer.Typer(help="Run history and inspection commands.")
bundle_app = typer.Typer(help="Bundle browsing commands.")
dataset_app = typer.Typer(help="Dataset utility commands.")
config_app = typer.Typer(help="Configuration utility commands.")
report_app = typer.Typer(help="Reporting commands.", invoke_without_command=True)
monitor_app = typer.Typer(help="Monitoring setup and operations.")
trace_app = typer.Typer(help="Tracing commands.")
model_app = typer.Typer(help="Model discovery commands.")
agent_app = typer.Typer(help="Agent discovery commands.")
app.add_typer(eval_app, name="eval")
app.add_typer(run_app, name="run")
app.add_typer(bundle_app, name="bundle")
app.add_typer(dataset_app, name="dataset")
app.add_typer(config_app, name="config")
app.add_typer(report_app, name="report")
app.add_typer(monitor_app, name="monitor")
app.add_typer(trace_app, name="trace")
app.add_typer(model_app, name="model")
app.add_typer(agent_app, name="agent")

log = get_logger(__name__)
DEFAULT_REPORT_INPUT = Path(".agentops/results/latest/results.json")


def _planned_command(command_name: str) -> None:
    typer.echo(
        "This command is planned but not implemented in this release:\n"
        f"  {command_name}\n"
        "Please use the currently available commands (`init`, `eval run`, `report`) for now."
    )
    raise typer.Exit(code=1)


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
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        "--path",
        help="Workspace directory to initialise.",
    ),
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


@eval_app.command("compare")
def cmd_eval_compare(
    runs: Annotated[
        str,
        typer.Option("--runs", help="Comma-separated run ids (example: ID1,ID2)."),
    ],
) -> None:
    """Compare two past evaluation runs (planned)."""
    _ = runs
    _planned_command("agentops eval compare --runs ID1,ID2")


# ---------------------------------------------------------------------------
# agentops report
# ---------------------------------------------------------------------------

@report_app.callback(invoke_without_command=True)
def cmd_report(
    ctx: typer.Context,
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
    if ctx.invoked_subcommand is not None:
        return

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


@report_app.command("show")
def cmd_report_show() -> None:
    """View reports in table format (planned)."""
    _planned_command("agentops report show")


@report_app.command("export")
def cmd_report_export() -> None:
    """Export reports in JSON/Markdown/CSV formats (planned)."""
    _planned_command("agentops report export")


@run_app.command("list")
def cmd_run_list() -> None:
    """List past evaluation runs (planned)."""
    _planned_command("agentops run list")


@run_app.command("show")
def cmd_run_show() -> None:
    """Show summary of a past run (planned)."""
    _planned_command("agentops run show")


@run_app.command("view")
def cmd_run_view(
    run_id: str,
    entry: Annotated[
        Optional[int],
        typer.Option("--entry", help="Optional row/entry index for deep inspection."),
    ] = None,
) -> None:
    """Deep-inspect run details (planned)."""
    _ = run_id, entry
    _planned_command("agentops run view <id> [--entry N]")


@bundle_app.command("list")
def cmd_bundle_list() -> None:
    """List available bundles (planned)."""
    _planned_command("agentops bundle list")


@bundle_app.command("show")
def cmd_bundle_show() -> None:
    """Show bundle details (planned)."""
    _planned_command("agentops bundle show")


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


@config_app.command("validate")
def cmd_config_validate() -> None:
    """Validate configuration files (planned)."""
    _planned_command("agentops config validate")


@config_app.command("show")
def cmd_config_show() -> None:
    """Show merged runtime config (planned)."""
    _planned_command("agentops config show")


@config_app.command("cicd")
def cmd_config_cicd() -> None:
    """Generate CI/CD pipeline config (planned)."""
    _planned_command("agentops config cicd")


@trace_app.command("init")
def cmd_trace_init() -> None:
    """Set up tracing integration (planned)."""
    _planned_command("agentops trace init")


@monitor_app.command("setup")
def cmd_monitor_setup() -> None:
    """Set up monitoring resources (planned)."""
    _planned_command("agentops monitor setup")


@monitor_app.command("dashboard")
def cmd_monitor_dashboard() -> None:
    """Show monitoring dashboard setup instructions (planned)."""
    _planned_command("agentops monitor dashboard")


@monitor_app.command("alert")
def cmd_monitor_alert() -> None:
    """Configure monitoring alerts (planned)."""
    _planned_command("agentops monitor alert")


@model_app.command("list")
def cmd_model_list() -> None:
    """List chat-capable models in Foundry project (planned)."""
    _planned_command("agentops model list")


@agent_app.command("list")
def cmd_agent_list() -> None:
    """List agents in Foundry project (planned)."""
    _planned_command("agentops agent list")


def main() -> None:
    app()
