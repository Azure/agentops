"""Foundry v2 cloud evaluation publishing service.

Publishes already computed AgentOps backend metrics to the
**New Foundry Evaluations** panel using the same 3-step OneDP upload flow:

1. ``create_evaluation_result`` — uploads ``instance_results.jsonl`` to blob
2. ``start_evaluation_run``    — creates the run entry with portal-required
   properties (``_azureml.evaluate_artifacts``, ``_azureml.evaluation_sdk_name``,
   name-map entries, ``runType``)
3. ``update_evaluation_run``   — marks the run ``Completed`` and links it to the
   result artifact via ``evaluationResultId``
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from agentops.core.config_loader import load_dataset_config
from agentops.core.models import BackendConfig


@dataclass(frozen=True)
class FoundryEvalPublishResult:
    """Result of publishing an evaluation to the Foundry v2 panel."""

    studio_url: str
    evaluation_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROW_LINE_PATTERN = re.compile(
    r"^row=(?P<row>\d+)\s+exact_match=(?P<exact>true|false)\s+expected=(?P<expected>.+?)\s+prediction=(?P<prediction>.+)$"
)


def _resolve_dataset_source_path(dataset_config_path: Path, source_path: Path) -> Path:
    if source_path.is_absolute():
        return source_path

    candidate = (dataset_config_path.parent / source_path).resolve()
    if candidate.exists():
        return candidate

    fallback = (Path.cwd() / source_path).resolve()
    if fallback.exists():
        return fallback

    return candidate


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("Dataset JSONL rows must be objects")
        rows.append(payload)
    return rows


def _parse_output_rows(stdout_path: Path) -> Dict[int, Dict[str, Any]]:
    parsed: Dict[int, Dict[str, Any]] = {}
    if not stdout_path.exists():
        return parsed

    for raw_line in stdout_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _ROW_LINE_PATTERN.match(line)
        if not match:
            continue

        row_number = int(match.group("row"))
        expected = ast.literal_eval(match.group("expected"))
        prediction = ast.literal_eval(match.group("prediction"))
        exact_match = match.group("exact") == "true"

        parsed[row_number] = {
            "expected": str(expected),
            "prediction": str(prediction),
            "exact_match": exact_match,
        }

    return parsed


def _parse_project_identity(project_endpoint: str) -> tuple[str, str]:
    parsed = urlparse(project_endpoint)
    host = parsed.netloc
    match = re.search(r"^([^.]+)\.services\.ai\.azure\.com$", host)
    if not match:
        raise ValueError(f"Invalid Foundry project endpoint host: {host}")
    account_name = match.group(1)

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[0] != "api" or path_parts[1] != "projects":
        raise ValueError(
            "Foundry project endpoint must look like "
            "https://<account>.services.ai.azure.com/api/projects/<project>"
        )
    project_name = path_parts[2]
    return account_name, project_name


def _load_backend_metrics_payload(path: Path) -> tuple[Dict[str, float], Dict[int, Dict[str, float]]]:
    if not path.exists():
        raise FileNotFoundError(f"Backend metrics file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid backend metrics payload: expected JSON object")

    metrics_entries = payload.get("metrics", [])
    if not isinstance(metrics_entries, list):
        raise ValueError("Invalid backend metrics payload: 'metrics' must be a list")

    metrics: Dict[str, float] = {}
    for item in metrics_entries:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[name] = float(value)

    row_metrics_entries = payload.get("row_metrics", [])
    if not isinstance(row_metrics_entries, list):
        raise ValueError("Invalid backend metrics payload: 'row_metrics' must be a list")

    row_metrics: Dict[int, Dict[str, float]] = {}
    for row in row_metrics_entries:
        if not isinstance(row, dict):
            continue
        row_index = row.get("row_index")
        raw_metrics = row.get("metrics", [])
        if not isinstance(row_index, int) or row_index <= 0 or not isinstance(raw_metrics, list):
            continue

        row_values: Dict[str, float] = {}
        for metric in raw_metrics:
            if not isinstance(metric, dict):
                continue
            name = metric.get("name")
            value = metric.get("value")
            if isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
                row_values[name] = float(value)
        row_metrics[row_index] = row_values

    if not metrics:
        raise ValueError("Backend metrics payload does not contain numeric metrics")

    return metrics, row_metrics


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def publish_foundry_evaluation(
    *,
    backend_config: BackendConfig,
    dataset_config_path: Path,
    backend_stdout_path: Path,
    evaluation_name: str | None = None,
) -> FoundryEvalPublishResult:
    """Publish evaluation results to the New Foundry Evaluations panel.

    Publishes existing AgentOps backend metrics so Foundry displays
    the same evaluator outputs seen in `results.json` and `report.md`.
    """
    try:
        import pandas as pd  # noqa: WPS433
        from azure.ai.evaluation._evaluate._utils import (  # noqa: WPS433
            _log_metrics_and_instance_results_onedp,
        )
    except ImportError as exc:
        raise ImportError(
            "Foundry evaluation publish requires 'azure-ai-evaluation' and 'pandas'. "
            "Install with: pip install azure-ai-evaluation pandas"
        ) from exc

    # --- resolve project endpoint ----------------------------------------
    project_endpoint_env = backend_config.project_endpoint_env or "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
    project_endpoint = backend_config.project_endpoint or os.getenv(project_endpoint_env)
    if not project_endpoint:
        raise ValueError(
            "Foundry evaluation publish requires backend.project_endpoint or "
            f"environment variable {project_endpoint_env}"
        )

    _parse_project_identity(project_endpoint)  # validate format

    # --- build per-row JSONL from backend outputs ------------------------
    dataset_config = load_dataset_config(dataset_config_path)
    dataset_source_path = _resolve_dataset_source_path(
        dataset_config_path, dataset_config.source.path
    )
    dataset_rows = _load_jsonl(dataset_source_path)
    parsed_rows = _parse_output_rows(backend_stdout_path)
    backend_metrics_path = backend_stdout_path.parent / "backend_metrics.json"
    metrics, row_metrics_by_index = _load_backend_metrics_payload(backend_metrics_path)

    if not parsed_rows:
        raise ValueError(
            "Foundry evaluation publish could not parse backend stdout rows"
        )

    input_field = dataset_config.format.input_field
    instance_rows: List[Dict[str, Any]] = []
    for index, row in enumerate(dataset_rows, start=1):
        row_result = parsed_rows.get(index)
        if row_result is None:
            continue

        instance_payload: Dict[str, Any] = {
            "line_number": index - 1,
            "input": str(row.get(input_field, "")),
            "response": row_result["prediction"],
            "ground_truth": row_result["expected"],
        }
        for metric_name, metric_value in row_metrics_by_index.get(index, {}).items():
            instance_payload[metric_name] = metric_value

        instance_rows.append(instance_payload)

    if not instance_rows:
        raise ValueError(
            "Foundry evaluation publish has no content rows to submit"
        )

    eval_name = evaluation_name or f"agentops-eval-{uuid.uuid4().hex[:8]}"
    logger = logging.getLogger("agentops.foundry_evals")
    logger.info("Publishing evaluation to Foundry: %s", eval_name)

    # Build the evaluator name map (maps internal metric name -> display name)
    name_map: Dict[str, str] = {
        metric_name: metric_name
        for metric_name in metrics.keys()
    }

    instance_results_df = pd.DataFrame(instance_rows)
    studio_url = _log_metrics_and_instance_results_onedp(
        metrics=metrics,
        instance_results=instance_results_df,
        project_url=project_endpoint,
        evaluation_name=eval_name,
        name_map=name_map,
    )

    if not studio_url:
        raise RuntimeError(
            "Foundry evaluation upload completed but studio URL is missing."
        )

    logger.info("Foundry publish completed successfully")
    logger.info("Evaluation published: %s", studio_url)
    return FoundryEvalPublishResult(
        studio_url=studio_url,
        evaluation_name=eval_name,
    )
