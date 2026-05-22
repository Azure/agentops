"""Tests for the Foundry prompt-agent deployment helper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentops.pipeline import prompt_deploy


def test_stage_prompt_agent_candidate_creates_version_and_candidate_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / ".agentops" / "prompts" / "agent-instructions.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.parent.mkdir(parents=True)
    prompt.write_text("new instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: support-agent:3",
                "dataset: data.jsonl",
                "prompt_file: .agentops/prompts/agent-instructions.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
            ]
        ),
        encoding="utf-8",
    )

    current = SimpleNamespace(
        id="agent-version-3",
        version="3",
        definition={"kind": "prompt", "model": "gpt-4o-mini", "instructions": "old"},
        metadata={},
    )
    created = SimpleNamespace(id="agent-version-4", version="4")
    captured = {}

    monkeypatch.setattr(
        prompt_deploy,
        "_get_agent_version",
        lambda endpoint, name, version: current,
    )

    def fake_create(endpoint, name, definition, *, metadata, description):
        captured["definition"] = definition
        captured["metadata"] = metadata
        captured["description"] = description
        return created

    monkeypatch.setattr(prompt_deploy, "_create_agent_version", fake_create)

    record = prompt_deploy.stage_prompt_agent_candidate(
        config_path=config,
        environment="dev",
        output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
        eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
    )

    assert record["action"] == "created"
    assert record["candidate_agent"] == "support-agent:4"
    assert captured["definition"]["instructions"] == "new instructions\n"
    assert captured["metadata"]["agentops.env"] == "dev"
    candidate_config = (tmp_path / ".agentops/deployments/agentops.candidate.yaml").read_text(
        encoding="utf-8"
    )
    assert "agent: support-agent:4" in candidate_config
    assert str(dataset) in candidate_config


def test_stage_prompt_agent_candidate_reuses_unchanged_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / "prompt.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.write_text("same instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: support-agent:3",
                "dataset: data.jsonl",
                "prompt_file: prompt.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
            ]
        ),
        encoding="utf-8",
    )
    current = SimpleNamespace(
        id="agent-version-3",
        version="3",
        definition={
            "kind": "prompt",
            "model": "gpt-4o-mini",
            "instructions": "same instructions\n",
        },
        metadata={},
    )

    monkeypatch.setattr(
        prompt_deploy,
        "_get_agent_version",
        lambda endpoint, name, version: current,
    )
    monkeypatch.setattr(
        prompt_deploy,
        "_create_agent_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected create")),
    )

    record = prompt_deploy.stage_prompt_agent_candidate(
        config_path=config,
        environment="qa",
        output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
        eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
    )

    assert record["action"] == "reused"
    assert record["candidate_agent"] == "support-agent:3"
