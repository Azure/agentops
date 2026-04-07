"""Planned stub commands: dataset, monitor, trace, model, agent."""

from __future__ import annotations

import typer

from agentops.cli._planned import _planned_command

dataset_app = typer.Typer(help="Dataset utility commands.")
monitor_app = typer.Typer(help="Monitoring setup and operations.")
trace_app = typer.Typer(help="Tracing commands.")
model_app = typer.Typer(help="Model discovery commands.")
agent_app = typer.Typer(help="Agent discovery commands.")


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------


@trace_app.command("init")
def cmd_trace_init() -> None:
    """Set up tracing integration (planned)."""
    _planned_command("agentops trace init")


# ---------------------------------------------------------------------------
# model / agent
# ---------------------------------------------------------------------------


@model_app.command("list")
def cmd_model_list() -> None:
    """List chat-capable models in Foundry project (planned)."""
    _planned_command("agentops model list")


@agent_app.command("list")
def cmd_agent_list() -> None:
    """List agents in Foundry project (planned)."""
    _planned_command("agentops agent list")
