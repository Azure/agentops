"""Browse sub-commands: bundle list/show, run list/show/view."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

run_app = typer.Typer(help="Run history and inspection commands.")
bundle_app = typer.Typer(help="Bundle browsing commands.")


# ---------------------------------------------------------------------------
# bundle list / show
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# run list / show / view
# ---------------------------------------------------------------------------


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
    run_id: str = typer.Argument(help="Run ID (timestamp folder name or 'latest')."),
    entry: Annotated[
        int | None,
        typer.Option("--entry", help="Show detail for a specific row index."),
    ] = None,
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Workspace directory.",
    ),
) -> None:
    """View per-row metrics for an evaluation run."""
    from agentops.services.browse import view_run

    try:
        result = view_run(run_id=run_id, directory=directory, entry=entry)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    status = "PASS" if result.overall_passed else "FAIL"
    typer.echo(
        f"Run: {result.run_id} ({status})  "
        f"bundle={result.bundle_name}  dataset={result.dataset_name}"
    )

    if entry is not None:
        # Detail view for a single row
        row = result.rows[0]
        row_status = "PASS" if row.passed_all else "FAIL"
        typer.echo(f"\nRow {row.row_index}: {row_status}")
        typer.echo("")
        typer.echo("Scores:")
        for name, value in row.scores.items():
            typer.echo(f"  {name:<40} {value:.4f}")
        if row.threshold_results:
            typer.echo("")
            typer.echo("Thresholds:")
            for t in row.threshold_results:
                mark = "PASS" if t["passed"] else "FAIL"
                typer.echo(
                    f"  {t['evaluator']:<40} {t['criteria']} {t['expected']:<10} "
                    f"actual={t['actual']:<10} {mark}"
                )
    else:
        # Table view for all rows
        if not result.rows:
            typer.echo("\nNo per-row metrics available.")
            return

        # Collect metric names (excluding samples_evaluated)
        metric_names = [n for n in result.evaluator_names if n != "samples_evaluated"]

        # Header
        typer.echo("")
        header = f"{'Row':>4}  {'Pass':4}"
        for name in metric_names:
            short = name.replace("Evaluator", "")
            header += f"  {short:>10}"
        typer.echo(header)
        typer.echo("-" * len(header))

        # Rows
        for row in result.rows:
            row_status = "PASS" if row.passed_all else "FAIL"
            line = f"{row.row_index:>4}  {row_status:4}"
            for name in metric_names:
                val = row.scores.get(name)
                if val is not None:
                    line += f"  {val:>10.2f}"
                else:
                    line += f"  {'—':>10}"
            typer.echo(line)
