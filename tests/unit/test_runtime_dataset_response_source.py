from __future__ import annotations

from agentops.core.agentops_config import AgentOpsConfig, classify_agent
from agentops.core.evaluators import EvaluatorPreset
from agentops.pipeline import orchestrator, runtime


def test_dataset_response_source_does_not_invoke_target(monkeypatch) -> None:
    config = AgentOpsConfig(
        version=1,
        agent="https://example.test/chat",
        dataset="./qa.jsonl",
        response_source="dataset",
    )
    target = classify_agent(config.agent, config.protocol)
    latency = EvaluatorPreset(
        name="avg_latency_seconds",
        class_name="_latency",
        score_key="avg_latency_seconds",
        input_mapping={},
    )

    def fail_invoke(*args, **kwargs):
        raise AssertionError("target should not be invoked")

    monkeypatch.setattr(orchestrator.invocations, "invoke", fail_invoke)

    row = orchestrator._evaluate_row(
        row={"input": "hello", "response": "cached answer", "expected": "cached answer"},
        index=0,
        total=1,
        target=target,
        config=config,
        evaluators=[runtime.load_evaluator(latency)],
        timeout=1,
        progress=lambda _msg: None,
        rules_by_metric={},
    )

    assert row.error is None
    assert row.response == "cached answer"
    assert row.latency_seconds == 0.0
    assert row.metrics[0].name == "avg_latency_seconds"
    assert row.metrics[0].value == 0.0


def test_dataset_response_source_accepts_prediction_field() -> None:
    config = AgentOpsConfig(
        version=1,
        agent="https://example.test/chat",
        dataset="./qa.jsonl",
        response_source="dataset",
    )
    target = classify_agent(config.agent, config.protocol)
    latency = EvaluatorPreset(
        name="avg_latency_seconds",
        class_name="_latency",
        score_key="avg_latency_seconds",
        input_mapping={},
    )

    row = orchestrator._evaluate_row(
        row={"input": "hello", "prediction": "predicted answer"},
        index=0,
        total=1,
        target=target,
        config=config,
        evaluators=[runtime.load_evaluator(latency)],
        timeout=1,
        progress=lambda _msg: None,
        rules_by_metric={},
    )

    assert row.response == "predicted answer"
