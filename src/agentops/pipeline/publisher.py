"""Optional Foundry publishing for the AgentOps pipeline.

This module is invoked from :mod:`agentops.pipeline.orchestrator` only when
``publish: foundry`` is set in ``agentops.yaml``. It uploads the same metrics
that AgentOps already computed locally into the **New Foundry Evaluations**
panel, using the public ``_log_metrics_and_instance_results_onedp`` helper
from ``azure.ai.evaluation``.

The pipeline never re-runs the agent here. Local invocations + local
evaluators stay the canonical source of truth; this is just a publish hop.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agentops.core.results import RunResult

logger = logging.getLogger("agentops.pipeline.publisher")


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a successful Foundry publish."""

    studio_url: str
    evaluation_name: str


def publish_to_foundry(
    result: RunResult,
    *,
    project_endpoint: Optional[str] = None,
    evaluation_name: Optional[str] = None,
) -> PublishResult:
    """Publish ``result`` to the Foundry Evaluations panel.

    Parameters
    ----------
    result:
        The fully populated ``RunResult`` produced by the pipeline.
    project_endpoint:
        Foundry project URL. When ``None``, the function falls back to the
        ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` environment variable.
    evaluation_name:
        Display name for the run in the Foundry panel. Defaults to a unique
        ``agentops-eval-<short-uuid>``.

    Returns
    -------
    PublishResult
        ``studio_url`` is the deep link rendered on the Foundry portal.

    Raises
    ------
    ImportError
        ``azure-ai-evaluation`` and ``pandas`` are not installed.
    ValueError
        Project endpoint is missing or no rows are publishable.
    """
    endpoint = project_endpoint or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise ValueError(
            "publish: foundry requires either 'project_endpoint' in "
            "agentops.yaml or the AZURE_AI_FOUNDRY_PROJECT_ENDPOINT env var."
        )

    try:
        import pandas as pd  # noqa: WPS433
        from azure.ai.evaluation._evaluate._utils import (  # noqa: WPS433
            _log_metrics_and_instance_results_onedp,
        )
    except ImportError as exc:  # pragma: no cover - exercised only at runtime
        raise ImportError(
            "Foundry publish requires 'azure-ai-evaluation' and 'pandas'. "
            "Install with: pip install azure-ai-evaluation pandas"
        ) from exc

    instance_rows = _build_instance_rows(result)
    if not instance_rows:
        raise ValueError("Foundry publish has no content rows to submit.")

    metrics = dict(result.aggregate_metrics)
    name_map: Dict[str, str] = {key: key for key in metrics.keys()}
    eval_name = evaluation_name or f"agentops-eval-{uuid.uuid4().hex[:8]}"

    instance_results_df = pd.DataFrame(instance_rows)
    studio_url = _log_metrics_and_instance_results_onedp(
        metrics=metrics,
        instance_results=instance_results_df,
        project_url=endpoint,
        evaluation_name=eval_name,
        name_map=name_map,
    )
    if not studio_url:
        raise RuntimeError(
            "Foundry publish completed but the studio URL was empty."
        )

    logger.info("Foundry publish completed: %s", studio_url)
    return PublishResult(studio_url=studio_url, evaluation_name=eval_name)


def _build_instance_rows(result: RunResult) -> List[Dict[str, Any]]:
    """Project ``RunResult.rows`` into the OneDP instance-result schema."""
    rows: List[Dict[str, Any]] = []
    for row in result.rows:
        payload: Dict[str, Any] = {
            "line_number": row.row_index,
            "input": row.input,
            "response": row.response,
            "ground_truth": row.expected or "",
        }
        for metric in row.metrics:
            if metric.value is not None:
                payload[metric.name] = metric.value
        rows.append(payload)
    return rows
