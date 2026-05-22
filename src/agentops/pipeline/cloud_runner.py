"""Cloud-side publisher: submit a run to the New Foundry Evaluations panel.

Unlike :mod:`agentops.pipeline.publisher` (which uploads metrics that
AgentOps already computed locally to the *Classic* Foundry Evaluations
panel via OneDP), this module asks **Foundry to execute the agent and the
evaluators server-side** through the OpenAI Evals API.

The flow:

1. Build an :class:`azure.ai.projects.AIProjectClient` from the configured
   project endpoint using ``DefaultAzureCredential``.
2. Get the OpenAI client via ``project_client.get_openai_client()``. We do
   **not** pass ``api_version`` - the SDK picks the correct one (passing
   one explicitly has historically caused 404s in this codebase).
3. Inline the JSONL dataset rows as a ``file_content`` source.
4. Create the eval definition with ``client.evals.create(...)``, mapping
   each AgentOps evaluator preset onto an ``azure_ai_evaluator`` testing
   criterion.
5. Create the run with ``client.evals.runs.create(...)``, pointing at the
   inline rows and using ``azure_ai_target_completions`` with an
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

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agentops.core.agentops_config import DatasetSyncConfig
from agentops.core.results import RunResult

logger = logging.getLogger("agentops.pipeline.cloud_runner")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudRunResult:
    """Outcome of a cloud (New Foundry) publish."""

    eval_id: str
    run_id: str
    status: str
    report_url: Optional[str]
    evaluation_name: str
    #: Raw per-row output items downloaded from the Foundry Evals API.
    #: Each item is a dict with at least ``datasource_item`` (the original
    #: input row), ``sample`` (the agent response), and ``results``
    #: (per-criterion scores). May be empty if the SDK returns no items
    #: or the download failed (in which case orchestrator falls back to
    #: a thin RunResult that just records the portal URL).
    output_items: List[Dict[str, Any]] = field(default_factory=list)
    #: Dataset lineage and submission mode recorded for reports/Cockpit.
    dataset: Dict[str, Any] = field(default_factory=dict)


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


_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_DEFAULT_MAX_POLL_ATTEMPTS = 300  # 10 minutes at 2s intervals
_DEFAULT_HEARTBEAT_SECONDS = 10.0
_TERMINAL_STATUSES = {"completed", "failed", "canceled", "cancelled"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_on_foundry_cloud(
    result: RunResult,
    *,
    dataset_path: Path,
    project_endpoint: str,
    evaluation_name: Optional[str] = None,
    dataset_sync: Optional[DatasetSyncConfig] = None,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    max_poll_attempts: int = _DEFAULT_MAX_POLL_ATTEMPTS,
    progress: Optional[Callable[[str], None]] = None,
) -> CloudRunResult:
    """Submit ``result``'s target to Foundry for server-side evaluation.

    Parameters
    ----------
    result:
        Local run result. Used to derive the agent reference and the list
        of evaluator presets that should map onto ``azure_ai_evaluator``
        testing criteria.
    dataset_path:
        Path to the JSONL dataset to submit. Must already exist.
    project_endpoint:
        Foundry project endpoint URL (e.g.
        ``https://contoso.services.ai.azure.com/api/projects/p``).
    evaluation_name:
        Optional display name. Defaults to ``agentops-cloud-<short-uuid>``.
    dataset_sync:
        Optional submission policy. ``auto`` and ``inline`` currently use
        inline ``file_content`` compatibility and record that lineage; ``foundry``
        fails fast until the Foundry dataset reference path is validated.
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

    credential = DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)
    project_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
    )

    # NB: do not pass api_version - the SDK chooses the right one. Passing
    # an explicit version has historically caused 404s in this codebase.
    openai_client = project_client.get_openai_client()

    eval_name = evaluation_name or f"agentops-cloud-{uuid.uuid4().hex[:8]}"
    testing_criteria = _build_testing_criteria(result)
    if not testing_criteria:
        raise ValueError(
            "no AgentOps evaluators map onto azure_ai_evaluator testing "
            "criteria; nothing to evaluate server-side."
        )

    progress(f"cloud: preparing run '{eval_name}'")
    progress(
        "cloud: remote Foundry evaluations are asynchronous; small smoke "
        "runs commonly take 30-90s depending on queueing, agent latency, "
        "and evaluator model latency."
    )

    item_schema = _build_item_schema(dataset_path)
    source, dataset_lineage = _build_dataset_source(
        dataset_path,
        dataset_sync or DatasetSyncConfig(),
        project_client=project_client,
        progress=progress,
    )

    progress(
        f"cloud: creating eval ({len(testing_criteria)} criteria, "
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
        f"cloud: starting run for agent {agent_name}:{agent_version}"
    )
    try:
        run_obj = openai_client.evals.runs.create(
            eval_id=eval_id,
            name=f"{eval_name}-run",
            data_source={  # type: ignore[arg-type]
                "type": "azure_ai_target_completions",
                "source": source,
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
    except Exception as exc:  # noqa: BLE001
        raise _friendly_run_create_error(
            exc, agent_name=agent_name, agent_version=agent_version
        ) from exc
    run_id = run_obj.id

    progress(
        f"cloud: polling run {run_id} (interval "
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

    progress(f"cloud: done. status={status}")

    # Download per-row results from Foundry so the local results.json can
    # be populated without re-invoking the agent client-side.
    output_items = _list_output_items(
        openai_client,
        eval_id=eval_id,
        run_id=run_id,
        progress=progress,
    )

    return CloudRunResult(
        eval_id=eval_id,
        run_id=run_id,
        status=status,
        report_url=report_url,
        evaluation_name=eval_name,
        output_items=output_items,
        dataset=dataset_lineage,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    for input_field, placeholder in preset.input_mapping.items():
        if placeholder == "$prediction" and getattr(preset, "needs_conversation", False):
            mapping[input_field] = "{{sample.output_items}}"
            continue
        mapped = _CLOUD_PLACEHOLDERS.get(placeholder)
        if mapped:
            mapping[input_field] = mapped
    return mapping


def _build_file_content_source(
    dataset_path: Path,
    *,
    progress: Callable[[str], None],
) -> Dict[str, Any]:
    """Inline JSONL rows for Foundry target-completions runs.

    New Foundry currently validates file-id sources by extension after the
    upload is materialized server-side. Inline ``file_content`` avoids a
    service-side filename loss where valid ``.jsonl`` uploads can be read back
    as extensionless files.
    """
    progress(f"cloud: preparing {dataset_path.name}")
    content: List[Dict[str, Any]] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(
                    f"dataset row {line_number} must be a JSON object for "
                    "publish: foundry_cloud"
                )
            content.append({"item": row})
    if not content:
        raise ValueError("dataset must contain at least one row for publish: foundry_cloud")
    progress(f"cloud: prepared {len(content)} row(s)")
    return {
        "type": "file_content",
        "content": content,
    }


def _build_dataset_source(
    dataset_path: Path,
    dataset_sync: DatasetSyncConfig,
    *,
    project_client: Any,
    progress: Callable[[str], None],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if dataset_sync.mode == "inline":
        source = _build_file_content_source(dataset_path, progress=progress)
        return source, _build_inline_dataset_lineage(dataset_path, dataset_sync)

    try:
        source, lineage = _build_foundry_dataset_source(
            dataset_path,
            dataset_sync,
            project_client=project_client,
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001
        if dataset_sync.mode == "foundry":
            raise
        reason = _summarize_dataset_sync_error(exc)
        logger.debug(
            "Foundry dataset sync failed; falling back to inline file_content",
            exc_info=True,
        )
        progress(
            "cloud: dataset sync unavailable; using inline rows for this run. "
            f"Reason: {reason}"
        )
        source = _build_file_content_source(dataset_path, progress=progress)
        lineage = _build_inline_dataset_lineage(dataset_path, dataset_sync)
        lineage["status"] = "auto_fallback_inline"
        lineage["sync_error"] = reason
        return source, lineage
    return source, lineage


def _build_foundry_dataset_source(
    dataset_path: Path,
    dataset_sync: DatasetSyncConfig,
    *,
    project_client: Any,
    progress: Callable[[str], None],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    sha256 = _sha256_file(dataset_path)
    name = dataset_sync.name or _derived_foundry_dataset_name(dataset_path)
    version = _resolved_foundry_dataset_version(dataset_sync.version, sha256)
    progress(f"cloud: syncing dataset to Foundry {name}@{version}")

    dataset = _get_or_upload_foundry_dataset(
        project_client,
        name=name,
        version=version,
        dataset_path=dataset_path,
        progress=progress,
    )
    dataset_id = _dataset_attr(dataset, "id")
    if not dataset_id:
        raise RuntimeError(
            f"Foundry dataset {name}@{version} did not return an id."
        )
    progress(f"cloud: using Foundry dataset {name}@{version}")
    return (
        {
            "type": "file_id",
            "id": dataset_id,
        },
        {
            "mode": "foundry",
            "requested_mode": dataset_sync.mode,
            "source_type": "file_id",
            "local_path": str(dataset_path),
            "sha256": sha256,
            "status": "synced",
            "foundry_name": name,
            "foundry_version": version,
            "foundry_id": dataset_id,
            "foundry_uri": _dataset_attr(dataset, "dataUri"),
        },
    )


def _get_or_upload_foundry_dataset(
    project_client: Any,
    *,
    name: str,
    version: str,
    dataset_path: Path,
    progress: Callable[[str], None],
) -> Any:
    try:
        dataset = project_client.datasets.get(name=name, version=version)
        progress(f"cloud: found existing Foundry dataset {name}@{version}")
        return dataset
    except Exception as exc:  # noqa: BLE001
        if not _looks_not_found(exc):
            raise

    try:
        return project_client.datasets.upload_file(
            name=name,
            version=version,
            file_path=str(dataset_path),
        )
    except Exception as exc:  # noqa: BLE001
        if _looks_conflict(exc):
            progress(
                f"cloud: Foundry dataset {name}@{version} already exists; "
                "reusing it"
            )
            return project_client.datasets.get(name=name, version=version)
        raise


def _build_inline_dataset_lineage(
    dataset_path: Path,
    dataset_sync: DatasetSyncConfig,
) -> Dict[str, Any]:
    """Describe the local-to-Foundry dataset relationship for inline runs."""
    lineage: Dict[str, Any] = {
        "mode": "inline",
        "requested_mode": dataset_sync.mode,
        "source_type": "file_content",
        "local_path": str(dataset_path),
        "sha256": _sha256_file(dataset_path),
        "status": "compatibility_inline",
        "foundry_behavior": (
            "Foundry may materialize inline rows as eval-data-* backing "
            "dataset assets in the project Data page."
        ),
    }
    if dataset_sync.name:
        lineage["configured_name"] = dataset_sync.name
    if dataset_sync.version:
        lineage["configured_version"] = dataset_sync.version
    return lineage


def _summarize_dataset_sync_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "defaultazurecredential failed to retrieve a token" in lower:
        return (
            "Azure authentication was unavailable for Foundry dataset sync. "
            "Run `az login` or set `dataset_sync.mode: inline` to skip dataset "
            "asset sync during quick demos."
        )
    if "azureclicredential: failed to invoke the azure cli" in lower:
        return (
            "Azure CLI authentication was unavailable for Foundry dataset sync. "
            "Run `az login` or set `dataset_sync.mode: inline` for this run."
        )
    first_line = text.splitlines()[0].strip() if text else exc.__class__.__name__
    if len(first_line) > 180:
        first_line = first_line[:177] + "..."
    return f"{exc.__class__.__name__}: {first_line}"


def _derived_foundry_dataset_name(dataset_path: Path) -> str:
    stem = dataset_path.stem.lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-_")
    return f"agentops-{slug or 'dataset'}"


def _resolved_foundry_dataset_version(configured: str, sha256: str) -> str:
    if configured == "content-hash":
        return f"sha256-{sha256[:16]}"
    return configured


def _dataset_attr(dataset: Any, name: str) -> Optional[str]:
    value = getattr(dataset, name, None)
    if value is None and isinstance(dataset, dict):
        value = dataset.get(name)
    return str(value) if value else None


def _looks_not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True
    text = str(exc).lower()
    return "not found" in text or "resource not found" in text or "404" in text


def _looks_conflict(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 409:
        return True
    text = str(exc).lower()
    return "already exists" in text or "conflict" in text or "409" in text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    started = time.monotonic()
    last_progress_at = started
    for attempt in range(1, max_attempts + 1):
        run = openai_client.evals.runs.retrieve(eval_id=eval_id, run_id=run_id)
        status = getattr(run, "status", "unknown")
        now = time.monotonic()
        elapsed = now - started
        status_changed = status != last_status
        heartbeat_due = now - last_progress_at >= _DEFAULT_HEARTBEAT_SECONDS
        if status_changed or heartbeat_due:
            label = "run status ->" if status_changed else "still"
            progress(
                f"cloud: {label} {status} "
                f"(elapsed {_format_elapsed(elapsed)}, attempt {attempt}/{max_attempts})"
            )
            last_progress_at = now
            last_status = status
        if status in _TERMINAL_STATUSES:
            return run
        time.sleep(interval_seconds)
    raise RuntimeError(
        f"cloud evaluation run {run_id} did not finish within "
        f"{max_attempts} polls of {interval_seconds:g}s "
        f"(last status: {last_status!r})."
    )


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m{remaining:02d}s"
    return f"{remaining}s"


def _friendly_run_create_error(
    exc: Exception,
    *,
    agent_name: str,
    agent_version: str,
) -> Exception:
    """Convert a noisy Foundry/OpenAI ``evals.runs.create`` failure into a
    short, actionable ``RuntimeError``.

    The Evals API returns the underlying validation message inside a
    nested JSON envelope (``error.message`` →
    ``Evaluation failed validation: {"Code": "ResourceNotFound", ...}``).
    Rendering the raw exception dumps the whole envelope on stderr, which
    is unreadable. We pick out the inner detail and rephrase it in the
    common-case forms users actually hit.
    """
    raw = _extract_error_message(exc) or str(exc)
    lowered = raw.lower()

    if "was not found" in lowered or "resourcenotfound" in lowered:
        return RuntimeError(
            f"Agent '{agent_name}:{agent_version}' was not found in your "
            "Foundry project.\n"
            "  - Verify the name and version in target.endpoint.agent_id "
            "(format: name:version).\n"
            "  - Confirm AZURE_AI_FOUNDRY_PROJECT_ENDPOINT points to the "
            "project that owns the agent.\n"
            "  - Make sure the agent is deployed; list agents in the "
            "Foundry portal under Agents."
        )

    if "permission" in lowered or "forbidden" in lowered or "403" in raw:
        return RuntimeError(
            "Foundry denied the evaluation request (permission).\n"
            f"  - Confirm you have access to the project that owns "
            f"agent '{agent_name}:{agent_version}'.\n"
            "  - Try `az login` with the correct tenant or check the "
            "managed identity assigned to this environment."
        )

    if "quota" in lowered or "ratelimit" in lowered or "429" in raw:
        return RuntimeError(
            "Foundry rate-limited the evaluation request. Retry in a "
            "few minutes, or reduce dataset size."
        )

    return RuntimeError(f"Cloud evaluation could not start: {raw}")


def _extract_error_message(exc: Exception) -> Optional[str]:
    """Best-effort extraction of the human-readable message buried inside
    an OpenAI / Azure SDK error.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg:
                inner = _strip_validation_envelope(msg)
                return inner or msg
    msg = getattr(exc, "message", None)
    if isinstance(msg, str) and msg:
        return _strip_validation_envelope(msg) or msg
    return None


def _strip_validation_envelope(text: str) -> Optional[str]:
    """Pull the ``Message: ...`` line out of the validation envelope that
    Foundry returns inside ``error.message``. Returns ``None`` if no such
    line is present so callers can fall back to the original text.
    """
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("message:"):
            return s.split(":", 1)[1].strip()
    return None


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


def _list_output_items(
    openai_client: Any,
    *,
    eval_id: str,
    run_id: str,
    progress: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Download per-row output items from a completed Foundry eval run.

    Returns a list of dicts (one per dataset row) containing the original
    ``datasource_item`` (input row), the ``sample`` returned by the agent,
    and the per-criterion ``results``. Returns ``[]`` on any failure so
    the orchestrator can still emit a ``results.json`` that records the
    Foundry portal URL (no fallback to local invocation).
    """
    try:
        # The OpenAI Evals API exposes a paginated list endpoint at
        # ``client.evals.runs.output_items.list``. We accept either a
        # paginator object with ``.data`` / iteration, or a plain list.
        output_items_api = openai_client.evals.runs.output_items
        page = output_items_api.list(eval_id=eval_id, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not list output_items: %s", exc)
        progress(
            f"cloud: WARNING - could not download per-row results "
            f"({exc.__class__.__name__}); local results.json will record the "
            f"portal URL only."
        )
        return []

    items: List[Dict[str, Any]] = []
    try:
        iterable = getattr(page, "data", None) or page
        for raw in iterable:
            item = _coerce_output_item_to_dict(raw)
            if item is not None:
                items.append(item)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not iterate output_items: %s", exc)
        progress(
            f"cloud: WARNING - failed to iterate output_items "
            f"({exc.__class__.__name__}); local results.json will be thin."
        )
        return []

    progress(f"cloud: downloaded {len(items)} output item(s)")
    return items


def _coerce_output_item_to_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """Convert an SDK output item (Pydantic model or dict) into a plain dict."""
    if isinstance(raw, dict):
        return raw
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(raw, method, None)
        if callable(fn):
            try:
                value = fn()
                if isinstance(value, dict):
                    return value
            except Exception:  # noqa: BLE001
                continue
    # Fallback: pull known attributes off the object.
    keys = ("id", "status", "datasource_item", "sample", "results")
    if any(hasattr(raw, k) for k in keys):
        return {k: getattr(raw, k, None) for k in keys}
    return None
