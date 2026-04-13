"""AgentOps CLI — main application and sub-command registration.

This module creates the root Typer app, registers sub-command groups
from their respective modules, and defines the global callback (logging,
version) and the ``init`` command.

Command modules:
    eval_commands    — eval run, eval compare
    report_commands  — report, report show, report export
    browse_commands  — bundle list/show, run list/show/view
    config_commands  — config cicd, config validate, config show
    planned          — dataset, monitor, trace, model, agent (stubs)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentops.utils.logging import get_logger, setup_logging

# ---------------------------------------------------------------------------
# Import sub-command apps from their modules
# ---------------------------------------------------------------------------
from agentops.cli._planned import _planned_command
from agentops.cli.browse_commands import bundle_app, run_app
from agentops.cli.config_commands import config_app
from agentops.cli.dataset_commands import dataset_app
from agentops.cli.eval_commands import eval_app
from agentops.cli.report_commands import report_app

# ---------------------------------------------------------------------------
# Stub sub-apps for future command groups (1-2 commands each)
# ---------------------------------------------------------------------------
monitor_app = typer.Typer(help="Monitoring setup and operations.")
trace_app = typer.Typer(help="Tracing commands.")
model_app = typer.Typer(help="Model discovery commands.")
agent_app = typer.Typer(help="Agent discovery commands.")


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


@trace_app.command("init")
def cmd_trace_init() -> None:
    """Set up tracing integration (planned)."""
    _planned_command("agentops trace init")


@model_app.command("list")
def cmd_model_list() -> None:
    """List chat-capable models in Foundry project (planned)."""
    _planned_command("agentops model list")


@agent_app.command("list")
def cmd_agent_list() -> None:
    """List agents in Foundry project (planned)."""
    _planned_command("agentops agent list")


# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="agentops",
    help="AgentOps — standardized evaluation workflows for AI projects.",
    add_completion=False,
)

# Register sub-command groups
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


# ---------------------------------------------------------------------------
# Global callback — configures logging before any command runs
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        from agentops import __version__

        typer.echo(f"agentops {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable DEBUG logging."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    setup_logging(verbose=verbose)


# ---------------------------------------------------------------------------
# agentops init (top-level command, lives here)
# ---------------------------------------------------------------------------


@app.command("init")
def cmd_init(
    force: bool = typer.Option(
        False, "--force", help="Overwrite starter files if they exist."
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        "--path",
        help="Workspace directory to initialise.",
    ),
) -> None:
    """Initialise an AgentOps workspace (creates .agentops/config.yaml)."""
    from agentops.services.initializer import initialize_workspace

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


def main() -> None:
    app()
