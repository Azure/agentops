from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
# agentops init
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


# ---------------------------------------------------------------------------
# agentops report
# ---------------------------------------------------------------------------


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


@run_app.command("list")
def cmd_run_list(
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """List past evaluation runs."""
    from agentops.services.browse import list_runs

    try:
        result = list_runs(directory=directory)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.runs:
        typer.echo(f"No runs found in {result.results_dir}")
        return

    typer.echo(f"Runs in {result.results_dir}:\n")
    for run in result.runs:
        status = "PASS" if run.overall_passed else "FAIL"
        typer.echo(
            f"  {run.run_id}  {status:<4}  "
            f"bundle={run.bundle_name}  dataset={run.dataset_name}  "
            f"duration={run.duration_seconds:.1f}s"
        )


@run_app.command("show")
def cmd_run_show(
    run_id: str = typer.Argument(help="Run ID (timestamp folder name or 'latest')."),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """Show summary of a past evaluation run."""
    from agentops.services.browse import show_run

    try:
        detail = show_run(run_id=run_id, directory=directory)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    status = "PASS" if detail.overall_passed else "FAIL"
    typer.echo(f"Run: {detail.run_id}")
    typer.echo(f"Status: {status}")
    typer.echo(f"Bundle: {detail.bundle_name}")
    typer.echo(f"Dataset: {detail.dataset_name}")
    typer.echo(f"Backend: {detail.backend}")
    typer.echo(f"Started: {detail.started_at}")
    typer.echo(f"Duration: {detail.duration_seconds:.1f}s")
    typer.echo(f"Items: {detail.items_passed}/{detail.items_total} passed")
    typer.echo("")
    typer.echo("Metrics:")
    for m in detail.metrics:
        typer.echo(f"  {m['name']:<40} {m['value']:.4f}")
    if detail.thresholds:
        typer.echo("")
        typer.echo("Thresholds:")
        for t in detail.thresholds:
            mark = "PASS" if t["passed"] else "FAIL"
            typer.echo(
                f"  {t['evaluator']:<40} {t['criteria']} {t['expected']:<10} "
                f"actual={t['actual']:<10} {mark}"
            )
    if detail.foundry_url:
        typer.echo(f"\nFoundry portal: {detail.foundry_url}")
    if detail.report_path:
        typer.echo(f"Report: {detail.report_path}")


@run_app.command("view")
def cmd_run_view(
    run_id: str,
    entry: Annotated[
        int | None,
        typer.Option("--entry", help="Optional row/entry index for deep inspection."),
    ] = None,
) -> None:
    """Deep-inspect run details (planned)."""
    _ = run_id, entry
    _planned_command("agentops run view <id> [--entry N]")


@bundle_app.command("list")
def cmd_bundle_list(
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """List available evaluation bundles."""
    from agentops.services.browse import list_bundles

    try:
        result = list_bundles(directory=directory)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.bundles:
        typer.echo(f"No bundles found in {result.bundles_dir}")
        return

    typer.echo(f"Bundles in {result.bundles_dir}:\n")
    for b in result.bundles:
        evals = ", ".join(b.evaluators) if b.evaluators else "(none)"
        typer.echo(f"  {b.name}")
        if b.description:
            typer.echo(f"    {b.description}")
        typer.echo(f"    evaluators: {evals}")
        typer.echo(f"    thresholds: {b.thresholds}")
        typer.echo("")


@bundle_app.command("show")
def cmd_bundle_show(
    bundle_name: str = typer.Argument(help="Bundle name or filename (without .yaml)."),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """Show details of an evaluation bundle."""
    from agentops.services.browse import show_bundle

    try:
        detail = show_bundle(bundle_name=bundle_name, directory=directory)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Bundle: {detail.name}")
    typer.echo(f"Path: {detail.path}")
    if detail.description:
        typer.echo(f"Description: {detail.description}")
    if detail.metadata:
        typer.echo(f"Metadata: {detail.metadata}")
    typer.echo("")
    typer.echo("Evaluators:")
    for e in detail.evaluators:
        status = "enabled" if e["enabled"] else "disabled"
        typer.echo(f"  {e['name']} (source={e['source']}, {status})")
    typer.echo("")
    typer.echo("Thresholds:")
    for t in detail.thresholds:
        value = t["value"] if t["value"] is not None else ""
        typer.echo(f"  {t['evaluator']} {t['criteria']} {value}")


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
