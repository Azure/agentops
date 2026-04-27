from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentops.services.reporting import generate_report_from_results
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
report_app = typer.Typer(help="Reporting commands.")
workflow_app = typer.Typer(help="CI/CD workflow commands.")
skills_app = typer.Typer(help="Coding agent skills management.")
app.add_typer(eval_app, name="eval")
app.add_typer(report_app, name="report")
app.add_typer(workflow_app, name="workflow")
app.add_typer(skills_app, name="skills")

log = get_logger(__name__)
DEFAULT_REPORT_INPUT = Path(".agentops/results/latest/results.json")


def _resolve_platforms(
    directory: Path,
    explicit: list[str] | None,
    prompt: bool,
) -> list[str]:
    """Resolve target platforms: explicit > auto-detect > fallback."""
    from agentops.services.skills import detect_platforms

    if explicit:
        return explicit

    detected = detect_platforms(directory)
    if detected:
        typer.echo(f"Detected coding agent platform(s): {', '.join(detected)}")
        return detected

    if prompt:
        install = typer.confirm(
            "No coding agent platform detected. Install skills for GitHub Copilot?",
            default=True,
        )
        return ["copilot"] if install else []

    return ["copilot"]


def _print_skills_result(result: object) -> None:
    """Print skills installation summary."""
    platforms = getattr(result, "platforms", [])
    if platforms:
        typer.echo(f"Skills platforms: {', '.join(platforms)}")
    for created in result.created_files:  # type: ignore[attr-defined]
        typer.echo(f" + created {created}")
    for overwritten in result.overwritten_files:  # type: ignore[attr-defined]
        typer.echo(f" ~ overwritten {overwritten}")
    for skipped in result.skipped_files:  # type: ignore[attr-defined]
        typer.echo(f" - skipped {skipped} (use --force to overwrite)")


def _print_registration_result(result: object) -> None:
    """Print skill registration summary."""
    registered = getattr(result, "registered_files", [])
    for path in registered:
        typer.echo(f" * registered skills in {path}")


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
    flat: bool = typer.Option(
        False,
        "--flat",
        help="Use the 1.0 minimal layout (agentops.yaml at project root + .agentops/data/smoke.jsonl).",
    ),
) -> None:
    """Initialise an AgentOps workspace.

    The default layout creates a full ``.agentops/`` workspace (legacy). Pass
    ``--flat`` to bootstrap the simpler 1.0 layout — a single
    ``agentops.yaml`` at the project root and a tiny seed dataset.
    """
    if flat:
        from agentops.services.initializer import initialize_flat_workspace

        log.debug("cmd_init flat=True force=%s dir=%s", force, directory)
        try:
            result = initialize_flat_workspace(directory=directory, force=force)
        except Exception as exc:
            typer.echo(f"Error: failed to initialize workspace: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        typer.echo("Initialized AgentOps 1.0 workspace.")
        for created in result.created_files:
            typer.echo(f" + created {created}")
        for overwritten in result.overwritten_files:
            typer.echo(f" ~ overwritten {overwritten}")
        for skipped in result.skipped_files:
            typer.echo(f" - skipped {skipped}")
        typer.echo("")
        typer.echo("Edit agentops.yaml to point at your agent, then run: agentops eval run")
        return

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

    typer.echo("")
    typer.echo("To install coding agent skills, run: agentops skills install")


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
            help="Path to agentops.yaml (1.0) or run.yaml (legacy). "
            "Defaults: agentops.yaml, then .agentops/run.yaml.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for results."),
    ] = None,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help="Path to a previous results.json to compare this run against (1.0 only).",
        ),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
) -> None:
    """Run an evaluation defined in agentops.yaml (1.0) or run.yaml (legacy)."""
    if report_format not in ("md", "html", "all"):
        typer.echo("Error: --format must be md, html, or all.", err=True)
        raise typer.Exit(code=1)

    config_path = _resolve_eval_config_path(config)
    log.debug(
        "cmd_eval_run called config=%s output=%s format=%s baseline=%s",
        config_path,
        output,
        report_format,
        baseline,
    )

    if _is_flat_schema(config_path):
        _run_flat_schema_eval(
            config_path=config_path,
            output=output,
            baseline=baseline,
        )
        return

    if baseline is not None:
        typer.echo(
            "Error: --baseline is only supported with the 1.0 agentops.yaml schema.",
            err=True,
        )
        raise typer.Exit(code=1)

    from agentops.services.runner import run_evaluation as legacy_run_evaluation

    try:
        run_result = legacy_run_evaluation(
            config_path=config_path,
            output_override=output,
            report_format=report_format,
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


def _resolve_eval_config_path(config: Path | None) -> Path:
    if config is not None:
        return config
    flat = Path("agentops.yaml")
    if flat.exists():
        return flat
    return Path(".agentops/run.yaml")


def _is_flat_schema(config_path: Path) -> bool:
    """Return True when config_path is a 1.0 agentops.yaml file."""
    if not config_path.exists():
        return False
    try:
        from agentops.utils.yaml import load_yaml
    except ImportError:
        return False
    try:
        data = load_yaml(config_path)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    # Flat schema markers: 'agent' (string) at top-level, no legacy 'target'/'bundle'.
    if "target" in data or "bundle" in data:
        return False
    return isinstance(data.get("agent"), str)


def _run_flat_schema_eval(
    *,
    config_path: Path,
    output: Path | None,
    baseline: Path | None,
) -> None:
    from agentops.core.config_loader import load_agentops_config
    from agentops.pipeline.orchestrator import (
        RunOptions,
        exit_code_from,
        run_evaluation,
    )

    try:
        config_obj = load_agentops_config(config_path)
    except Exception as exc:
        typer.echo(f"Error: failed to load {config_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    output_dir = output or _default_flat_output_dir(config_path)

    options = RunOptions(
        config_path=config_path.resolve(),
        output_dir=output_dir,
        baseline_path=baseline.resolve() if baseline else None,
    )

    try:
        result = run_evaluation(config_obj, options=options)
    except Exception as exc:
        typer.echo(f"Error: evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Evaluation output directory: {output_dir}")
    typer.echo(f"results.json: {output_dir / 'results.json'}")
    typer.echo(f"report.md:    {output_dir / 'report.md'}")
    if result.summary.overall_passed:
        typer.echo("Threshold status: PASSED")
        return
    typer.echo("Threshold status: FAILED")
    raise typer.Exit(code=exit_code_from(result))


def _default_flat_output_dir(config_path: Path) -> Path:
    base = config_path.parent / ".agentops" / "results"
    return base / "latest"


def _is_flat_results(results_path: Path) -> bool:
    """Return True when results.json was produced by the flat pipeline."""
    if not results_path.exists():
        return False
    try:
        import json as _json
        data = _json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    target = data.get("target")
    return (
        data.get("version") == 1
        and isinstance(target, dict)
        and "kind" in target
        and "bundle" not in data
    )


def _regenerate_flat_report(
    *,
    results_path: Path,
    output_path: Path | None,
    report_format: str,
) -> Path:
    """Render report.md from a flat-pipeline results.json."""
    import json as _json

    from agentops.core.results import RunResult
    from agentops.pipeline import reporter as flat_reporter

    if report_format not in ("md", "all"):
        raise ValueError(
            "flat-pipeline results only support --format md (got %r)" % report_format
        )
    payload = _json.loads(results_path.read_text(encoding="utf-8"))
    result = RunResult.model_validate(payload)
    target = output_path or (results_path.parent / "report.md")
    target.write_text(flat_reporter.render(result), encoding="utf-8")
    return target



# ---------------------------------------------------------------------------
# agentops report generate
# ---------------------------------------------------------------------------


@report_app.command("generate")
def cmd_report_generate(
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
    if report_format not in ("md", "html", "all"):
        typer.echo("Error: --format must be md, html, or all.", err=True)
        raise typer.Exit(code=1)

    resolved_results_in = results_in or DEFAULT_REPORT_INPUT
    log.debug(
        "cmd_report_generate called in=%s out=%s format=%s",
        resolved_results_in,
        report_out,
        report_format,
    )

    if _is_flat_results(resolved_results_in):
        try:
            output_path = _regenerate_flat_report(
                results_path=resolved_results_in,
                output_path=report_out,
                report_format=report_format,
            )
        except Exception as exc:
            typer.echo(f"Error: report generation failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        typer.echo(f"Loaded results: {resolved_results_in}")
        typer.echo(f"Generated report: {output_path}")
        return

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


# ---------------------------------------------------------------------------
# agentops workflow generate
# ---------------------------------------------------------------------------


@workflow_app.command("generate")
def cmd_workflow_generate(
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing workflow files."
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
) -> None:
    """Generate GitHub Actions workflows for AgentOps evaluation.

    Auto-detects which pipelines to create based on the .agentops/ workspace:
    PR evaluation (always), CI evaluation (multiple configs), and CD pipeline
    with safety QA gate + deploy placeholder (multiple configs).
    """
    from agentops.services.cicd import generate_cicd_workflows

    log.debug("cmd_workflow_generate called force=%s dir=%s", force, directory)
    try:
        result = generate_cicd_workflows(directory=directory, force=force)
    except Exception as exc:
        typer.echo(f"Error: failed to generate CI/CD workflows: {exc}", err=True)
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
        typer.echo("  4. Commit and push the workflow files")
    elif result.skipped_files:
        typer.echo("No files written. Use --force to overwrite existing workflows.")


# ---------------------------------------------------------------------------
# agentops skills install
# ---------------------------------------------------------------------------


@skills_app.command("install")
def cmd_skills_install(
    platform: Annotated[
        list[str] | None,
        typer.Option(
            "--platform",
            "-p",
            help="Target platform(s): copilot, claude.",
        ),
    ] = None,
    from_github: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Install a community skill from GitHub. "
                "Format: org/repo or github:org/repo[@ref]. "
                "Example: --from donlee/pptx-designer"
            ),
        ),
    ] = None,
    force: bool = typer.Option(
        False,
        "--force",
        help="Deprecated — skills are always overwritten with the latest version.",
    ),
    prompt: bool = typer.Option(
        False,
        "--prompt",
        help="Ask before installing skills when no coding agent platform is detected.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
) -> None:
    """Install AgentOps coding agent skills into the target project.

    Use --from to install a community skill from GitHub:

        agentops skills install --from donlee/pptx-designer

        agentops skills install --from github:org/repo@v1.0
    """
    log.debug(
        "cmd_skills_install called platform=%s from=%s force=%s prompt=%s dir=%s",
        platform,
        from_github,
        force,
        prompt,
        directory,
    )
    resolved_platforms = _resolve_platforms(
        directory=directory, explicit=platform, prompt=prompt
    )
    if not resolved_platforms:
        typer.echo("No platforms selected. Skipping skill installation.")
        return

    if from_github:
        # GitHub-based skill installation
        from agentops.services.skills import install_github_skill

        typer.echo(f"Installing skill from GitHub: {from_github}")
        try:
            result = install_github_skill(
                source=from_github,
                directory=directory,
                platforms=resolved_platforms,
                force=True,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            typer.echo(f"Error: failed to install skill: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        _print_skills_result(result)
        return

    # Bundled skills installation
    from agentops.services.skills import install_skills

    try:
        result = install_skills(
            directory=directory, platforms=resolved_platforms, force=True
        )
    except Exception as exc:
        typer.echo(f"Error: failed to install skills: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _print_skills_result(result)

    from agentops.services.skills import register_skills

    try:
        reg_result = register_skills(directory=directory, platforms=resolved_platforms)
    except Exception as exc:
        typer.echo(f"Warning: failed to register skills: {exc}", err=True)
    else:
        _print_registration_result(reg_result)


def main() -> None:
    app()
