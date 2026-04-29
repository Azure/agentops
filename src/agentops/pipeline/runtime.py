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

    return DefaultAzureCredential(exclude_developer_cli_credential=True)


def _model_config() -> Dict[str, str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
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
        raise RuntimeError(
            "AI-assisted evaluators require an evaluator model. "
            "Missing environment variables: " + ", ".join(missing)
        )

    config: Dict[str, str] = {
        "azure_endpoint": endpoint,  # type: ignore[dict-item]
        "azure_deployment": deployment,  # type: ignore[dict-item]
        "api_version": api_version,
    }
    return config


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
            "Install with: pip install azure-ai-evaluation"
        ) from exc

    cls = getattr(module, preset.class_name, None)
    if cls is None:
        raise RuntimeError(
            f"Evaluator class {preset.class_name!r} not found in azure.ai.evaluation"
        )

    init_kwargs: Dict[str, Any] = {}
    if preset.class_name in _AI_ASSISTED:
        init_kwargs["model_config"] = _model_config()
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
    "$tool_calls": "tool_calls",
    "$tool_definitions": "tool_definitions",
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

    Returns ``None`` when there are no tool calls to include — callers
    should fall back to plain string kwargs in that case.
    """
    if not isinstance(tool_calls, list) or not tool_calls:
        return None

    query_messages: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": input_text or ""}],
        }
    ]

    response_messages: List[Dict[str, Any]] = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        # Normalise across the OpenAI ``function_call`` shape and the
        # nested ``function`` envelope produced by some Foundry payloads.
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
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
                # leave as raw string — evaluators tolerate either form
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
    merged = {**row, "response": response, "input": row.get("input")}
    for kwarg, placeholder in mapping.items():
        if not isinstance(placeholder, str) or not placeholder.startswith("$"):
            resolved[kwarg] = placeholder
            continue
        source_key = _PLACEHOLDERS.get(placeholder)
        if source_key is None:
            raise ValueError(f"unknown evaluator placeholder {placeholder!r}")
        value = merged.get(source_key)
        if value is None:
            continue
        resolved[kwarg] = value
    return resolved


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
) -> RowMetric:
    """Execute one evaluator on one row. Captures errors so the run continues."""
    preset = runtime.preset
    if runtime.callable is _LATENCY_SENTINEL:
        return RowMetric(name=preset.score_key, value=float(latency_seconds))

    try:
        kwargs = _resolve_kwargs(preset.input_mapping, row=row, response=response)
        if preset.needs_conversation:
            conversation = _build_conversation_messages(
                input_text=row.get("input"),
                response_text=response,
                tool_calls=row.get("tool_calls"),
            )
            if conversation is not None:
                # Upgrade query/response from plain strings to the
                # conversational schema. Both kwargs are guaranteed to be
                # in input_mapping for evaluators that opt into this.
                if "query" in kwargs:
                    kwargs["query"] = conversation["query"]
                if "response" in kwargs:
                    kwargs["response"] = conversation["response"]
        result = runtime.callable(**kwargs)
        score = _extract_score(result, preset.score_key)
        reason = _extract_reason(result, preset.score_key)
        return RowMetric(name=preset.score_key, value=score, reason=reason)
    except Exception as exc:  # noqa: BLE001
        return RowMetric(name=preset.score_key, error=str(exc))
