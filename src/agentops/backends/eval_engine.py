"""Shared evaluation engine used by all AgentOps backends.

This module contains evaluator loading, instantiation, execution, scoring,
dataset utilities, and cloud-evaluator mapping helpers.  Every backend
(Foundry, HTTP, Local Adapter) imports from here instead of coupling to
a specific backend implementation.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentops.core.models import EvaluatorConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cloud-only evaluator sentinel
# ---------------------------------------------------------------------------


class _CloudOnlyEvaluatorError(Exception):
    """Raised when an evaluator is only available via Foundry Cloud Evaluation."""


# ---------------------------------------------------------------------------
# Credential help (shared by _default_credential and _acquire_token)
# ---------------------------------------------------------------------------

_CREDENTIAL_HELP_MESSAGE = (
    "Azure authentication failed. To fix this, do one of the following:\n"
    "\n"
    "  1. Run 'az login' (Azure CLI) to authenticate interactively.\n"
    "  2. Set AZURE_CLIENT_ID, AZURE_TENANT_ID, and AZURE_CLIENT_SECRET \n"
    "     environment variables for service-principal authentication.\n"
    "  3. If running on Azure, ensure a managed identity is configured.\n"
    "\n"
    "Docs: https://aka.ms/azsdk/python/identity/defaultazurecredential/troubleshoot"
)

# ---------------------------------------------------------------------------
# Evaluator classification constants
# ---------------------------------------------------------------------------

_NLP_ONLY_EVALUATORS = frozenset(
    {
        "f1_score",
        "bleu_score",
        "rouge_score",
        "meteor_score",
        "gleu_score",
    }
)

_EVALUATORS_NEEDING_GROUND_TRUTH = frozenset(
    {
        "similarity",
        "response_completeness",
        "f1_score",
        "bleu_score",
        "rouge_score",
        "meteor_score",
        "gleu_score",
    }
)

_EVALUATORS_NEEDING_CONTEXT = frozenset(
    {
        "groundedness",
        "groundedness_pro",
        "relevance",
        "retrieval",
    }
)

_EVALUATORS_NEEDING_TOOL_CALLS = frozenset(
    {
        "tool_call_accuracy",
        "tool_selection",
    }
)

_EVALUATORS_NEEDING_TOOL_DEFS_ONLY = frozenset(
    {
        "tool_input_accuracy",
        "tool_output_utilization",
        "tool_call_success",
    }
)

_EVALUATORS_NEEDING_OUTPUT_ITEMS = frozenset(
    {
        "task_adherence",
    }
)

_SAFETY_EVALUATORS = frozenset(
    {
        "violence",
        "sexual",
        "self_harm",
        "hate_unfairness",
        "content_safety",
        "protected_material",
        "code_vulnerability",
        "ungrounded_attributes",
        "indirect_attack",
    }
)

_AI_ASSISTED_EVALUATORS = {
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
    "TaskCompletionEvaluator",
    "TaskNavigationEfficiencyEvaluator",
    "ToolSelectionEvaluator",
    "ToolInputAccuracyEvaluator",
    "ToolOutputUtilizationEvaluator",
    "ToolCallSuccessEvaluator",
}

_SAFETY_EVALUATOR_CLASSES = frozenset(
    {
        "ViolenceEvaluator",
        "SexualEvaluator",
        "SelfHarmEvaluator",
        "HateUnfairnessEvaluator",
        "ContentSafetyEvaluator",
        "ProtectedMaterialEvaluator",
        "CodeVulnerabilityEvaluator",
        "UngroundedAttributesEvaluator",
        "IndirectAttackEvaluator",
        "GroundednessProEvaluator",
    }
)

_SUPPORTED_LOCAL_EVALUATORS = {
    "exact_match",
    "latency_seconds",
    "avg_latency_seconds",
}

# ---------------------------------------------------------------------------
# Runtime dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoundryEvaluatorRuntime:
    name: str
    evaluator: Callable[..., dict[str, Any]]
    input_mapping: dict[str, str]
    score_keys: list[str]


# ---------------------------------------------------------------------------
# Dataset utilities
# ---------------------------------------------------------------------------


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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("Dataset JSONL rows must be objects")
        rows.append(payload)
    if not rows:
        raise ValueError(f"Dataset is empty: {path}")
    return rows


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Evaluator name / mapping helpers
# ---------------------------------------------------------------------------


def _to_builtin_evaluator_name(evaluator_name: str) -> str:
    """Convert 'SimilarityEvaluator' → 'similarity'."""
    normalized = evaluator_name.strip()
    normalized = normalized.removesuffix("Evaluator")
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", normalized).lower()
    return snake


def _to_snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _cloud_evaluator_data_mapping(
    builtin_name: str,
    input_field: str,
    expected_field: str,
    context_field: str | None = None,
) -> dict[str, str]:
    """Build ``data_mapping`` for an ``azure_ai_evaluator`` testing criterion."""
    item_input = "{{item." + input_field + "}}"
    item_expected = "{{item." + expected_field + "}}"
    sample_response = "{{sample.output_text}}"

    mapping: dict[str, str] = {}
    if builtin_name in _SAFETY_EVALUATORS:
        mapping["query"] = item_input
        mapping["response"] = sample_response
        return mapping
    if builtin_name not in _NLP_ONLY_EVALUATORS:
        mapping["query"] = item_input
    if builtin_name in _EVALUATORS_NEEDING_OUTPUT_ITEMS:
        mapping["response"] = "{{sample.output_items}}"
    else:
        mapping["response"] = sample_response
    if builtin_name in _EVALUATORS_NEEDING_GROUND_TRUTH:
        mapping["ground_truth"] = item_expected
    elif builtin_name in _EVALUATORS_NEEDING_CONTEXT:
        context_item = "{{item." + (context_field or expected_field) + "}}"
        mapping["context"] = context_item
    elif builtin_name in _EVALUATORS_NEEDING_TOOL_CALLS:
        mapping["tool_calls"] = "{{sample.tool_calls}}"
        mapping["tool_definitions"] = "{{item.tool_definitions}}"
    elif builtin_name in _EVALUATORS_NEEDING_TOOL_DEFS_ONLY:
        mapping["tool_definitions"] = "{{item.tool_definitions}}"
    return mapping


def _cloud_evaluator_needs_model(builtin_name: str) -> bool:
    """Return True if the evaluator is AI-assisted and needs a deployment_name."""
    if builtin_name in _SAFETY_EVALUATORS:
        return False
    return builtin_name not in _NLP_ONLY_EVALUATORS


# Default initialization_parameters for evaluators that require them but are
# not AI-assisted (so they don't get deployment_name automatically).
_NLP_DEFAULT_INIT_PARAMS: dict[str, dict[str, Any]] = {
    "rouge_score": {"rouge_type": "rouge1"},
}


def _parse_agent_name_version(agent_id: str) -> tuple[str, str | None]:
    """Parse 'my-agent:3' into ('my-agent', '3')."""
    if ":" in agent_id:
        name, version = agent_id.split(":", 1)
        return name.strip(), version.strip() or None
    return agent_id.strip(), None


# ---------------------------------------------------------------------------
# Evaluator input mapping defaults
# ---------------------------------------------------------------------------


def _default_foundry_input_mapping(name: str) -> dict[str, str]:
    if name == "SimilarityEvaluator":
        return {
            "query": "$prompt",
            "response": "$prediction",
            "ground_truth": "$expected",
        }
    if name == "GroundednessEvaluator":
        return {
            "query": "$prompt",
            "response": "$prediction",
            "context": "$row.context",
        }
    if name in ("CoherenceEvaluator", "FluencyEvaluator"):
        return {
            "query": "$prompt",
            "response": "$prediction",
        }
    if name == "F1ScoreEvaluator":
        return {
            "response": "$prediction",
            "ground_truth": "$expected",
        }
    if name in ("RelevanceEvaluator", "RetrievalEvaluator"):
        return {
            "query": "$prompt",
            "response": "$prediction",
            "context": "$row.context",
        }
    if name == "ResponseCompletenessEvaluator":
        return {
            "response": "$prediction",
            "ground_truth": "$expected",
        }
    if name in (
        "TaskCompletionEvaluator",
        "IntentResolutionEvaluator",
        "TaskAdherenceEvaluator",
    ):
        return {
            "query": "$prompt",
            "response": "$prediction",
        }
    if name == "ToolCallAccuracyEvaluator":
        return {
            "query": "$prompt",
            "response": "$prediction",
            "tool_calls": "$row.tool_calls",
            "tool_definitions": "$row.tool_definitions",
        }
    if name in ("ToolSelectionEvaluator",):
        return {
            "query": "$prompt",
            "response": "$prediction",
            "tool_calls": "$row.tool_calls",
            "tool_definitions": "$row.tool_definitions",
        }
    if name in (
        "ToolInputAccuracyEvaluator",
        "ToolOutputUtilizationEvaluator",
        "ToolCallSuccessEvaluator",
    ):
        return {
            "query": "$prompt",
            "response": "$prediction",
            "tool_definitions": "$row.tool_definitions",
        }
    if name in (
        "ViolenceEvaluator",
        "SexualEvaluator",
        "SelfHarmEvaluator",
        "HateUnfairnessEvaluator",
        "ContentSafetyEvaluator",
        "ProtectedMaterialEvaluator",
        "CodeVulnerabilityEvaluator",
        "UngroundedAttributesEvaluator",
        "IndirectAttackEvaluator",
        "GroundednessProEvaluator",
    ):
        return {
            "query": "$prompt",
            "response": "$prediction",
        }
    return {}


def _default_score_keys(name: str) -> list[str]:
    snake_name = _to_snake_case(name)
    bare_name = snake_name.replace("_evaluator", "")
    keys = [
        bare_name,
        snake_name,
        f"{bare_name}_score",
        f"gpt_{bare_name}",
        "score",
        "value",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_supported_local_evaluators(evaluators: list[EvaluatorConfig]) -> None:
    unsupported = sorted(
        evaluator.name
        for evaluator in evaluators
        if evaluator.enabled
        and evaluator.source == "local"
        and evaluator.name not in _SUPPORTED_LOCAL_EVALUATORS
    )
    if unsupported:
        raise ValueError(
            "Unsupported local evaluator(s): "
            + ", ".join(unsupported)
            + ". Supported local evaluators are: "
            + ", ".join(sorted(_SUPPORTED_LOCAL_EVALUATORS))
        )


# ---------------------------------------------------------------------------
# Azure credential helpers (lazy imports)
# ---------------------------------------------------------------------------


def _default_credential() -> Any:
    try:
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:
        raise ImportError(
            "Foundry evaluators require 'azure-identity'. "
            "Install with: pip install azure-identity"
        ) from exc

    try:
        return DefaultAzureCredential(exclude_developer_cli_credential=True)
    except Exception as exc:
        raise RuntimeError(_CREDENTIAL_HELP_MESSAGE) from exc


def _azure_ai_project_config() -> str:
    """Return the Foundry project endpoint for safety/RAI evaluators."""
    project_endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        raise ValueError(
            "Safety evaluators require an Azure AI Foundry project endpoint. "
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT or provide "
            "config.init.azure_ai_project in the bundle evaluator config."
        )
    return project_endpoint


def _azure_openai_model_config(
    *,
    fallback_endpoint: str | None = None,
    fallback_deployment: str | None = None,
) -> dict[str, str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or fallback_endpoint
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or fallback_deployment
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")

    missing: list[str] = []
    if not endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not deployment:
        missing.append("AZURE_OPENAI_DEPLOYMENT")

    if missing:
        raise ValueError(
            "Foundry evaluator requires Azure OpenAI evaluator model settings. "
            "Missing: " + ", ".join(missing)
        )

    assert endpoint is not None
    assert deployment is not None

    model_config: dict[str, str] = {
        "azure_endpoint": endpoint,
        "azure_deployment": deployment,
    }
    if api_version:
        model_config["api_version"] = api_version
    return model_config


# ---------------------------------------------------------------------------
# Evaluator instantiation helpers
# ---------------------------------------------------------------------------


def _is_reasoning_like_deployment_name(name: str) -> bool:
    normalized = name.strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("o1")
        or normalized.startswith("o3")
        or normalized.startswith("o4")
        or normalized.startswith("gpt-5")
    )


def _should_enable_reasoning_mode(
    *,
    evaluator_name: str,
    init_kwargs: dict[str, Any],
) -> bool:
    if evaluator_name not in _AI_ASSISTED_EVALUATORS:
        return False
    if "is_reasoning_model" in init_kwargs:
        return False

    model_config = init_kwargs.get("model_config")
    if not isinstance(model_config, dict):
        return False

    deployment = model_config.get("azure_deployment") or model_config.get("model")
    if not isinstance(deployment, str):
        return False

    return _is_reasoning_like_deployment_name(deployment)


def _instantiate_evaluator_symbol(
    evaluator_symbol: Any,
    *,
    evaluator_name: str,
    init_kwargs: dict[str, Any],
) -> Callable[..., dict[str, Any]]:
    if not inspect.isclass(evaluator_symbol):
        if callable(evaluator_symbol):
            if init_kwargs:
                raise ValueError(
                    f"Evaluator '{evaluator_name}' resolved to callable and does not support config.init"
                )
            return evaluator_symbol
        raise ValueError(f"Evaluator '{evaluator_name}' is not callable")

    try:
        return evaluator_symbol(**init_kwargs)
    except TypeError as exc:
        if "is_reasoning_model" in init_kwargs:
            fallback_kwargs = dict(init_kwargs)
            fallback_kwargs.pop("is_reasoning_model", None)
            return evaluator_symbol(**fallback_kwargs)
        raise exc


def _interpolate_env_values(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if not match:
            return value
        env_name = match.group(1)
        env_value = os.getenv(env_name)
        if env_value is None:
            raise ValueError(
                f"Missing environment variable required by evaluator config: {env_name}"
            )
        return env_value
    if isinstance(value, dict):
        return {key: _interpolate_env_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_values(item) for item in value]
    return value


def _load_foundry_evaluator_callable(
    *,
    evaluator_name: str,
    evaluator_config: dict[str, Any],
    fallback_endpoint: str | None = None,
    fallback_deployment: str | None = None,
) -> Callable[..., dict[str, Any]]:
    kind = str(evaluator_config.get("kind", "builtin")).strip().lower()
    init_kwargs_raw = evaluator_config.get("init", {})
    if init_kwargs_raw is None:
        init_kwargs_raw = {}
    if not isinstance(init_kwargs_raw, dict):
        raise ValueError(f"Evaluator '{evaluator_name}' config.init must be an object")
    init_kwargs = _interpolate_env_values(init_kwargs_raw)

    if kind == "builtin":
        class_name = str(evaluator_config.get("class_name") or evaluator_name).strip()
        if not class_name:
            raise ValueError(
                f"Evaluator '{evaluator_name}' class_name must be non-empty"
            )

        if class_name in _AI_ASSISTED_EVALUATORS and "model_config" not in init_kwargs:
            init_kwargs["model_config"] = _azure_openai_model_config(
                fallback_endpoint=fallback_endpoint,
                fallback_deployment=fallback_deployment,
            )

        if (
            class_name in _SAFETY_EVALUATOR_CLASSES
            and "azure_ai_project" not in init_kwargs
        ):
            init_kwargs["azure_ai_project"] = _azure_ai_project_config()

        if "credential" not in init_kwargs:
            init_kwargs["credential"] = _default_credential()

        if _should_enable_reasoning_mode(
            evaluator_name=class_name,
            init_kwargs=init_kwargs,
        ):
            init_kwargs["is_reasoning_model"] = True

        try:
            module = importlib.import_module("azure.ai.evaluation")
            evaluator_symbol = getattr(module, class_name)
        except ImportError as exc:
            raise ImportError(
                "Foundry evaluators require 'azure-ai-evaluation'. "
                "Install with: pip install azure-ai-evaluation"
            ) from exc
        except AttributeError as exc:
            raise _CloudOnlyEvaluatorError(
                f"Evaluator '{class_name}' is not available in the local "
                f"azure-ai-evaluation SDK. It may only be available via "
                f"Foundry Cloud Evaluation (builtin.{_to_builtin_evaluator_name(class_name)}). "
                f"Use 'hosting: foundry' with 'execution_mode: remote' to "
                f"run this evaluator, or disable it for local runs."
            ) from exc

        return _instantiate_evaluator_symbol(
            evaluator_symbol,
            evaluator_name=evaluator_name,
            init_kwargs=init_kwargs,
        )

    if kind == "custom":
        callable_path = evaluator_config.get("callable_path")
        if not isinstance(callable_path, str) or not callable_path.strip():
            raise ValueError(
                f"Evaluator '{evaluator_name}' with kind=custom requires config.callable_path"
            )

        module_name, separator, symbol_name = callable_path.partition(":")
        if not separator or not module_name.strip() or not symbol_name.strip():
            raise ValueError(
                f"Evaluator '{evaluator_name}' callable_path must be '<module>:<symbol>'"
            )

        module = importlib.import_module(module_name.strip())
        evaluator_symbol = getattr(module, symbol_name.strip())

        return _instantiate_evaluator_symbol(
            evaluator_symbol,
            evaluator_name=evaluator_name,
            init_kwargs=init_kwargs,
        )

    raise ValueError(
        f"Evaluator '{evaluator_name}' has unsupported config.kind '{kind}'. "
        "Use 'builtin' or 'custom'."
    )


# ---------------------------------------------------------------------------
# Build evaluator runtimes from bundle config
# ---------------------------------------------------------------------------


def _build_foundry_evaluator_runtimes(
    evaluators: list[EvaluatorConfig],
    *,
    fallback_endpoint: str | None = None,
    fallback_deployment: str | None = None,
) -> list[FoundryEvaluatorRuntime]:
    runtimes: list[FoundryEvaluatorRuntime] = []
    for evaluator in evaluators:
        if not evaluator.enabled or evaluator.source != "foundry":
            continue

        config = evaluator.config or {}
        if not isinstance(config, dict):
            raise ValueError(f"Evaluator '{evaluator.name}' config must be an object")

        input_mapping_raw = config.get("input_mapping")
        if input_mapping_raw is None:
            input_mapping = _default_foundry_input_mapping(evaluator.name)
        else:
            if not isinstance(input_mapping_raw, dict):
                raise ValueError(
                    f"Evaluator '{evaluator.name}' config.input_mapping must be an object"
                )
            input_mapping = {
                str(key): str(value) for key, value in input_mapping_raw.items()
            }

        score_keys_raw = config.get("score_keys")
        if score_keys_raw is None:
            score_keys = _default_score_keys(evaluator.name)
        else:
            if not isinstance(score_keys_raw, list) or not all(
                isinstance(item, str) for item in score_keys_raw
            ):
                raise ValueError(
                    f"Evaluator '{evaluator.name}' config.score_keys must be a list of strings"
                )
            score_keys = score_keys_raw

        try:
            evaluator_callable = _load_foundry_evaluator_callable(
                evaluator_name=evaluator.name,
                evaluator_config=config,
                fallback_endpoint=fallback_endpoint,
                fallback_deployment=fallback_deployment,
            )
        except _CloudOnlyEvaluatorError:
            logger.warning(
                "Skipping evaluator '%s' — not available in the local "
                "azure-ai-evaluation SDK. This evaluator is only supported "
                "via Foundry Cloud Evaluation (hosting: foundry, "
                "execution_mode: remote). It will be ignored for this "
                "local run.",
                evaluator.name,
            )
            continue

        runtimes.append(
            FoundryEvaluatorRuntime(
                name=evaluator.name,
                evaluator=evaluator_callable,
                input_mapping=input_mapping,
                score_keys=score_keys,
            )
        )
    return runtimes


# ---------------------------------------------------------------------------
# Evaluator score extraction
# ---------------------------------------------------------------------------


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _find_numeric_value(payload: Any) -> float | None:
    direct = _as_number(payload)
    if direct is not None:
        return direct

    if isinstance(payload, dict):
        for item in payload.values():
            found = _find_numeric_value(item)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_numeric_value(item)
            if found is not None:
                return found

    return None


def _extract_evaluator_score(
    payload: dict[str, Any], preferred_keys: list[str], evaluator_name: str
) -> float:
    for key in preferred_keys:
        if key in payload:
            numeric = _find_numeric_value(payload[key])
            if numeric is not None:
                return numeric

    for value in payload.values():
        numeric = _find_numeric_value(value)
        if numeric is not None:
            return numeric

    raise ValueError(f"Foundry evaluator '{evaluator_name}' returned no numeric score")


# ---------------------------------------------------------------------------
# Evaluator mapping resolution and execution
# ---------------------------------------------------------------------------


def _resolve_mapping_value(
    expression: Any,
    *,
    prompt: str,
    prediction: str,
    expected: str,
    row: dict[str, Any],
) -> Any:
    if not isinstance(expression, str):
        return expression

    env_match = re.fullmatch(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", expression)
    if env_match:
        env_name = env_match.group(1)
        env_value = os.getenv(env_name)
        if env_value is None:
            raise ValueError(
                f"Missing environment variable required by evaluator mapping: {env_name}"
            )
        return env_value

    if expression.startswith("$row."):
        row_key = expression[5:]
        if row_key not in row:
            raise ValueError(
                f"Missing row field referenced by evaluator mapping: {row_key}"
            )
        return row[row_key]

    if expression.startswith("$"):
        token = expression[1:]
        aliases: dict[str, Any] = {
            "prompt": prompt,
            "query": prompt,
            "input": prompt,
            "prediction": prediction,
            "response": prediction,
            "output_text": prediction,
            "expected": expected,
            "ground_truth": expected,
            "reference": expected,
            "context": expected,
        }
        if token in aliases:
            return aliases[token]
        if token in row:
            return row[token]
        raise ValueError(f"Unknown evaluator mapping token: {expression}")

    return expression


def _build_evaluator_kwargs(
    runtime: FoundryEvaluatorRuntime,
    *,
    prompt: str,
    prediction: str,
    expected: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    if runtime.input_mapping:
        return {
            key: _resolve_mapping_value(
                value,
                prompt=prompt,
                prediction=prediction,
                expected=expected,
                row=row,
            )
            for key, value in runtime.input_mapping.items()
        }

    base_context: dict[str, Any] = {
        "prompt": prompt,
        "query": prompt,
        "input": prompt,
        "response": prediction,
        "prediction": prediction,
        "output_text": prediction,
        "expected": expected,
        "ground_truth": expected,
        "reference": expected,
        "context": expected,
    }

    signature = inspect.signature(runtime.evaluator)
    accepts_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )

    if accepts_kwargs:
        merged = dict(base_context)
        merged.update(row)
        return merged

    kwargs: dict[str, Any] = {}
    for name, param in signature.parameters.items():
        if param.kind not in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            continue
        if name in row:
            kwargs[name] = row[name]
            continue
        if name in base_context:
            kwargs[name] = base_context[name]
            continue
        if param.default is inspect.Parameter.empty:
            raise ValueError(
                f"Evaluator '{runtime.name}' requires argument '{name}'. "
                "Provide evaluators[].config.input_mapping in bundle config."
            )
    return kwargs


def _run_foundry_evaluator(
    runtime: FoundryEvaluatorRuntime,
    *,
    prompt: str,
    prediction: str,
    expected: str,
    row: dict[str, Any],
) -> float:
    kwargs = _build_evaluator_kwargs(
        runtime,
        prompt=prompt,
        prediction=prediction,
        expected=expected,
        row=row,
    )
    payload = runtime.evaluator(**kwargs)
    if not isinstance(payload, dict):
        raise ValueError(f"Evaluator '{runtime.name}' returned invalid payload")

    score = _extract_evaluator_score(
        payload,
        preferred_keys=runtime.score_keys,
        evaluator_name=runtime.name,
    )
    return round(score, 6)
