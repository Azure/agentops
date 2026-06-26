"""Evaluator runtime for AgentOps 1.0.

Each :class:`EvaluatorPreset` from the catalog is instantiated lazily from
``azure.ai.evaluation`` and run against one dataset row. The runtime hides
SDK details (``model_config`` for AI-assisted evaluators, ``azure_ai_project``
for safety evaluators, kwarg mapping, score extraction).
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agentops.core.evaluators import EvaluatorPreset
from agentops.core.results import RowMetric

# Evaluator classes that require an evaluator model via ``model_config``.
_AI_ASSISTED = {
    "GroundednessEvaluator",
    "RelevanceEvaluator",
    "CoherenceEvaluator",
    "FluencyEvaluator",
    "SimilarityEvaluator",
    "RetrievalEvaluator",
    "ResponseCompletenessEvaluator",
    "QAEvaluator",
    "IntentResolutionEvaluator",
    "TaskAdherenceEvaluator",
    "ToolCallAccuracyEvaluator",
}

# Evaluator classes that require ``azure_ai_project``.
_SAFETY = {
    "ViolenceEvaluator",
    "SexualEvaluator",
    "SelfHarmEvaluator",
    "HateUnfairnessEvaluator",
    "ContentSafetyEvaluator",
    "ProtectedMaterialEvaluator",
}


@dataclass
class EvaluatorRuntime:
    """A loaded, ready-to-call evaluator."""

    preset: EvaluatorPreset
    callable: Any  # evaluator instance or sentinel for "latency"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _credential() -> Any:
    from azure.identity import DefaultAzureCredential  # noqa: WPS433

    return DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)


_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _model_config() -> Dict[str, Any]:
    from agentops.utils.azure_endpoints import (
        derive_openai_endpoint_from_project,
        normalize_azure_openai_endpoint,
    )

    raw_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    endpoint = normalize_azure_openai_endpoint(raw_endpoint)
    if not endpoint:
        # CONTRIBUTING.md promises ``AZURE_OPENAI_ENDPOINT`` is "auto-derived
        # from the project endpoint when absent". The Foundry project URL
        # already encodes the AI Services account host, so we can recover
        # the base inference endpoint without an extra round-trip or any
        # new wizard prompt.
        endpoint = derive_openai_endpoint_from_project(
            os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        )
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )
    # The New Foundry "AI Services" inference endpoint rejects the
    # azure-ai-evaluation SDK's stock api-version with
    # ``BadRequest: API version not supported``. Default to a version
    # known to work against both the New Foundry proxy and classic
    # Azure OpenAI; allow override via AZURE_OPENAI_API_VERSION.
    api_version = os.getenv("AZURE_OPENAI_API_VERSION") or "2025-04-01-preview"

    missing = []
    if not endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not deployment:
        missing.append("AZURE_OPENAI_DEPLOYMENT")
    if missing:
        hint = ""
        if "AZURE_OPENAI_DEPLOYMENT" in missing:
            hint = (
                " Set AZURE_OPENAI_DEPLOYMENT to the name of a model "
                "deployment in your Foundry project (Models + endpoints "
                "in the portal), or switch the run to `execution: cloud` "
                "in agentops.yaml so Foundry runs the evaluators server-side."
            )
        raise RuntimeError(
            "AI-assisted evaluators require an evaluator model. "
            "Missing environment variables: " + ", ".join(missing) + "." + hint
        )

    config: Dict[str, Any] = {
        "azure_endpoint": endpoint,
        "azure_deployment": deployment,
        "api_version": api_version,
    }
    return config


def _is_reasoning_model_deployment(deployment: Optional[str]) -> bool:
    """Return whether an evaluator deployment needs reasoning-model parameters."""

    if not deployment:
        return False
    normalized = deployment.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _REASONING_MODEL_PREFIXES)


def _project_endpoint() -> str:
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "Safety evaluators require AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
        )
    return endpoint


_LATENCY_SENTINEL = object()


def load_evaluator(preset: EvaluatorPreset) -> EvaluatorRuntime:
    """Instantiate one evaluator. Raises a clear error if the SDK is missing."""
    if preset.class_name == "_latency":
        return EvaluatorRuntime(preset=preset, callable=_LATENCY_SENTINEL)

    try:
        module = importlib.import_module("azure.ai.evaluation")
    except ImportError as exc:
        raise RuntimeError(
            "Evaluators require the 'azure-ai-evaluation' package. "
            "Install the Foundry extra in this virtual environment. "
            "Run: python -m pip install --upgrade 'agentops-accelerator[foundry]'"
        ) from exc

    cls = getattr(module, preset.class_name, None)
    if cls is None:
        raise RuntimeError(
            f"Evaluator class {preset.class_name!r} not found in azure.ai.evaluation"
        )

    init_kwargs: Dict[str, Any] = {}
    if preset.class_name in _AI_ASSISTED:
        model_config = _model_config()
        init_kwargs["model_config"] = model_config
        if _is_reasoning_model_deployment(model_config.get("azure_deployment")):
            init_kwargs["is_reasoning_model"] = True
    if preset.class_name in _SAFETY:
        init_kwargs["azure_ai_project"] = _project_endpoint()
        init_kwargs["credential"] = _credential()

    try:
        instance = cls(**init_kwargs) if inspect.isclass(cls) else cls
    except TypeError:
        # Some evaluators reject unexpected kwargs (e.g. F1ScoreEvaluator).
        instance = cls() if inspect.isclass(cls) else cls

    return EvaluatorRuntime(preset=preset, callable=instance)


def load_evaluators(presets: List[EvaluatorPreset]) -> List[EvaluatorRuntime]:
    return [load_evaluator(preset) for preset in presets]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


_PLACEHOLDERS = {
    "$prompt": "input",
    "$prediction": "response",
    "$expected": "expected",
    "$context": "context",
    "$retrieved_context": "retrieved_context",
    "$retrieved_context_items": "retrieved_context_items",
    "$tool_calls": "tool_calls",
    "$tool_definitions": "tool_definitions",
    "$telemetry.trace_id": "telemetry.trace_id",
}


def _build_conversation_messages(
    *,
    input_text: Optional[str],
    response_text: str,
    tool_calls: Any,
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Build conversation-style ``query`` and ``response`` for agent evaluators.

    When the agent invoked tools, returning only the final answer text to
    evaluators like ``IntentResolutionEvaluator`` and ``TaskAdherenceEvaluator``
    leaves them blind to *how* the agent arrived at that answer. They then
    consistently score it as 1/5 even when the agent did the right thing.

    This helper returns a structured payload compatible with the
    ``azure.ai.evaluation`` conversational schema:

    * ``query`` -> a single user message with the original input text
    * ``response`` -> a sequence of assistant tool_call messages, optional
      tool result messages (when each captured call has a ``result``
      string), and a final assistant text message with the natural-language
      answer.

    Returns ``None`` when there are no tool calls to include - callers
    should fall back to plain string kwargs in that case.
    """
    has_tool_calls = isinstance(tool_calls, list) and len(tool_calls) > 0

    query_messages: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": input_text or ""}],
        }
    ]

    response_messages: List[Dict[str, Any]] = []
    if has_tool_calls:
        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            # Normalise across the OpenAI ``function_call`` shape and the
            # nested ``function`` envelope produced by some Foundry payloads.
            raw_function = call.get("function")
            function: Dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
            name = call.get("name") or function.get("name")
            if not name:
                continue
            arguments = call.get("arguments")
            if arguments is None:
                arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    # leave as raw string - evaluators tolerate either form
                    pass
            tool_call_id = call.get("tool_call_id") or call.get("id") or f"call_{index}"

            response_messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_call",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "arguments": arguments if arguments is not None else {},
                }],
            })

            result = call.get("result")
            if isinstance(result, str) and result:
                response_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": [{"type": "tool_result", "tool_result": result}],
                })

    if response_text:
        response_messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": response_text}],
        })

    if not response_messages:
        return None

    return {"query": query_messages, "response": response_messages}


def _resolve_kwargs(
    mapping: Dict[str, str],
    *,
    row: Dict[str, Any],
    response: str,
) -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    row_response = row.get("response")
    merged = {**row, "response": response, "input": row.get("input")}
    for kwarg, placeholder in mapping.items():
        if not isinstance(placeholder, str) or not placeholder.startswith("$"):
            resolved[kwarg] = placeholder
            continue
        source_path = _PLACEHOLDERS.get(placeholder)
        if source_path is None and placeholder.startswith("$response."):
            if isinstance(row_response, dict):
                value = _lookup_placeholder(row_response, placeholder[len("$response."):])
                if value is not None:
                    resolved[kwarg] = value
                    continue
            source_path = placeholder[1:]
        if source_path is None and placeholder.startswith("$telemetry."):
            source_path = placeholder[1:]
        if source_path is None:
            raise ValueError(f"unknown evaluator placeholder {placeholder!r}")
        value = _lookup_placeholder(merged, source_path)
        if value is None:
            continue
        resolved[kwarg] = value
    return resolved


def _lookup_placeholder(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _extract_score(payload: Any, score_key: str) -> Optional[float]:
    if payload is None:
        return None
    if isinstance(payload, (int, float)):
        return float(payload)
    if not isinstance(payload, dict):
        return None
    for candidate in (
        score_key,
        f"{score_key}_score",
        f"gpt_{score_key}",
        "score",
    ):
        value = payload.get(candidate)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _extract_reason(payload: Any, score_key: str) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for candidate in (
        f"{score_key}_reason",
        f"{score_key}_reasoning",
        f"gpt_{score_key}_reason",
        "reason",
        "reasoning",
    ):
        value = payload.get(candidate)
        if isinstance(value, str) and value.strip():
            return value
    return None


def run_evaluator(
    runtime: EvaluatorRuntime,
    *,
    row: Dict[str, Any],
    response: str,
    latency_seconds: float,
    actual_tool_calls: Optional[List[Any]] = None,
) -> RowMetric:
    """Execute one evaluator on one row. Captures errors so the run continues."""
    preset = runtime.preset
    if runtime.callable is _LATENCY_SENTINEL:
        return RowMetric(name=preset.score_key, value=float(latency_seconds))

    # ToolCallAccuracyEvaluator: special handling when the agent made no
    # tool calls. The Azure SDK evaluator raises ("No tool calls found in
    # response...") which would surface as ERR. Translate that into a
    # meaningful score:
    #   * dataset has no tool_calls either -> not applicable (n/a).
    #   * dataset expected tool_calls -> the agent failed to call them, so
    #     score it as 0.0 instead of crashing the row.
    if preset.class_name == "ToolCallAccuracyEvaluator":
        has_actual = isinstance(actual_tool_calls, list) and len(actual_tool_calls) > 0
        has_dataset = isinstance(row.get("tool_calls"), list) and len(row["tool_calls"]) > 0
        if not has_actual:
            if has_dataset:
                return RowMetric(
                    name=preset.score_key,
                    value=0.0,
                    reason="agent made no tool calls but the dataset expected some",
                )
            return RowMetric(
                name=preset.score_key,
                value=None,
                reason="not applicable: agent made no tool calls",
            )

    try:
        kwargs = _resolve_kwargs(preset.input_mapping, row=row, response=response)
        if preset.needs_conversation:
            # Prefer the actual calls made by the agent during invocation;
            # fall back to the dataset's expected calls if the runner did
            # not provide any (e.g. unit tests).
            tool_calls_for_convo = (
                actual_tool_calls
                if actual_tool_calls is not None
                else row.get("tool_calls")
            )
            conversation = _build_conversation_messages(
                input_text=row.get("input"),
                response_text=response,
                tool_calls=tool_calls_for_convo,
            )
            if conversation is not None:
                # Upgrade query/response from plain strings to the
                # conversational schema. Both kwargs are guaranteed to be
                # in input_mapping for evaluators that opt into this.
                if "query" in kwargs:
                    kwargs["query"] = conversation["query"]
                if "response" in kwargs:
                    kwargs["response"] = conversation["response"]

        # Retry once on transient Azure CLI credential failures. The
        # az CLI occasionally fails to launch on Windows under heavy
        # I/O; DefaultAzureCredential's other sources usually succeed
        # on the second attempt because the token has been cached.
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                result = runtime.callable(**kwargs)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0 and _is_transient_credential_error(exc):
                    time.sleep(0.5)
                    continue
                raise
        if last_exc is not None:  # pragma: no cover - defensive
            raise last_exc
        score = _extract_score(result, preset.score_key)
        reason = _extract_reason(result, preset.score_key)
        return RowMetric(name=preset.score_key, value=score, reason=reason)
    except Exception as exc:  # noqa: BLE001
        return RowMetric(name=preset.score_key, error=str(exc))


_TRANSIENT_CRED_MARKERS = (
    "failed to invoke the azure cli",
    "azureclicredential",
    "credentialunavailableerror",
)


def _is_transient_credential_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_CRED_MARKERS)
