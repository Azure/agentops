"""Tests for reasoning-model grader support in the evaluator runtime.

``azure-ai-evaluation`` requires ``is_reasoning_model=True`` to rewrite the
chat-completions request (``max_tokens`` -> ``max_completion_tokens``) for
OpenAI o-series and GPT-5 graders. AgentOps resolves the flag from the grader
deployment via :func:`_evaluator_reasoning_enabled` and forwards it to
AI-assisted evaluators in :func:`load_evaluator`.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from agentops.core.evaluators import EvaluatorPreset
from agentops.pipeline import runtime
from agentops.pipeline.runtime import (
    _evaluator_reasoning_enabled,
    _looks_like_reasoning_model,
    load_evaluator,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AGENTOPS_EVALUATOR_REASONING",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    "deployment",
    [
        "o1",
        "o1-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gpt-5",
        "gpt5",
        "gpt-5-mini",
        "my-o3-grader",
    ],
)
def test_reasoning_names_detected(deployment: str) -> None:
    assert _looks_like_reasoning_model(deployment) is True


@pytest.mark.parametrize(
    "deployment",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-35-turbo",
        "my-grader",
        "",
    ],
)
def test_classic_names_not_detected(deployment: str) -> None:
    assert _looks_like_reasoning_model(deployment) is False


def test_env_true_overrides_classic_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_EVALUATOR_REASONING", "true")
    assert _evaluator_reasoning_enabled("gpt-4o") is True


def test_env_false_overrides_reasoning_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_EVALUATOR_REASONING", "false")
    assert _evaluator_reasoning_enabled("o3-mini") is False


def test_env_auto_falls_back_to_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_EVALUATOR_REASONING", "auto")
    assert _evaluator_reasoning_enabled("o3-mini") is True
    assert _evaluator_reasoning_enabled("gpt-4o") is False


class _FakeEvaluator:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __call__(self, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover
        return {}


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    fake_module = types.SimpleNamespace(CoherenceEvaluator=_FakeEvaluator)
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: fake_module,
    )


def _preset() -> EvaluatorPreset:
    return EvaluatorPreset(
        name="coherence",
        class_name="CoherenceEvaluator",
        score_key="coherence",
        input_mapping={"response": "$prediction"},
    )


def test_load_evaluator_passes_flag_for_reasoning_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "o3-mini")
    _install_fake_sdk(monkeypatch)

    rt = load_evaluator(_preset())

    assert rt.callable.kwargs.get("is_reasoning_model") is True
    assert rt.callable.kwargs["model_config"]["azure_deployment"] == "o3-mini"


def test_load_evaluator_omits_flag_for_classic_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    _install_fake_sdk(monkeypatch)

    rt = load_evaluator(_preset())

    assert "is_reasoning_model" not in rt.callable.kwargs
    assert rt.callable.kwargs["model_config"]["azure_deployment"] == "gpt-4o-mini"
