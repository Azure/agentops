"""Unit tests for AI-assisted evaluator reasoning-model compatibility."""

from __future__ import annotations

import sys
import types
from typing import Any, Dict

import pytest

from agentops.core.agentops_config import TargetResolution, classify_agent
from agentops.core.evaluators import EvaluatorPreset
from agentops.pipeline.runtime import load_evaluator


def _preset(class_name: str = "CoherenceEvaluator") -> EvaluatorPreset:
    return EvaluatorPreset(
        name=class_name,
        class_name=class_name,
        score_key="coherence",
        input_mapping={"query": "$prompt", "response": "$prediction"},
    )


def _install_fake_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    evaluator_cls: type[Any] | None = None,
) -> type[Any]:
    """Stub azure-ai-evaluation without importing the real Azure SDK."""

    class CapturingEvaluator:
        kwargs: Dict[str, Any] = {}

        def __init__(self, **kwargs: Any) -> None:
            type(self).kwargs = kwargs

    installed_cls = evaluator_cls or CapturingEvaluator
    fake_azure = types.ModuleType("azure")
    fake_ai = types.ModuleType("azure.ai")
    fake_evaluation = types.ModuleType("azure.ai.evaluation")
    fake_azure.ai = fake_ai  # type: ignore[attr-defined]
    fake_ai.evaluation = fake_evaluation  # type: ignore[attr-defined]
    fake_evaluation.CoherenceEvaluator = installed_cls  # type: ignore[attr-defined]
    fake_evaluation.FluencyEvaluator = installed_cls  # type: ignore[attr-defined]
    fake_evaluation.SimilarityEvaluator = installed_cls  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure", fake_azure)
    monkeypatch.setitem(sys.modules, "azure.ai", fake_ai)
    monkeypatch.setitem(sys.modules, "azure.ai.evaluation", fake_evaluation)
    return installed_cls


def _configure_model_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deployment: str,
    override: str | None = None,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", deployment)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    if override is None:
        monkeypatch.delenv("AGENTOPS_EVALUATOR_REASONING_MODEL", raising=False)
    else:
        monkeypatch.setenv("AGENTOPS_EVALUATOR_REASONING_MODEL", override)


def _clear_explicit_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("AGENTOPS_EVALUATOR_REASONING_MODEL", raising=False)


def _load_with_deployment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deployment: str,
    override: str | None = None,
) -> Dict[str, Any]:
    evaluator_cls = _install_fake_evaluation(monkeypatch)
    _configure_model_env(monkeypatch, deployment=deployment, override=override)

    load_evaluator(_preset())

    return evaluator_cls.kwargs


def _model_direct_target(deployment: str) -> TargetResolution:
    return classify_agent(f"model:{deployment}")


@pytest.mark.parametrize(
    "deployment",
    ["gpt-5.1", "GPT-5-Judge", "o1-preview", "o3-mini", "o4"],
)
def test_load_evaluator_marks_reasoning_deployments(
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
) -> None:
    kwargs = _load_with_deployment(monkeypatch, deployment=deployment)

    assert kwargs["model_config"]["azure_deployment"] == deployment
    assert kwargs["is_reasoning_model"] is True


def test_load_evaluator_keeps_gpt_4o_mini_shape_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = _load_with_deployment(monkeypatch, deployment="gpt-4o-mini")

    assert kwargs["model_config"]["azure_deployment"] == "gpt-4o-mini"
    assert "is_reasoning_model" not in kwargs


def test_model_direct_defaults_to_target_deployment_and_foundry_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator_cls = _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://aifappframework.services.ai.azure.com/api/projects/models",
    )

    load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))

    model_config = evaluator_cls.kwargs["model_config"]
    assert model_config["azure_endpoint"] == (
        "https://aifappframework.services.ai.azure.com"
    )
    assert model_config["azure_deployment"] == "gpt-5.1"
    assert evaluator_cls.kwargs["is_reasoning_model"] is True


def test_explicit_evaluator_env_overrides_model_direct_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator_cls = _install_fake_evaluation(monkeypatch)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://target.services.ai.azure.com/api/projects/models",
    )
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://judge.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("AGENTOPS_EVALUATOR_REASONING_MODEL", raising=False)

    load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))

    model_config = evaluator_cls.kwargs["model_config"]
    assert model_config["azure_endpoint"] == "https://judge.openai.azure.com"
    assert model_config["azure_deployment"] == "gpt-4o-mini"
    assert "is_reasoning_model" not in evaluator_cls.kwargs


def test_deployment_only_override_keeps_foundry_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A separate judge inside the same Foundry project needs only a deployment."""
    evaluator_cls = _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://aifappframework.services.ai.azure.com/api/projects/models",
    )
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-443723")

    load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))

    model_config = evaluator_cls.kwargs["model_config"]
    assert model_config["azure_endpoint"] == (
        "https://aifappframework.services.ai.azure.com"
    )
    assert model_config["azure_deployment"] == "gpt-4.1-443723"
    # gpt-4.1 is not a reasoning model.
    assert "is_reasoning_model" not in evaluator_cls.kwargs


def test_deployment_only_override_via_foundry_alias_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AZURE_AI_MODEL_DEPLOYMENT_NAME is the Foundry-style alias and is sufficient."""
    evaluator_cls = _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://aifappframework.services.ai.azure.com/api/projects/models",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-443723")

    load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))

    model_config = evaluator_cls.kwargs["model_config"]
    assert model_config["azure_endpoint"] == (
        "https://aifappframework.services.ai.azure.com"
    )
    assert model_config["azure_deployment"] == "gpt-4.1-443723"


def test_deployment_only_override_requires_foundry_project_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-443723")

    with pytest.raises(RuntimeError, match="cannot derive an evaluator endpoint"):
        load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))


def test_endpoint_only_override_still_requires_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://judge.openai.azure.com")

    with pytest.raises(RuntimeError, match="no evaluator deployment"):
        load_evaluator(_preset(), target=_model_direct_target("gpt-5.1"))


def test_model_direct_defaults_raise_when_foundry_endpoint_cannot_be_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_evaluation(monkeypatch)
    _clear_explicit_model_env(monkeypatch)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://aifappframework.services.ai.azure.com/projects/models",
    )

    with pytest.raises(RuntimeError, match="Cannot derive evaluator endpoint") as excinfo:
        load_evaluator(_preset(), target=_model_direct_target("gpt-4o-mini"))

    message = str(excinfo.value)
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in message
    assert "AZURE_OPENAI_ENDPOINT" in message
    assert "AZURE_OPENAI_DEPLOYMENT" in message


@pytest.mark.parametrize("override", ["1", "true", "yes", "on", " TRUE "])
def test_reasoning_env_override_can_force_alias(
    monkeypatch: pytest.MonkeyPatch,
    override: str,
) -> None:
    kwargs = _load_with_deployment(
        monkeypatch,
        deployment="prod-judge-alias",
        override=override,
    )

    assert kwargs["is_reasoning_model"] is True


@pytest.mark.parametrize("override", ["0", "false", "no", "off", " OFF "])
def test_reasoning_env_override_can_disable_detection(
    monkeypatch: pytest.MonkeyPatch,
    override: str,
) -> None:
    kwargs = _load_with_deployment(
        monkeypatch,
        deployment="gpt-5.1",
        override=override,
    )

    assert "is_reasoning_model" not in kwargs


def test_invalid_reasoning_env_override_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_evaluation(monkeypatch)
    _configure_model_env(
        monkeypatch,
        deployment="gpt-4o-mini",
        override="maybe",
    )

    with pytest.raises(
        RuntimeError,
        match="AGENTOPS_EVALUATOR_REASONING_MODEL must be one of",
    ):
        load_evaluator(_preset())


def test_rejecting_reasoning_kwarg_raises_without_dropping_model_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StrictEvaluator:
        no_arg_call_count = 0

        def __init__(self, *, model_config: Dict[str, str] | None = None) -> None:
            if model_config is None:
                type(self).no_arg_call_count += 1

    _install_fake_evaluation(monkeypatch, StrictEvaluator)
    _configure_model_env(monkeypatch, deployment="gpt-5.1")

    with pytest.raises(
        RuntimeError,
        match="Failed to initialize evaluator 'CoherenceEvaluator'",
    ) as excinfo:
        load_evaluator(_preset())

    message = str(excinfo.value)
    assert "model_config" in message
    assert "is_reasoning_model" in message
    assert "upgrade azure-ai-evaluation" in message
    assert StrictEvaluator.no_arg_call_count == 0
