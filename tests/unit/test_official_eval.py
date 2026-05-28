from __future__ import annotations

import json
from pathlib import Path

from agentops.pipeline.official_eval import (
    AGENTOPS_CLOUD_RUNNER,
    AGENTOPS_LOCAL_RUNNER,
    OFFICIAL_EVAL_RUNNER,
    analyze_official_eval_support,
    main,
    prepare_official_eval,
    recommended_eval_runner,
)


def _write_prompt_config(root: Path, dataset: str = "data.jsonl") -> None:
    (root / "agentops.yaml").write_text(
        f"version: 1\nagent: support-agent:4\ndataset: {dataset}\n",
        encoding="utf-8",
    )


def _write_dataset(root: Path, row: dict[str, object] | None = None) -> None:
    payload = row or {"input": "Say hello", "expected": "Hello!"}
    (root / "data.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_analyze_official_eval_support_for_prompt_agent(tmp_path: Path) -> None:
    _write_prompt_config(tmp_path)
    _write_dataset(tmp_path)

    support = analyze_official_eval_support(tmp_path / "agentops.yaml")

    assert support.eligible is True
    assert support.runner == OFFICIAL_EVAL_RUNNER
    assert support.agent_ids == "support-agent:4"
    assert "builtin.coherence" in support.official_evaluators
    assert "builtin.text_similarity" in support.official_evaluators
    assert "builtin.response_completeness" in support.official_evaluators
    assert "builtin.f1_score" not in support.official_evaluators
    assert support.warnings == ()
    assert recommended_eval_runner(tmp_path) == AGENTOPS_CLOUD_RUNNER


def test_prepare_official_eval_writes_data_and_metadata(tmp_path: Path) -> None:
    _write_prompt_config(tmp_path)
    _write_dataset(tmp_path, {"input": "What is AgentOps?", "expected": "A release gate."})

    prepared = prepare_official_eval(
        tmp_path / "agentops.yaml",
        tmp_path / ".agentops" / "official-eval" / "input.json",
        deployment_name="gpt-4o-mini",
    )

    data = json.loads(prepared.data_path.read_text(encoding="utf-8"))
    metadata = json.loads(prepared.metadata_path.read_text(encoding="utf-8"))

    assert data["evaluators"][:2] == ["builtin.coherence", "builtin.fluency"]
    assert "builtin.response_completeness" in data["evaluators"]
    assert "builtin.f1_score" not in data["evaluators"]
    assert data["data"][0]["query"] == "What is AgentOps?"
    assert data["data"][0]["ground_truth"] == "A release gate."
    assert data["openai_graders"]["builtin.text_similarity"]["reference"] == "{{item.ground_truth}}"
    assert metadata["runner"] == OFFICIAL_EVAL_RUNNER
    assert metadata["deployment_name"] == "gpt-4o-mini"
    assert metadata["items_total"] == 1
    assert metadata["machine_readable_thresholds"] is False
    assert metadata["skipped_agentops_evaluators"] == ["avg_latency_seconds"]


def test_prepare_official_eval_records_preview_runner_refs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENTOPS_OFFICIAL_EVAL_ACTION", "placerda/ai-agent-evals@v3-beta")
    monkeypatch.setenv("AGENTOPS_OFFICIAL_EVAL_ADO_TASK", "AIAgentEvaluationPreview@2")
    _write_prompt_config(tmp_path)
    _write_dataset(tmp_path)

    prepared = prepare_official_eval(
        tmp_path / "agentops.yaml",
        tmp_path / ".agentops" / "official-eval" / "input.json",
        deployment_name="gpt-4o-mini",
    )

    metadata = json.loads(prepared.metadata_path.read_text(encoding="utf-8"))
    assert metadata["action"] == "placerda/ai-agent-evals@v3-beta"
    assert metadata["azure_devops_task"] == "AIAgentEvaluationPreview@2"


def test_support_falls_back_for_hosted_or_http_agent(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: https://example.test/chat\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    _write_dataset(tmp_path)

    support = analyze_official_eval_support(tmp_path / "agentops.yaml")

    assert support.eligible is False
    assert support.runner == AGENTOPS_LOCAL_RUNNER
    assert "only evaluates Foundry prompt agents" in support.reasons[0]


def test_support_rejects_missing_ground_truth(tmp_path: Path) -> None:
    _write_prompt_config(tmp_path)
    _write_dataset(tmp_path, {"input": "No expected answer"})

    support = analyze_official_eval_support(tmp_path / "agentops.yaml")

    assert support.eligible is False
    assert "expected" in support.reasons[0]


def test_prepare_cli_writes_github_outputs(tmp_path: Path) -> None:
    _write_prompt_config(tmp_path)
    _write_dataset(tmp_path)
    github_output = tmp_path / "github-output.txt"

    code = main(
        [
            "prepare",
            "--config",
            str(tmp_path / "agentops.yaml"),
            "--out",
            str(tmp_path / ".agentops" / "official-eval" / "input.json"),
            "--deployment-name",
            "gpt-4o-mini",
            "--github-output",
            str(github_output),
        ]
    )

    assert code == 0
    output = github_output.read_text(encoding="utf-8")
    assert "agent_ids=support-agent:4" in output
    assert "deployment_name=gpt-4o-mini" in output
