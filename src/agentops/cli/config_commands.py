"""Config sub-commands: config validate, config show, config cicd."""

from __future__ import annotations

from pathlib import Path

import typer

from agentops.cli._planned import _planned_command
from agentops.utils.logging import get_logger

log = get_logger(__name__)

config_app = typer.Typer(help="Configuration utility commands.")


@config_app.command("validate")
def cmd_config_validate() -> None:
    """Validate configuration files (planned)."""
    _planned_command("agentops config validate")


@config_app.command("show")
def cmd_config_show() -> None:
    """Show merged runtime config (planned)."""
    _planned_command("agentops config show")


@config_app.command("cicd")
def cmd_config_cicd(
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing workflow file."
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
) -> None:
    """Generate a GitHub Actions workflow for AgentOps evaluation."""
    from agentops.services.cicd import generate_cicd_workflow

    log.debug("cmd_config_cicd called force=%s dir=%s", force, directory)
    try:
        result = generate_cicd_workflow(directory=directory, force=force)
    except Exception as exc:
        typer.echo(f"Error: failed to generate CI/CD workflow: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for created in result.created_files:
        typer.echo(f" + created {created}")
    for overwritten in result.overwritten_files:
        typer.echo(f" ~ overwritten {overwritten}")
    for skipped in result.skipped_files:
        typer.echo(f" - skipped {skipped} (use --force to overwrite)")

    if result.created_files or result.overwritten_files:
        typer.echo("")
        typer.echo("Next steps:")
        typer.echo(
            "  1. Set GitHub repository variables: AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID"
        )
        typer.echo(
            "  2. Set GitHub repository secret: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
        )
        typer.echo(
            "  3. Configure Azure Workload Identity Federation (see docs/ci-github-actions.md)"
        )
        typer.echo("  4. Commit and push the workflow file")
    elif result.skipped_files:
        typer.echo("No files written. Use --force to overwrite existing workflow.")
