"""Cloud-side publisher: submit a run to the New Foundry Evaluations panel.

Unlike :mod:`agentops.pipeline.publisher` (which uploads metrics that
AgentOps already computed locally to the *Classic* Foundry Evaluations
panel via OneDP), this module asks **Foundry to execute the agent and the
evaluators server-side** through the OpenAI Evals API.

The flow:

1. Build an :class:`azure.ai.projects.AIProjectClient` from the configured
   project endpoint using ``DefaultAzureCredential``.
2. Get the OpenAI client via ``project_client.get_openai_client()``. We do
   **not** pass ``api_version`` — the SDK picks the correct one (passing
   one explicitly has historically caused 404s in this codebase).
3. Upload the JSONL dataset as an OpenAI file with ``purpose="evals"``.
4. Create the eval definition with ``client.evals.create(...)``, mapping
   each AgentOps evaluator preset onto an ``azure_ai_evaluator`` testing
   criterion.
5. Create the run with ``client.evals.runs.create(...)``, pointing at the
   uploaded file and using ``azure_ai_target_completions`` with an
   ``agent_reference`` so Foundry invokes the agent itself.
6. Poll until the run terminates, then return identifiers + the portal URL.

This module never re-runs the agent locally and never invokes evaluators
locally; that work happens inside Foundry. The local ``results.json``
(produced before this hop) remains the canonical record from AgentOps's
point of view.

Limitations (documented in the YAML schema docstring as well):

* Only ``foundry_prompt`` agents (``name:version``) are supported. HTTP
  endpoints, local adapters, and direct model deployments are rejected.
* Only builtin evaluators that map cleanly onto ``azure_ai_evaluator``
  testing criteria are supported. Custom evaluators are skipped with a
  warning.
* Latency reported by the New Foundry view is Foundry-to-Foundry, not the
  client-perceived latency captured locally.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agentops.core.results import RunResult

logger = logging.getLogger("agentops.pipeline.cloud_publisher")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudPublishResult:
    """Outcome of a cloud (New Foundry) publish."""

    eval_id: str
    run_id: str
    status: str
    report_url: Optional[str]
    evaluation_name: str


# Map AgentOps evaluator class names to the OpenAI Evals API evaluator
# names that ``azure_ai_evaluator`` recognises. Any preset whose
# ``class_name`` is not in this map is skipped (with a warning) when
# building testing criteria.
_AZURE_AI_EVALUATOR_NAMES: Dict[str, str] = {
    "CoherenceEvaluator": "builtin.coherence",
    "FluencyEvaluator": "builtin.fluency",
    "SimilarityEvaluator": "builtin.similarity",
    "F1ScoreEvaluator": "builtin.f1_score",
    "RelevanceEvaluator": "builtin.relevance",
    "GroundednessEvaluator": "builtin.groundedness",
    "RetrievalEvaluator": "builtin.retrieval",
    "ResponseCompletenessEvaluator": "builtin.response_completeness",
    "ToolCallAccuracyEvaluator": "builtin.tool_call_accuracy",
    "IntentResolutionEvaluator": "builtin.intent_resolution",
    "TaskAdherenceEvaluator": "builtin.task_adherence",
}

_CLOUD_EVALUATORS_REQUIRING_DEPLOYMENT = {
    "CoherenceEvaluator",
    "FluencyEvaluator",
    "SimilarityEvaluator",
    "RelevanceEvaluator",
    "GroundednessEvaluator",
    "RetrievalEvaluator",
    "ResponseCompletenessEvaluator",
    "ToolCallAccuracyEvaluator",
    "IntentResolutionEvaluator",
    "TaskAdherenceEvaluator",
}

_CLOUD_PLACEHOLDERS = {
    "$prompt": "{{item.input}}",
    "$prediction": "{{sample.output_text}}",
    "$expected": "{{item.expected}}",
    "$context": "{{item.context}}",
    "$tool_calls": "{{item.tool_calls}}",
    "$tool_definitions": "{{item.tool_definitions}}",
}


_DEFAULT_POLL_INTERVAL_SECONDS = 5.0
_DEFAULT_MAX_POLL_ATTEMPTS = 120  # 10 minutes at 5s intervals
_TERMINAL_STATUSES = {"completed", "failed", "canceled", "cancelled"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def publish_to_foundry_cloud(
    result: RunResult,
    *,
    dataset_path: Path,
    project_endpoint: str,
    evaluation_name: Optional[str] = None,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    max_poll_attempts: int = _DEFAULT_MAX_POLL_ATTEMPTS,
    progress: Optional[Callable[[str], None]] = None,
) -> CloudPublishResult:
    """Submit ``result``'s target to Foundry for server-side evaluation.

    Parameters
    ----------
    result:
        Local run result. Used to derive the agent reference and the list
        of evaluator presets that should map onto ``azure_ai_evaluator``
        testing criteria.
    dataset_path:
        Path to the JSONL dataset to upload. Must already exist.
    project_endpoint:
        Foundry project endpoint URL (e.g.
        ``https://contoso.services.ai.azure.com/api/projects/p``).
    evaluation_name:
        Optional display name. Defaults to ``agentops-cloud-<short-uuid>``.
    poll_interval_seconds, max_poll_attempts:
        Control polling cadence and bound. The default budget is
        ~10 minutes.
    progress:
        Optional callback invoked with one-line status updates. The
        orchestrator wires this to the same channel that prints per-row
        progress so the user sees what is happening during the long
        cloud round-trip.

    Raises
    ------
    ImportError
        ``azure-ai-projects`` / ``azure-identity`` are not installed.
    ValueError
        Target is not a Foundry agent or the dataset is missing.
    RuntimeError
        Polling timed out or the run terminated with a non-completed
        status.
    """
    progress = progress or (lambda _msg: None)

    if result.target.kind != "foundry_prompt":
        raise ValueError(
            "publish: foundry_cloud only supports Foundry agents declared "
            "as 'name:version' (foundry_prompt targets). Got "
            f"target.kind={result.target.kind!r}."
        )
    if not dataset_path.exists():
        raise ValueError(f"dataset file not found: {dataset_path}")

    agent_name = result.target.name
    agent_version = result.target.version
    if not agent_name or not agent_version:
        raise ValueError(
            "Cloud publish requires a fully qualified 'name:version' agent "
            f"reference; got name={agent_name!r} version={agent_version!r}"
        )

    try:
        from azure.ai.projects import AIProjectClient  # noqa: WPS433
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - exercised only at runtime
        raise ImportError(
            "publish: foundry_cloud requires 'azure-ai-projects' and "
            "'azure-identity'. Install with:\n"
            "  pip install azure-ai-projects azure-identity"
        ) from exc

    credential = DefaultAzureCredential(
        exclude_developer_cli_credential=True,
    )
    project_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
    )

    # NB: do not pass api_version — the SDK chooses the right one. Passing
    # an explicit version has historically caused 404s in this codebase.
    openai_client = project_client.get_openai_client()

    eval_name = evaluation_name or f"agentops-cloud-{uuid.uuid4().hex[:8]}"
    testing_criteria = _build_testing_criteria(result)
    if not testing_criteria:
        raise ValueError(
            "no AgentOps evaluators map onto azure_ai_evaluator testing "
            "criteria; nothing to evaluate server-side."
        )

    progress(f"cloud publish: preparing run '{eval_name}'")

    file_id = _upload_dataset(openai_client, dataset_path, progress=progress)
    item_schema = _build_item_schema(dataset_path)

    progress(
        f"cloud publish: creating eval ({len(testing_criteria)} criteria, "
        f"item_schema fields: {sorted(item_schema['properties'].keys())})"
    )
    eval_obj = openai_client.evals.create(
        name=eval_name,
        data_source_config={
            "type": "custom",
            "item_schema": item_schema,
            "include_sample_schema": True,
        },
        testing_criteria=testing_criteria,  # type: ignore[arg-type]
    )
    eval_id = eval_obj.id

    progress(
        f"cloud publish: starting run for agent {agent_name}:{agent_version}"
    )
    run_obj = openai_client.evals.runs.create(
        eval_id=eval_id,
        name=f"{eval_name}-run",
        data_source={  # type: ignore[arg-type]
            "type": "azure_ai_target_completions",
            "source": {
                "type": "file_id",
                "id": file_id,
            },
            "input_messages": {
                "type": "template",
                "template": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": {
                            "type": "input_text",
                            "text": "{{item.input}}",
                        },
                    }
                ],
            },
            "target": {
                "type": "azure_ai_agent",
                "name": agent_name,
                "version": agent_version,
            },
        },
    )
    run_id = run_obj.id

    progress(
        f"cloud publish: polling run {run_id} (interval "
        f"{poll_interval_seconds:g}s, max {max_poll_attempts} attempts)"
    )
    final_run = _poll_until_terminal(
        openai_client,
        eval_id=eval_id,
        run_id=run_id,
        interval_seconds=poll_interval_seconds,
        max_attempts=max_poll_attempts,
        progress=progress,
    )

    status = getattr(final_run, "status", "unknown")
    report_url = _extract_report_url(final_run)

    if status != "completed":
        raise RuntimeError(
            f"cloud evaluation run {run_id} terminated with status "
            f"{status!r}; see {report_url or 'the Foundry portal'}."
        )

    progress(f"cloud publish: done. status={status}")
    return CloudPublishResult(
        eval_id=eval_id,
        run_id=run_id,
        status=status,
        report_url=report_url,
        evaluation_name=eval_name,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _upload_dataset(
    openai_client: Any,
    dataset_path: Path,
    *,
    progress: Callable[[str], None],
) -> str:
    """Upload the dataset as an OpenAI file (purpose='evals')."""
    progress(f"cloud publish: uploading {dataset_path.name}")
    with dataset_path.open("rb") as handle:
        uploaded = openai_client.files.create(
            file=handle,
            purpose="evals",
        )
    file_id = uploaded.id
    progress(f"cloud publish: uploaded as file_id={file_id}")
    return file_id


def _build_testing_criteria(result: RunResult) -> List[Dict[str, Any]]:
    """Map evaluator class names from ``result`` onto Azure AI evaluators.

    Prefer ``result.evaluators`` because it records the evaluator set selected
    for the run even when every local invocation failed and no aggregate
    metrics were produced. Fall back to aggregate metric keys for compatibility
    with older result payloads.
    """
    # Lazy import to avoid pulling evaluators into modules that don't
    # need them.
    from agentops.core.evaluators import CATALOG

    evaluator_deployment = _evaluator_deployment_name()

    # ``CATALOG`` is keyed by preset.name (== class name); ``aggregate_metrics``
    # is keyed by preset.score_key. Build a one-shot reverse index for older
    # result payloads or synthesized tests that only carry metric keys.
    by_score_key = {p.score_key: p for p in CATALOG.values()}
    presets = [CATALOG[name] for name in result.evaluators if name in CATALOG]
    if not presets:
        presets = [
            preset
            for metric_name in result.aggregate_metrics.keys()
            if (preset := by_score_key.get(metric_name)) is not None
        ]

    criteria: List[Dict[str, Any]] = []
    seen: set = set()
    for preset in presets:
        # Latency is computed locally; Foundry has its own server-side view.
        if "runtime" in preset.categories:
            continue
        azure_name = _AZURE_AI_EVALUATOR_NAMES.get(preset.class_name)
        if not azure_name:
            logger.warning(
                "no azure_ai_evaluator mapping for %s; skipping in cloud run",
                preset.class_name,
            )
            continue
        if azure_name in seen:
            continue
        seen.add(azure_name)
        criterion: Dict[str, Any] = {
            "type": "azure_ai_evaluator",
            "name": preset.score_key,
            "evaluator_name": azure_name,
            "data_mapping": _build_cloud_data_mapping(preset),
        }
        if preset.class_name in _CLOUD_EVALUATORS_REQUIRING_DEPLOYMENT:
            if not evaluator_deployment:
                raise ValueError(
                    "publish: foundry_cloud requires AZURE_OPENAI_DEPLOYMENT "
                    "or AZURE_AI_MODEL_DEPLOYMENT_NAME for Azure AI "
                    f"evaluator {preset.class_name}."
                )
            criterion["initialization_parameters"] = {
                "deployment_name": evaluator_deployment,
            }
        criteria.append(criterion)
    return criteria


def _evaluator_deployment_name() -> Optional[str]:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )


def _build_cloud_data_mapping(preset: Any) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for field, placeholder in preset.input_mapping.items():
        if placeholder == "$prediction" and getattr(preset, "needs_conversation", False):
            mapping[field] = "{{sample.output_items}}"
            continue
        mapped = _CLOUD_PLACEHOLDERS.get(placeholder)
        if mapped:
            mapping[field] = mapped
    return mapping


def _build_item_schema(dataset_path: Path) -> Dict[str, Any]:
    """Inspect the first dataset row to derive a JSON schema.

    Foundry's Evals API requires an ``item_schema`` declaring the shape of
    each row. We read the first non-empty line of the JSONL file and
    advertise every top-level key as a string property; this is permissive
    enough for typical AgentOps datasets (input, expected, context,
    tool_calls, tool_definitions).
    """
    properties: Dict[str, Dict[str, str]] = {}
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                for key in row.keys():
                    properties[str(key)] = {"type": "string"}
            break
    if not properties:
        # Fall back to a single 'input' field so eval creation does not
        # blow up on an empty dataset.
        properties["input"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }


def _poll_until_terminal(
    openai_client: Any,
    *,
    eval_id: str,
    run_id: str,
    interval_seconds: float,
    max_attempts: int,
    progress: Callable[[str], None],
) -> Any:
    """Poll ``runs.retrieve`` until the run reaches a terminal status."""
    last_status: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        run = openai_client.evals.runs.retrieve(eval_id=eval_id, run_id=run_id)
        status = getattr(run, "status", "unknown")
        if status != last_status:
            progress(
                f"cloud publish: run status -> {status} "
                f"(attempt {attempt}/{max_attempts})"
            )
            last_status = status
        if status in _TERMINAL_STATUSES:
            return run
        time.sleep(interval_seconds)
    raise RuntimeError(
        f"cloud evaluation run {run_id} did not finish within "
        f"{max_attempts} polls of {interval_seconds:g}s "
        f"(last status: {last_status!r})."
    )


def _extract_report_url(run: Any) -> Optional[str]:
    """Best-effort extraction of the portal URL from a run object."""
    for attr in ("report_url", "reportUrl"):
        value = getattr(run, attr, None)
        if isinstance(value, str) and value:
            return value
    metadata = getattr(run, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("report_url", "reportUrl"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    return None
