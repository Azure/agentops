"""Config sub-commands: config validate, config show, config cicd."""

from __future__ import annotations

from pathlib import Path

import typer

from agentops.utils.logging import get_logger

log = get_logger(__name__)

config_app = typer.Typer(help="Configuration utility commands.")


@config_app.command("validate")
def cmd_config_validate(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to run.yaml (default: .agentops/run.yaml).",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """Validate configuration files (run.yaml, bundle, dataset, data)."""
    from agentops.services.validate import validate_config

    try:
        result = validate_config(config_path=config, directory=directory)
    except FileNotFoundError as exc:
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


@config_app.command("show")
def cmd_config_show(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to run.yaml (default: .agentops/run.yaml).",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """Show resolved configuration from a run.yaml file."""
    from agentops.services.validate import show_config

    try:
        result = show_config(config_path=config, directory=directory)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Run config: {result.run_config_path}")
    typer.echo(f"Bundle: {result.bundle_name} ({result.bundle_path})")
    typer.echo(f"Dataset: {result.dataset_name} ({result.dataset_path})")
    if result.data_path:
        rows = f" ({result.data_rows} rows)" if result.data_rows is not None else ""
        typer.echo(f"Data: {result.data_path}{rows}")
    typer.echo(f"Backend: {result.backend_type} (target={result.target})")
    if result.model:
        typer.echo(f"Model: {result.model}")
    if result.agent_id:
        typer.echo(f"Agent: {result.agent_id}")
    typer.echo(f"Thresholds: {result.thresholds}")
    typer.echo("")
    typer.echo("Evaluators:")
    for e in result.evaluators:
        status = "enabled" if e["enabled"] else "disabled"
        typer.echo(f"  {e['name']} (source={e['source']}, {status})")


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
