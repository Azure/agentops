"""Browse services for listing and inspecting bundles and runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentops.core.config_loader import load_bundle_config
from agentops.core.models import RunResult
from agentops.services._workspace import resolve_workspace


# ---------------------------------------------------------------------------
# Bundle browsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleSummary:
    """Summary info for a single bundle."""

    name: str
    path: Path
    description: str
    evaluators: List[str]
    thresholds: int


@dataclass(frozen=True)
class BundleListResult:
    """Result of listing bundles."""

    bundles: List[BundleSummary]
    bundles_dir: Path


def list_bundles(directory: Path = Path(".")) -> BundleListResult:
    """List all bundle YAML files in the workspace."""
    workspace = resolve_workspace(directory)
    bundles_dir = workspace / "bundles"

    if not bundles_dir.is_dir():
        return BundleListResult(bundles=[], bundles_dir=bundles_dir)

    summaries: List[BundleSummary] = []
    for yaml_file in sorted(bundles_dir.glob("*.yaml")):
        try:
            bundle = load_bundle_config(yaml_file)
            enabled = [e.name for e in bundle.evaluators if e.enabled]
            summaries.append(
                BundleSummary(
                    name=bundle.name,
                    path=yaml_file,
                    description=bundle.description or "",
                    evaluators=enabled,
                    thresholds=len(bundle.thresholds),
                )
            )
        except Exception:  # noqa: BLE001
            # Skip malformed bundles — still list them with minimal info
            summaries.append(
                BundleSummary(
                    name=yaml_file.stem,
                    path=yaml_file,
                    description="(error loading bundle)",
                    evaluators=[],
                    thresholds=0,
                )
            )

    return BundleListResult(bundles=summaries, bundles_dir=bundles_dir)


@dataclass(frozen=True)
class BundleDetail:
    """Full detail of a single bundle."""

    name: str
    path: Path
    description: str
    evaluators: List[Dict[str, Any]]
    thresholds: List[Dict[str, Any]]
    metadata: Dict[str, Any]


def show_bundle(bundle_name: str, directory: Path = Path(".")) -> BundleDetail:
    """Load and return full details of a bundle by name."""
    workspace = resolve_workspace(directory)
    bundles_dir = workspace / "bundles"

    # Try exact filename first, then search by bundle name
    candidates = [
        bundles_dir / f"{bundle_name}.yaml",
        bundles_dir / f"{bundle_name}",
    ]

    bundle_path: Optional[Path] = None
    for candidate in candidates:
        if candidate.is_file():
            bundle_path = candidate
            break

    # Search by bundle name field if not found by filename
    if bundle_path is None and bundles_dir.is_dir():
        for yaml_file in bundles_dir.glob("*.yaml"):
            try:
                bundle = load_bundle_config(yaml_file)
                if bundle.name == bundle_name:
                    bundle_path = yaml_file
                    break
            except Exception:  # noqa: BLE001
                continue

    if bundle_path is None:
        raise FileNotFoundError(
            f"Bundle '{bundle_name}' not found in {bundles_dir}. "
            f"Available bundles: {', '.join(f.stem for f in bundles_dir.glob('*.yaml'))}"
        )

    bundle = load_bundle_config(bundle_path)
    return BundleDetail(
        name=bundle.name,
        path=bundle_path,
        description=bundle.description or "",
        evaluators=[
            {
                "name": e.name,
                "source": e.source,
                "enabled": e.enabled,
            }
            for e in bundle.evaluators
        ],
        thresholds=[
            {
                "evaluator": t.evaluator,
                "criteria": t.criteria,
                "value": t.value,
            }
            for t in bundle.thresholds
        ],
        metadata=bundle.metadata,
    )


# ---------------------------------------------------------------------------
# Run browsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    """Summary info for a single past run."""

    run_id: str
    path: Path
    bundle_name: str
    dataset_name: str
    status: str
    started_at: str
    duration_seconds: float
    metrics_count: int
    overall_passed: bool


@dataclass(frozen=True)
class RunListResult:
    """Result of listing runs."""

    runs: List[RunSummary]
    results_dir: Path


def list_runs(directory: Path = Path(".")) -> RunListResult:
    """List all past evaluation runs in the workspace."""
    workspace = resolve_workspace(directory)
    results_dir = workspace / "results"

    if not results_dir.is_dir():
        return RunListResult(runs=[], results_dir=results_dir)

    summaries: List[RunSummary] = []
    for run_dir in sorted(results_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        if run_dir.name == "latest":
            continue  # Skip the symlink/copy

        results_file = run_dir / "results.json"
        if not results_file.exists():
            continue

        try:
            data = json.loads(results_file.read_text(encoding="utf-8"))
            result = RunResult.model_validate(data)
            summaries.append(
                RunSummary(
                    run_id=run_dir.name,
                    path=run_dir,
                    bundle_name=result.bundle.name,
                    dataset_name=result.dataset.name,
                    status=result.status,
                    started_at=result.execution.started_at,
                    duration_seconds=result.execution.duration_seconds,
                    metrics_count=len(result.metrics),
                    overall_passed=result.summary.overall_passed,
                )
            )
        except Exception:  # noqa: BLE001
            # Include the run with minimal info if results.json is malformed
            summaries.append(
                RunSummary(
                    run_id=run_dir.name,
                    path=run_dir,
                    bundle_name="(error)",
                    dataset_name="(error)",
                    status="error",
                    started_at="",
                    duration_seconds=0,
                    metrics_count=0,
                    overall_passed=False,
                )
            )

    return RunListResult(runs=summaries, results_dir=results_dir)


@dataclass(frozen=True)
class RunDetail:
    """Full detail of a single past run."""

    run_id: str
    path: Path
    bundle_name: str
    dataset_name: str
    status: str
    backend: str
    started_at: str
    finished_at: str
    duration_seconds: float
    overall_passed: bool
    metrics: List[Dict[str, Any]]
    thresholds: List[Dict[str, Any]]
    items_total: int
    items_passed: int
    report_path: Optional[Path]
    foundry_url: Optional[str]


def show_run(run_id: str, directory: Path = Path(".")) -> RunDetail:
    """Load and return full details of a past run."""
    workspace = resolve_workspace(directory)
    results_dir = workspace / "results"

    run_dir = (results_dir / run_id).resolve()
    if not run_dir.is_dir():
        available = [
            d.name
            for d in sorted(results_dir.iterdir(), reverse=True)
            if d.is_dir() and d.name != "latest" and (d / "results.json").exists()
        ]
        hint = ", ".join(available[:5]) if available else "(none)"
        raise FileNotFoundError(
            f"Run '{run_id}' not found in {results_dir}. Recent runs: {hint}"
        )

    results_file = run_dir / "results.json"
    if not results_file.exists():
        raise FileNotFoundError(f"No results.json in {run_dir}")

    data = json.loads(results_file.read_text(encoding="utf-8"))
    result = RunResult.model_validate(data)

    report_path = run_dir / "report.md"
    if not report_path.exists():
        report_path = None

    foundry_url = None
    if result.artifacts and result.artifacts.foundry_eval_studio_url:
        foundry_url = result.artifacts.foundry_eval_studio_url

    items_total = result.summary.thresholds_count
    items_passed = result.summary.thresholds_passed
    # Use item_evaluations for more accurate counts
    if result.item_evaluations:
        items_total = len(result.item_evaluations)
        items_passed = sum(1 for i in result.item_evaluations if i.passed_all)

    return RunDetail(
        run_id=run_id,
        path=run_dir,
        bundle_name=result.bundle.name,
        dataset_name=result.dataset.name,
        status=result.status,
        backend=result.execution.backend,
        started_at=result.execution.started_at,
        finished_at=result.execution.finished_at,
        duration_seconds=result.execution.duration_seconds,
        overall_passed=result.summary.overall_passed,
        metrics=[{"name": m.name, "value": m.value} for m in result.metrics],
        thresholds=[
            {
                "evaluator": t.evaluator,
                "criteria": t.criteria,
                "expected": t.expected,
                "actual": t.actual,
                "passed": t.passed,
            }
            for t in result.thresholds
        ],
        items_total=items_total,
        items_passed=items_passed,
        report_path=report_path,
        foundry_url=foundry_url,
    )
