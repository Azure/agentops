from __future__ import annotations

from agentops.core.agentops_config import AgentOpsConfig, classify_agent
from agentops.core.evaluators import EvaluatorPreset
from agentops.pipeline import invocations, orchestrator, runtime


def test_http_json_captures_named_response_fields(monkeypatch) -> None:
    cfg = AgentOpsConfig(
        version=1,
        agent="https://example.test/chat",
        dataset="./qa.jsonl",
        protocol="http-json",
        request_field="question",
        response_fields={
            "response": "output.answer",
            "context": "output.context",
            "citations": "output.citations",
        },
    )
    target = classify_agent(cfg.agent, cfg.protocol)

    def fake_request_json(**_kwargs):
        return {
            "output": {
                "answer": "Use the reset page.",
                "context": ["Password reset article"],
                "citations": ["password.md"],
            }
        }

    monkeypatch.setattr(invocations, "_http_request_json", fake_request_json)

    result = invocations.invoke(
        target,
        cfg,
        {"input": "How do I reset my password?"},
        timeout=1,
    )

    assert result.response == "Use the reset page."
    assert result.metadata["response_fields"] == {
        "response": "Use the reset page.",
        "context": ["Password reset article"],
        "citations": ["password.md"],
    }


def test_response_fields_are_available_to_evaluator_mapping(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_evaluator(**kwargs):
        captured.update(kwargs)
        return {"score": 5}

    cfg = AgentOpsConfig(
        version=1,
        agent="https://example.test/chat",
        dataset="./qa.jsonl",
    )
    target = classify_agent(cfg.agent, cfg.protocol)
    monkeypatch.setattr(
        orchestrator.invocations,
        "invoke",
        lambda *_args, **_kwargs: invocations.InvocationResult(
            response="Use the reset page.",
            latency_seconds=0.25,
            metadata={
                "response_fields": {
                    "response": "Use the reset page.",
                    "context": ["Password reset article"],
                }
            },
        ),
    )
    evaluator = runtime.EvaluatorRuntime(
        preset=EvaluatorPreset(
            name="groundedness",
            class_name="GroundednessEvaluator",
            score_key="groundedness",
            input_mapping={
                "response": "$prediction",
                "context": "$response.context",
            },
        ),
        callable=fake_evaluator,
    )

    row = orchestrator._evaluate_row(
        row={"input": "question", "expected": "answer"},
        index=0,
        total=1,
        target=target,
        config=cfg,
        evaluators=[evaluator],
        timeout=1,
        progress=lambda _msg: None,
        rules_by_metric={},
    )

    assert row.response == "Use the reset page."
    assert row.context == '["Password reset article"]'
    assert captured == {
        "response": "Use the reset page.",
        "context": ["Password reset article"],
    }
