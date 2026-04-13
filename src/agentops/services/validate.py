"""Validation and inspection services for configs and datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentops.core.config_loader import (
    load_bundle_config,
    load_dataset_config,
    load_run_config,
)
from agentops.services._workspace import resolve_workspace


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding."""

    file: Path
    severity: str  # "error" | "warning"
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Result of a validation run."""

    passed: bool
    files_checked: int
    issues: List[ValidationIssue]


# ---------------------------------------------------------------------------
# Path resolution (mirrors runner.py logic)
# ---------------------------------------------------------------------------


def _resolve_path(path_value: Path, base_dir: Path) -> Path:
    """Resolve a relative path against a base directory."""
    if path_value.is_absolute():
        return path_value
    candidate = (base_dir / path_value).resolve()
    if candidate.exists():
        return candidate
    fallback = (Path.cwd() / path_value).resolve()
    if fallback.exists():
        return fallback
    return candidate


# ---------------------------------------------------------------------------
# config validate
# ---------------------------------------------------------------------------


def validate_config(
    config_path: Optional[Path] = None,
    directory: Path = Path("."),
) -> ValidationResult:
    """Validate the full configuration chain: run.yaml → bundle → dataset → data.

    Returns a ValidationResult with all issues found.
    """
    issues: List[ValidationIssue] = []
    files_checked = 0

    # Resolve run config path
    if config_path is None:
        workspace = resolve_workspace(directory)
        config_path = workspace / "run.yaml"

    config_path = config_path.resolve()

    # --- Validate run.yaml ---
    if not config_path.exists():
        issues.append(
            ValidationIssue(
                file=config_path,
                severity="error",
                message=f"Run config not found: {config_path}",
            )
        )
        return ValidationResult(passed=False, files_checked=0, issues=issues)

    files_checked += 1
    try:
        run_config = load_run_config(config_path)
    except (ValueError, Exception) as exc:
        issues.append(
            ValidationIssue(file=config_path, severity="error", message=str(exc))
        )
        return ValidationResult(
            passed=False, files_checked=files_checked, issues=issues
        )

    run_config_dir = config_path.parent

    # --- Validate bundle ---
    bundle_path = _resolve_path(run_config.bundle.path, run_config_dir)
    if not bundle_path.exists():
        issues.append(
            ValidationIssue(
                file=bundle_path,
                severity="error",
                message=f"Bundle file not found: {bundle_path}",
            )
        )
    else:
        files_checked += 1
        try:
            bundle_config = load_bundle_config(bundle_path)

            # Semantic checks
            enabled = [e for e in bundle_config.evaluators if e.enabled]
            if not enabled:
                issues.append(
                    ValidationIssue(
                        file=bundle_path,
                        severity="warning",
                        message="No evaluators are enabled",
                    )
                )

            evaluator_names = {e.name for e in bundle_config.evaluators}
            for threshold in bundle_config.thresholds:
                if threshold.evaluator not in evaluator_names:
                    issues.append(
                        ValidationIssue(
                            file=bundle_path,
                            severity="warning",
                            message=(
                                f"Threshold references unknown evaluator "
                                f"'{threshold.evaluator}'"
                            ),
                        )
                    )
        except (ValueError, Exception) as exc:
            issues.append(
                ValidationIssue(file=bundle_path, severity="error", message=str(exc))
            )

    # --- Validate dataset ---
    dataset_path = _resolve_path(run_config.dataset.path, run_config_dir)
    if not dataset_path.exists():
        issues.append(
            ValidationIssue(
                file=dataset_path,
                severity="error",
                message=f"Dataset config not found: {dataset_path}",
            )
        )
    else:
        files_checked += 1
        try:
            dataset_config = load_dataset_config(dataset_path)
            data_issues, data_files = _validate_dataset_data(
                dataset_config, dataset_path.parent
            )
            issues.extend(data_issues)
            files_checked += data_files
        except (ValueError, Exception) as exc:
            issues.append(
                ValidationIssue(file=dataset_path, severity="error", message=str(exc))
            )

    passed = not any(i.severity == "error" for i in issues)
    return ValidationResult(passed=passed, files_checked=files_checked, issues=issues)


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigShowResult:
    """Resolved configuration view."""

    run_config_path: Path
    bundle_path: Path
    bundle_name: str
    dataset_path: Path
    dataset_name: str
    data_path: Optional[Path]
    backend_type: str
    target: str
    model: Optional[str]
    agent_id: Optional[str]
    evaluators: List[Dict[str, Any]]
    thresholds: int
    data_rows: Optional[int]


def show_config(
    config_path: Optional[Path] = None,
    directory: Path = Path("."),
) -> ConfigShowResult:
    """Load and resolve all configuration to show the merged view."""
    if config_path is None:
        workspace = resolve_workspace(directory)
        config_path = workspace / "run.yaml"

    config_path = config_path.resolve()
    run_config = load_run_config(config_path)
    run_config_dir = config_path.parent

    bundle_path = _resolve_path(run_config.bundle.path, run_config_dir)
    dataset_path = _resolve_path(run_config.dataset.path, run_config_dir)

    bundle_config = load_bundle_config(bundle_path)
    dataset_config = load_dataset_config(dataset_path)

    # Resolve data file
    data_path = _resolve_path(Path(dataset_config.source.path), dataset_path.parent)
    data_rows = None
    if data_path.exists():
        try:
            lines = data_path.read_text(encoding="utf-8").strip().splitlines()
            data_rows = len(lines)
        except Exception:  # noqa: BLE001
            pass

    evaluators = [
        {
            "name": e.name,
            "source": e.source,
            "enabled": e.enabled,
        }
        for e in bundle_config.evaluators
    ]

    return ConfigShowResult(
        run_config_path=config_path,
        bundle_path=bundle_path,
        bundle_name=bundle_config.name,
        dataset_path=dataset_path,
        dataset_name=dataset_config.name,
        data_path=data_path if data_path.exists() else None,
        backend_type=run_config.backend.type,
        target=run_config.backend.target or "agent",
        model=run_config.backend.model,
        agent_id=run_config.backend.agent_id,
        evaluators=evaluators,
        thresholds=len(bundle_config.thresholds),
        data_rows=data_rows,
    )


# ---------------------------------------------------------------------------
# dataset validate
# ---------------------------------------------------------------------------


def validate_dataset(
    dataset_path: Path,
) -> ValidationResult:
    """Validate a dataset config and its JSONL data file."""
    issues: List[ValidationIssue] = []
    files_checked = 0

    dataset_path = dataset_path.resolve()
    if not dataset_path.exists():
        issues.append(
            ValidationIssue(
                file=dataset_path,
                severity="error",
                message=f"Dataset config not found: {dataset_path}",
            )
        )
        return ValidationResult(passed=False, files_checked=0, issues=issues)

    files_checked += 1
    try:
        dataset_config = load_dataset_config(dataset_path)
    except (ValueError, Exception) as exc:
        issues.append(
            ValidationIssue(file=dataset_path, severity="error", message=str(exc))
        )
        return ValidationResult(
            passed=False, files_checked=files_checked, issues=issues
        )

    data_issues, data_files = _validate_dataset_data(
        dataset_config, dataset_path.parent
    )
    issues.extend(data_issues)
    files_checked += data_files

    passed = not any(i.severity == "error" for i in issues)
    return ValidationResult(passed=passed, files_checked=files_checked, issues=issues)


def _validate_dataset_data(
    dataset_config, base_dir: Path
) -> tuple[List[ValidationIssue], int]:
    """Validate the JSONL data file referenced by a dataset config."""
    issues: List[ValidationIssue] = []
    files_checked = 0

    data_path = _resolve_path(Path(dataset_config.source.path), base_dir)

    if not data_path.exists():
        issues.append(
            ValidationIssue(
                file=data_path,
                severity="error",
                message=f"Data file not found: {data_path}",
            )
        )
        return issues, files_checked

    files_checked += 1
    input_field = dataset_config.format.input_field
    expected_field = dataset_config.format.expected_field

    try:
        lines = data_path.read_text(encoding="utf-8").strip().splitlines()
    except Exception as exc:
        issues.append(
            ValidationIssue(
                file=data_path,
                severity="error",
                message=f"Cannot read data file: {exc}",
            )
        )
        return issues, files_checked

    if not lines:
        issues.append(
            ValidationIssue(
                file=data_path,
                severity="warning",
                message="Data file is empty (0 rows)",
            )
        )
        return issues, files_checked

    for line_num, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(
                ValidationIssue(
                    file=data_path,
                    severity="error",
                    message=f"Line {line_num}: invalid JSON — {exc}",
                )
            )
            continue

        if not isinstance(row, dict):
            issues.append(
                ValidationIssue(
                    file=data_path,
                    severity="error",
                    message=f"Line {line_num}: expected JSON object, got {type(row).__name__}",
                )
            )
            continue

        if input_field not in row:
            issues.append(
                ValidationIssue(
                    file=data_path,
                    severity="error",
                    message=f"Line {line_num}: missing required field '{input_field}'",
                )
            )
        if expected_field not in row:
            issues.append(
                ValidationIssue(
                    file=data_path,
                    severity="error",
                    message=f"Line {line_num}: missing required field '{expected_field}'",
                )
            )

    return issues, files_checked


# ---------------------------------------------------------------------------
# dataset describe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetDescription:
    """Description of a dataset."""

    name: str
    path: Path
    data_path: Optional[Path]
    description: str
    source_type: str
    format_type: str
    input_field: str
    expected_field: str
    context_field: Optional[str]
    row_count: int
    fields: List[str]
    metadata: Dict[str, Any]


def describe_dataset(dataset_path: Path) -> DatasetDescription:
    """Load a dataset config and describe its contents."""
    dataset_path = dataset_path.resolve()
    dataset_config = load_dataset_config(dataset_path)

    data_path = _resolve_path(Path(dataset_config.source.path), dataset_path.parent)

    row_count = 0
    fields: List[str] = []

    if data_path.exists():
        try:
            lines = data_path.read_text(encoding="utf-8").strip().splitlines()
            row_count = len(lines)
            # Collect unique fields from all rows
            all_fields: dict[str, None] = {}
            for line in lines:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        for key in row:
                            all_fields[key] = None
                except json.JSONDecodeError:
                    pass
            fields = list(all_fields.keys())
        except Exception:  # noqa: BLE001
            pass

    return DatasetDescription(
        name=dataset_config.name,
        path=dataset_path,
        data_path=data_path if data_path.exists() else None,
        description=dataset_config.description or "",
        source_type=dataset_config.source.type,
        format_type=dataset_config.format.type,
        input_field=dataset_config.format.input_field,
        expected_field=dataset_config.format.expected_field,
        context_field=dataset_config.format.context_field,
        row_count=row_count,
        fields=fields,
        metadata=dataset_config.metadata,
    )
