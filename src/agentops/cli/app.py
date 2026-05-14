from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from agentops.utils.colors import style
from agentops.utils.logging import get_logger, setup_logging

app = typer.Typer(
    name="agentops",
    help="AgentOps - standardized evaluation workflows for AI projects.",
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
mcp_app = typer.Typer(help="MCP (Model Context Protocol) server commands.")
agent_app = typer.Typer(
    help=(
        "Agent server commands (host AgentOps as a Copilot SDK agent). "
        "Use `agentops doctor` for the local diagnostic analyzer."
    )
)
app.add_typer(eval_app, name="eval")
app.add_typer(report_app, name="report")
app.add_typer(workflow_app, name="workflow")
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(agent_app, name="agent")

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
# Global callback - configures logging before any command runs
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
    """Initialise an AgentOps workspace.

    Bootstraps the 1.0 minimal layout: a single ``agentops.yaml`` at the
    project root and a tiny seed dataset under ``.agentops/data/smoke.jsonl``.
    """
    from agentops.services.initializer import initialize_flat_workspace

    log.debug("cmd_init called force=%s dir=%s", force, directory)
    try:
        result = initialize_flat_workspace(directory=directory, force=force)
    except Exception as exc:
        typer.echo(f"Error: failed to initialize workspace: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Initialized AgentOps workspace.")
    for created in result.created_files:
        typer.echo(f" + created {created}")
    for overwritten in result.overwritten_files:
        typer.echo(f" ~ overwritten {overwritten}")
    for skipped in result.skipped_files:
        typer.echo(f" - skipped {skipped}")
    typer.echo("")
    typer.echo("Edit agentops.yaml to point at your agent, then run: agentops eval run")
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
            help="Path to agentops.yaml. Defaults to ./agentops.yaml.",
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
            help="Path to a previous results.json to compare this run against.",
        ),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
) -> None:
    """Run an evaluation defined in agentops.yaml."""
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

    if not config_path.exists():
        typer.echo(
            f"Error: config not found at {config_path}. "
            "Run `agentops init` to scaffold a starter agentops.yaml.",
            err=True,
        )
        raise typer.Exit(code=1)

    _run_flat_schema_eval(
        config_path=config_path,
        output=output,
        baseline=baseline,
    )


def _resolve_eval_config_path(config: Path | None) -> Path:
    if config is not None:
        return config
    return Path("agentops.yaml")


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

    use_default_layout = output is None
    if use_default_layout:
        output_dir: Path = _default_flat_output_dir(config_path)
    else:
        assert output is not None
        output_dir = output

    options = RunOptions(
        config_path=config_path.resolve(),
        output_dir=output_dir,
        baseline_path=baseline.resolve() if baseline else None,
        progress=lambda msg: typer.echo(msg),
    )

    try:
        result = run_evaluation(config_obj, options=options)
    except Exception as exc:
        typer.echo(f"Error: evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    latest_dir = config_path.parent / ".agentops" / "results" / "latest"
    if output_dir.resolve() != latest_dir.resolve():
        try:
            _mirror_to_latest(output_dir, latest_dir)
        except Exception as exc:  # pragma: no cover - mirror failures shouldn't fail the run
            typer.echo(
                f"Warning: failed to update {latest_dir}: {exc}",
                err=True,
            )
            latest_dir = None  # type: ignore[assignment]
    else:
        latest_dir = None  # type: ignore[assignment]

    typer.echo(f"Evaluation output directory: {style(str(output_dir), 'cyan')}")
    typer.echo(f"results.json: {style(str(output_dir / 'results.json'), 'cyan')}")
    typer.echo(f"report.md:    {style(str(output_dir / 'report.md'), 'cyan')}")
    if latest_dir is not None:
        typer.echo(f"latest/:      {style(str(latest_dir), 'cyan')}")
    if result.summary.overall_passed:
        typer.echo(f"Threshold status: {style('PASSED', 'bold', 'green')}")
        return
    typer.echo(f"Threshold status: {style('FAILED', 'bold', 'red')}")
    raise typer.Exit(code=exit_code_from(result))


def _default_flat_output_dir(config_path: Path) -> Path:
    base = config_path.parent / ".agentops" / "results"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return base / timestamp


def _mirror_to_latest(source: Path, latest: Path) -> None:
    """Replace ``latest`` with a copy of ``source``."""
    if latest.exists():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        else:
            shutil.rmtree(latest)
    shutil.copytree(source, latest)


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
            "Only --format md is supported (got %r)" % report_format
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
        str, typer.Option("--format", "-f", help="Report format: md (default).")
    ] = "md",
) -> None:
    """Regenerate report.md from a results.json file."""
    if report_format not in ("md", "all"):
        typer.echo("Error: --format must be md or all.", err=True)
        raise typer.Exit(code=1)

    resolved_results_in = results_in or DEFAULT_REPORT_INPUT
    log.debug(
        "cmd_report_generate called in=%s out=%s format=%s",
        resolved_results_in,
        report_out,
        report_format,
    )

    if not resolved_results_in.exists():
        typer.echo(
            f"Error: results not found at {resolved_results_in}.", err=True
        )
        raise typer.Exit(code=1)

    if not _is_flat_results(resolved_results_in):
        typer.echo(
            f"Error: {resolved_results_in} is not an AgentOps 1.0 results.json. "
            "Re-run `agentops eval run` to regenerate it.",
            err=True,
        )
        raise typer.Exit(code=1)

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
    kinds: str = typer.Option(
        "",
        "--kinds",
        help=(
            "Comma-separated subset of workflow kinds to generate. "
            "Valid values: pr, dev, qa, prod. "
            "Default (empty) generates all four."
        ),
    ),
    platform: str = typer.Option(
        "github",
        "--platform",
        "-p",
        help=(
            "CI/CD platform. 'github' (default) writes "
            "`.github/workflows/*.yml`; 'azure-devops' writes "
            "`.azuredevops/pipelines/*.yml`."
        ),
    ),
) -> None:
    """Generate the AgentOps GitFlow CI/CD workflows.

    By default writes all four templates that map to a classic GitFlow
    setup with three deploy environments (dev, qa, production):

      - agentops-pr           (PR gate; PRs to develop, release/**, main)
      - agentops-deploy-dev   (push to develop  -> environment: dev)
      - agentops-deploy-qa    (push to release/** -> environment: qa)
      - agentops-deploy-prod  (push to main      -> environment: production)

    Use --kinds to opt into a subset (e.g. --kinds pr,dev), and
    --platform to target either GitHub Actions or Azure DevOps Pipelines.
    The conceptual workflows are identical across platforms.
    """
    from agentops.services.cicd import ALL_KINDS, PLATFORMS, generate_cicd_workflows

    log.debug(
        "cmd_workflow_generate called force=%s dir=%s kinds=%r platform=%s",
        force, directory, kinds, platform,
    )

    if platform not in PLATFORMS:
        typer.echo(
            f"Error: unknown --platform value {platform!r}. "
            f"Valid: {', '.join(PLATFORMS)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    selected: list[str] | None = None
    if kinds.strip():
        selected = [k.strip() for k in kinds.split(",") if k.strip()]
        invalid = [k for k in selected if k not in ALL_KINDS]
        if invalid:
            typer.echo(
                f"Error: unknown --kinds value(s): {', '.join(invalid)}. "
                f"Valid: {', '.join(ALL_KINDS)}.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        result = generate_cicd_workflows(
            directory=directory, force=force, kinds=selected, platform=platform,
        )
    except Exception as exc:
        typer.echo(f"Error: failed to generate CI/CD workflows: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Platform: {result.platform}")
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
            "  1. Configure Azure Workload Identity Federation (OIDC) and set "
            "repository variables AZURE_CLIENT_ID, AZURE_TENANT_ID, "
            "AZURE_SUBSCRIPTION_ID, AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
        )
        typer.echo(
            "  2. Create three GitHub Environments: 'dev', 'qa', 'production'. "
            "Add required reviewers to 'production'."
        )
        typer.echo(
            "  3. Open each agentops-deploy-*.yml and replace the Build/Deploy "
            "placeholder steps with your stack's commands "
            "(snippets are provided in comments)."
        )
        typer.echo(
            "  4. In Settings -> Branches, require the 'AgentOps PR' status check "
            "on develop and main."
        )
        typer.echo(
            "  5. Commit and push. See docs/ci-github-actions.md for the full guide."
        )
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
        help="Deprecated - skills are always overwritten with the latest version.",
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


# ---------------------------------------------------------------------------
# agentops mcp serve
# ---------------------------------------------------------------------------


@mcp_app.command("serve")
def cmd_mcp_serve() -> None:
    """Start the AgentOps MCP server on stdio.

    Exposes the AgentOps workflow (init, eval run, report show, results
    summary, dataset add, list runs, workflow init) as MCP tools so that
    MCP-aware coding agents can drive AgentOps directly.

    Requires the optional ``mcp`` extra:

        pip install agentops-toolkit[mcp]
    """
    try:
        from agentops.mcp.server import serve_stdio
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        serve_stdio()
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# `agentops agent` commands
# ---------------------------------------------------------------------------


def _resolve_agent_config_path(workspace: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    candidate = workspace / ".agentops" / "agent.yaml"
    return candidate if candidate.exists() else None


@app.command("doctor")
def cmd_doctor(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            "-w",
            help="Project root containing `.agentops/`.",
        ),
    ] = Path("."),
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to `agent.yaml` (default: `.agentops/agent.yaml`).",
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Where to write the Markdown report.",
        ),
    ] = Path(".agentops/agent/report.md"),
    lookback_days: Annotated[
        int | None,
        typer.Option(
            "--lookback-days",
            help="Override the lookback window for production telemetry.",
        ),
    ] = None,
    severity_fail: Annotated[
        str,
        typer.Option(
            "--severity-fail",
            help="Exit 2 when a finding at or above this severity is produced.",
        ),
    ] = "critical",
    categories: Annotated[
        str | None,
        typer.Option(
            "--categories",
            help=(
                "Comma-separated list of categories to include "
                "(quality, performance, reliability, "
                "operational_excellence, security, responsible_ai). "
                "Default: include all."
            ),
        ),
    ] = None,
    exclude_rules: Annotated[
        str | None,
        typer.Option(
            "--exclude-rules",
            help=(
                "Comma-separated list of posture rule ids to skip "
                "(for example `waf.security.diagnostic_settings`)."
            ),
        ),
    ] = None,
) -> None:
    """Diagnose MLOps / security / responsible-AI gaps in this workspace.

    The AgentOps doctor scans your workspace, eval history, and (when
    configured) production telemetry, then produces a severity-ranked
    list of findings + a Markdown report. Complementary to Foundry
    Operate -> Compliance - this is the half Foundry doesn't surface
    (pipeline hygiene, identity / pipeline security, prompt + bundle
    Responsible-AI heuristics, plus the live regression / latency /
    error signals).

    Exit codes:

    * ``0`` - analyzer ran cleanly and no finding met `--severity-fail`.
    * ``2`` - at least one finding meets the configured severity floor.
    * ``1`` - runtime/configuration error.
    """
    from agentops.agent.analyzer import analyze
    from agentops.agent.config import load_agent_config
    from agentops.agent.findings import Severity
    from agentops.agent.history import append_analysis, build_record
    from agentops.agent.report import render_report
    from agentops.utils import telemetry
    import time as _time

    workspace = workspace.resolve()
    resolved_config = _resolve_agent_config_path(workspace, config_path)

    try:
        config = load_agent_config(resolved_config)
    except Exception as exc:
        typer.echo(f"Error loading agent config: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if lookback_days is not None:
        config = config.model_copy(update={"lookback_days": lookback_days})

    try:
        severity_floor = Severity(severity_fail.lower())
    except ValueError as exc:
        typer.echo(
            f"Error: invalid --severity-fail '{severity_fail}'. "
            "Use one of: info, warning, critical.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    telemetry.init_tracing()
    started_perf = _time.perf_counter()
    try:
        with telemetry.agent_analyze_span(
            workspace=str(workspace),
            lookback_days=config.lookback_days,
        ) as analyze_span:
            try:
                result = analyze(
                    workspace,
                    config,
                    categories=(
                        [c for c in categories.split(",") if c.strip()]
                        if categories
                        else None
                    ),
                    exclude_rules=(
                        [r for r in exclude_rules.split(",") if r.strip()]
                        if exclude_rules
                        else None
                    ),
                )
            except Exception as exc:  # pragma: no cover
                typer.echo(f"Error running analyzer: {exc}", err=True)
                raise typer.Exit(code=1) from exc

            duration_seconds = _time.perf_counter() - started_perf

            # Persist the analysis history (always - works without Azure).
            sources_enabled = _sources_enabled(config)
            record = build_record(
                result.findings,
                sources_enabled=sources_enabled,
                lookback_days=config.lookback_days,
                duration_seconds=duration_seconds,
            )
            try:
                history_file = append_analysis(workspace, record)
            except OSError as exc:  # pragma: no cover - best effort
                history_file = None
                log.debug("could not append agent history: %s", exc)

            telemetry.set_agent_analyze_result(
                analyze_span,
                findings_total=record.findings_total,
                by_severity=record.findings_by_severity,
                by_category=record.findings_by_category,
                max_severity=record.max_severity,
                sources_enabled=sources_enabled,
            )
    finally:
        telemetry.shutdown()

    out_path = out if out.is_absolute() else workspace / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(result), encoding="utf-8")

    typer.echo(f"Wrote {out_path}")
    if history_file is not None:
        typer.echo(f"Appended history: {history_file}")
    typer.echo(f"Findings: {len(result.findings)}")
    if result.max_severity is not None:
        typer.echo(f"Max severity: {result.max_severity.value}")

    if result.max_severity is not None and result.max_severity >= severity_floor:
        raise typer.Exit(code=2)


def _sources_enabled(config) -> list:
    """Return the list of source names that were enabled in agent.yaml."""
    enabled: list = []
    sources = getattr(config, "sources", None)
    if sources is None:
        return enabled
    for name in ("results_history", "azure_monitor", "foundry_control", "azure_resources"):
        source = getattr(sources, name, None)
        if source is None:
            continue
        if getattr(source, "enabled", True):
            enabled.append(name)
    return enabled


@agent_app.command("serve")
def cmd_agent_serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind host.")
    ] = "0.0.0.0",
    port: Annotated[
        int, typer.Option("--port", help="Bind port.")
    ] = 8080,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Project root for analysis."),
    ] = Path("."),
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to `agent.yaml` (default: `.agentops/agent.yaml`).",
        ),
    ] = None,
    no_verify: Annotated[
        bool,
        typer.Option(
            "--no-verify",
            help="Skip Copilot Extensions signature validation (dev only).",
        ),
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", help="Uvicorn worker count.")
    ] = 1,
) -> None:
    """Start the AgentOps doctor as a Copilot Extension HTTP server.

    Exposes ``POST /agents/messages`` (Copilot Extensions protocol),
    ``GET /healthz`` and ``GET /``. Requires the ``[agent]`` extra:

        pip install agentops-toolkit[agent]
    """
    try:
        import uvicorn
    except ImportError as exc:
        typer.echo(
            "Error: agent extras not installed. "
            "Run `pip install agentops-toolkit[agent]`.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    from agentops.agent.config import load_agent_config
    from agentops.agent.server.app import create_app

    workspace = workspace.resolve()
    resolved_config = _resolve_agent_config_path(workspace, config_path)

    try:
        config = load_agent_config(resolved_config)
    except Exception as exc:
        typer.echo(f"Error loading agent config: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    fastapi_app = create_app(
        workspace=workspace,
        config=config,
        verify_signature=not no_verify,
    )

    if no_verify:
        typer.echo(
            "WARNING: Copilot Extensions signature validation is disabled. "
            "Use only for local development."
        )

    uvicorn.run(fastapi_app, host=host, port=port, workers=workers)


@app.command("dashboard")
def cmd_dashboard(
    host: Annotated[
        str, typer.Option("--host", help="Bind host (default: 127.0.0.1).")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", help="Bind port (default: 8090).")
    ] = 8090,
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            "-w",
            help="Project root containing `.agentops/agent/history.jsonl`.",
        ),
    ] = Path("."),
) -> None:
    """Open the local AgentOps dashboard.

    Reads ``.agentops/results/*/results.json`` and
    ``.agentops/agent/history.jsonl`` (populated by ``agentops eval run``
    and ``agentops doctor``) and serves an interactive dark
    dashboard on http://127.0.0.1:8090. Read-only, single-page,
    auto-refreshes every 15s. Requires the ``[agent]`` extra::

        pip install agentops-toolkit[agent]

    No Azure resource needed for the local view. When
    ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` is set, the dashboard also
    pulls live data (invocations, error rate, p95 latency, token spend)
    from the Application Insights workspace attached to the Foundry
    project - auto-discovered, no extra configuration required.
    """
    try:
        import uvicorn
    except ImportError as exc:
        typer.echo(
            "Error: dashboard requires the [agent] extra. "
            "Run `pip install agentops-toolkit[agent]`.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    import threading
    import time as _time
    import webbrowser

    from agentops.agent.dashboard import create_app as create_dashboard_app

    workspace = workspace.resolve()
    fastapi_app = create_dashboard_app(workspace=workspace)
    url = f"http://{host}:{port}"

    typer.echo(f"AgentOps dashboard → {url}")
    typer.echo(f"workspace: {workspace}")
    typer.echo("Run `agentops doctor` in another terminal to populate doctor findings.")
    typer.echo("")
    typer.echo("Press Enter (or Ctrl+C) to stop the dashboard.")

    config = uvicorn.Config(
        fastapi_app, host=host, port=port, log_level="warning",
    )
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for uvicorn to actually bind before launching the browser so
    # the first GET does not race the server startup.
    for _ in range(40):  # up to ~2s
        if getattr(server, "started", False):
            break
        _time.sleep(0.05)
    try:
        webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001 - never fail dashboard on a browser launch issue
        pass

    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass

    typer.echo("Stopping dashboard…")
    server.should_exit = True
    server_thread.join(timeout=5)


def main() -> None:
    app()
