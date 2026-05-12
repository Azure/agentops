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
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from agentops.core.agentops_config import TargetResolution
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

_REASONING_MODEL_ENV = "AGENTOPS_EVALUATOR_REASONING_MODEL"
_REASONING_MODEL_TRUTHY = {"1", "true", "yes", "on"}
_REASONING_MODEL_FALSEY = {"0", "false", "no", "off"}
_REASONING_MODEL_VALUES = "1, true, yes, on, 0, false, no, off"
_REASONING_DEPLOYMENT_RE = re.compile(
    r"^(?:gpt[-_]?5(?:$|[^a-z0-9])|o[134](?:$|[^a-z0-9]))",
    re.IGNORECASE,
)


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


def _explicit_evaluator_deployment() -> Optional[str]:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )


def _foundry_data_plane_endpoint(project_endpoint: str) -> str:
    raw = project_endpoint.strip().rstrip("/")
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            "Cannot derive evaluator endpoint from "
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT because it is not an absolute URL. "
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT to a URL like "
            "'https://<resource>.services.ai.azure.com/api/projects/<project>', "
            "or set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT for an "
            "explicit evaluator deployment."
        )

    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) >= 3
        and segments[0].lower() == "api"
        and segments[1].lower() == "projects"
        and segments[2]
    ):
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    raise RuntimeError(
        "Cannot derive evaluator endpoint from "
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT. Expected a Foundry project URL like "
        "'https://<resource>.services.ai.azure.com/api/projects/<project>'. "
        "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT if you want to "
        "use a separate evaluator deployment."
    )


def _default_model_direct_config(
    *,
    target: Optional[TargetResolution],
    foundry_project_endpoint: Optional[str],
) -> tuple[str, str]:
    if target is None or target.kind != "model_direct" or not target.deployment:
        raise RuntimeError(
            "AI-assisted evaluators require an evaluator model. Set "
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT, or use a "
            "Foundry model-direct target like agent: 'model:<deployment>' so "
            "AgentOps can default the evaluator deployment to the target model."
        )
    endpoint = foundry_project_endpoint or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "AI-assisted evaluators can default the judge deployment to "
            f"{target.raw!r}, but AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is required "
            "to derive the evaluator endpoint. Set "
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT, or set AZURE_OPENAI_ENDPOINT "
            "and AZURE_OPENAI_DEPLOYMENT for an explicit evaluator deployment."
        )

    return _foundry_data_plane_endpoint(endpoint), target.deployment


def _model_config(
    *,
    target: Optional[TargetResolution] = None,
    foundry_project_endpoint: Optional[str] = None,
) -> Dict[str, str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = _explicit_evaluator_deployment()
    # The New Foundry "AI Services" inference endpoint rejects the
    # azure-ai-evaluation SDK's stock api-version with
    # ``BadRequest: API version not supported``. Default to a version
    # known to work against both the New Foundry proxy and classic
    # Azure OpenAI; allow override via AZURE_OPENAI_API_VERSION.
    api_version = os.getenv("AZURE_OPENAI_API_VERSION") or "2025-04-01-preview"

    if endpoint or deployment:
        missing = []
        if not endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not deployment:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        if missing:
            raise RuntimeError(
                "AI-assisted evaluator override is incomplete. Missing "
                "environment variables: "
                + ", ".join(missing)
                + ". Set both AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT "
                "(or AZURE_AI_MODEL_DEPLOYMENT_NAME), or unset them to use the "
                "Foundry model-direct defaults."
            )
    else:
        endpoint, deployment = _default_model_direct_config(
            target=target,
            foundry_project_endpoint=foundry_project_endpoint,
        )

    config: Dict[str, str] = {
        "azure_endpoint": endpoint,  # type: ignore[dict-item]
        "azure_deployment": deployment,  # type: ignore[dict-item]
        "api_version": api_version,
    }
    return config


def _reasoning_model_override() -> Optional[bool]:
    raw = os.getenv(_REASONING_MODEL_ENV)
    if raw is None:
        return None

    value = raw.strip().lower()
    if value in _REASONING_MODEL_TRUTHY:
        return True
    if value in _REASONING_MODEL_FALSEY:
        return False
    raise RuntimeError(
        f"{_REASONING_MODEL_ENV} must be one of {_REASONING_MODEL_VALUES}; "
        f"got {raw!r}."
    )


def _evaluator_is_reasoning_model(deployment: str) -> bool:
    override = _reasoning_model_override()
    if override is not None:
        return override

    # gpt-5 and o-series deployments require max_completion_tokens in current
    # Azure OpenAI APIs. The SDK converts legacy prompty max_tokens only when
    # evaluator classes receive is_reasoning_model=True.
    return bool(_REASONING_DEPLOYMENT_RE.match(deployment.strip()))


def _project_endpoint() -> str:
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "Safety evaluators require AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
        )
    return endpoint


_LATENCY_SENTINEL = object()


def load_evaluator(
    preset: EvaluatorPreset,
    *,
    target: Optional[TargetResolution] = None,
    foundry_project_endpoint: Optional[str] = None,
) -> EvaluatorRuntime:
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
        model_config = _model_config(
            target=target,
            foundry_project_endpoint=foundry_project_endpoint,
        )
        init_kwargs["model_config"] = model_config
        if _evaluator_is_reasoning_model(model_config["azure_deployment"]):
            init_kwargs["is_reasoning_model"] = True
    if preset.class_name in _SAFETY:
        init_kwargs["azure_ai_project"] = _project_endpoint()
        init_kwargs["credential"] = _credential()

    try:
        instance = cls(**init_kwargs) if inspect.isclass(cls) else cls
    except TypeError as exc:
        if init_kwargs:
            kwarg_names = ", ".join(sorted(init_kwargs))
            raise RuntimeError(
                f"Failed to initialize evaluator {preset.class_name!r} with "
                f"required configuration ({kwarg_names}). AgentOps will not "
                "retry without that configuration. If this happens with a "
                "GPT-5 or o-series evaluator deployment, upgrade "
                "azure-ai-evaluation to a version that supports "
                "is_reasoning_model."
            ) from exc
        # Some evaluators reject unexpected kwargs (e.g. F1ScoreEvaluator).
        instance = cls() if inspect.isclass(cls) else cls

    return EvaluatorRuntime(preset=preset, callable=instance)


def load_evaluators(
    presets: List[EvaluatorPreset],
    *,
    target: Optional[TargetResolution] = None,
    foundry_project_endpoint: Optional[str] = None,
) -> List[EvaluatorRuntime]:
    return [
        load_evaluator(
            preset,
            target=target,
            foundry_project_endpoint=foundry_project_endpoint,
        )
        for preset in presets
    ]


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
        if last_exc is not None:  # pragma: no cover — defensive
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
