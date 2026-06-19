"""Tests for :func:`agentops.pipeline.runtime._model_config`.

The covered behavior:

* ``AZURE_OPENAI_ENDPOINT`` is preferred when explicitly set.
* When absent, the endpoint is auto-derived from
  ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` — the CONTRIBUTING-stated promise
  that ``agentops init`` already satisfies for the project endpoint.
* When neither is available the error mentions both missing variables and
  hints the ``execution: cloud`` escape hatch for the deployment-only case.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentops.core.evaluators import EvaluatorPreset
from agentops.pipeline import runtime
from agentops.pipeline.runtime import _is_reasoning_model_deployment, _model_config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "AZURE_OPENAI_MODEL_NAME",
        "AZURE_AI_MODEL_NAME",
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)


def test_explicit_openai_endpoint_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://explicit.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://different.services.ai.azure.com/api/projects/p1",
    )

    cfg = _model_config()

    assert cfg["azure_endpoint"] == "https://explicit.openai.azure.com"
    assert cfg["azure_deployment"] == "gpt-4o-mini"


def test_endpoint_auto_derived_from_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://ai-account-xyz.services.ai.azure.com/api/projects/proj-default",
    )
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    cfg = _model_config()

    assert cfg["azure_endpoint"] == "https://ai-account-xyz.services.ai.azure.com"
    assert cfg["azure_deployment"] == "gpt-4o-mini"


def test_missing_deployment_still_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://acct.services.ai.azure.com/api/projects/p1",
    )

    with pytest.raises(RuntimeError) as excinfo:
        _model_config()

    message = str(excinfo.value)
    assert "AZURE_OPENAI_DEPLOYMENT" in message
    assert "AZURE_OPENAI_ENDPOINT" not in message
    # The hint nudges the user toward the cloud-execution escape hatch.
    assert "execution: cloud" in message


def test_missing_both_lists_endpoint_and_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        _model_config()

    message = str(excinfo.value)
    assert "AZURE_OPENAI_ENDPOINT" in message
    assert "AZURE_OPENAI_DEPLOYMENT" in message


def test_default_api_version_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    cfg = _model_config()

    assert cfg["api_version"] == "2025-04-01-preview"


def test_api_version_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    cfg = _model_config()

    assert cfg["api_version"] == "2024-12-01-preview"


@pytest.mark.parametrize("deployment", ["gpt-5", "gpt-5.4-mini", "o1-preview", "o3-mini", "o4-mini"])
def test_reasoning_model_deployment_detection(deployment: str) -> None:
    assert _is_reasoning_model_deployment(deployment) is True


@pytest.mark.parametrize("deployment", ["gpt-4o-mini", "gpt-4.1", "my-chat-grader", ""])
def test_non_reasoning_model_deployment_detection(deployment: str) -> None:
    assert _is_reasoning_model_deployment(deployment) is False


def test_ai_assisted_evaluator_marks_reasoning_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini")
    captured: dict[str, object] = {}

    class FakeCoherenceEvaluator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda _name: SimpleNamespace(CoherenceEvaluator=FakeCoherenceEvaluator),
    )

    preset = EvaluatorPreset(
        name="coherence",
        class_name="CoherenceEvaluator",
        score_key="coherence",
        input_mapping={},
    )
    runtime.load_evaluator(preset)

    assert captured["model_config"]["azure_deployment"] == "gpt-5.4-mini"
    assert captured["is_reasoning_model"] is True


def test_ai_assisted_evaluator_marks_reasoning_model_name_when_deployment_is_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "chat")
    monkeypatch.setenv("AZURE_OPENAI_MODEL_NAME", "gpt-5-nano")
    captured: dict[str, object] = {}

    class FakeCoherenceEvaluator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda _name: SimpleNamespace(CoherenceEvaluator=FakeCoherenceEvaluator),
    )

    preset = EvaluatorPreset(
        name="coherence",
        class_name="CoherenceEvaluator",
        score_key="coherence",
        input_mapping={},
    )
    runtime.load_evaluator(preset)

    assert captured["model_config"]["azure_deployment"] == "chat"
    assert captured["is_reasoning_model"] is True


def test_ai_assisted_evaluator_leaves_chat_deployments_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    captured: dict[str, object] = {}

    class FakeCoherenceEvaluator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda _name: SimpleNamespace(CoherenceEvaluator=FakeCoherenceEvaluator),
    )

    preset = EvaluatorPreset(
        name="coherence",
        class_name="CoherenceEvaluator",
        score_key="coherence",
        input_mapping={},
    )
    runtime.load_evaluator(preset)

    assert captured["model_config"]["azure_deployment"] == "gpt-4o-mini"
    assert "is_reasoning_model" not in captured
