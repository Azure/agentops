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


def _make_not_found(status: int = 404) -> Exception:
    """Build an exception that ``_is_not_found_error`` will treat as 404."""

    exc = Exception("simulated foundry 404")
    setattr(exc, "status_code", status)
    return exc


def test_stage_prompt_agent_candidate_bootstraps_empty_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When the target Foundry project is empty (404) and bootstrap defaults
    are configured, the helper should create the first agent version from
    those defaults plus the prompt file, and record ``action: bootstrapped``.
    """

    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / "prompt.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.write_text("freshly authored instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: travel-agent:1",
                "dataset: data.jsonl",
                "prompt_file: prompt.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
                "prompt_agent_bootstrap:",
                "  model: gpt-4o-mini",
                "  description: Helps plan short trips and explains tradeoffs.",
                "  model_parameters:",
                "    temperature: 0.2",
            ]
        ),
        encoding="utf-8",
    )

    def fake_get(endpoint, name, version):
        raise _make_not_found()

    created = SimpleNamespace(id="agent-version-1", version="1")
    captured: dict = {}

    def fake_create(endpoint, name, definition, *, metadata, description):
        captured["definition"] = definition
        captured["metadata"] = metadata
        captured["description"] = description
        return created

    monkeypatch.setattr(prompt_deploy, "_get_agent_version", fake_get)
    monkeypatch.setattr(prompt_deploy, "_create_agent_version", fake_create)

    record = prompt_deploy.stage_prompt_agent_candidate(
        config_path=config,
        environment="dev",
        output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
        eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
    )

    assert record["action"] == "bootstrapped"
    assert record["candidate_agent"] == "travel-agent:1"
    assert captured["definition"]["kind"] == "prompt"
    assert captured["definition"]["model"] == "gpt-4o-mini"
    assert captured["definition"]["instructions"] == "freshly authored instructions\n"
    assert captured["definition"]["model_parameters"] == {"temperature": 0.2}
    assert captured["description"] == "Helps plan short trips and explains tradeoffs."
    assert captured["metadata"]["agentops.env"] == "dev"


def test_stage_prompt_agent_candidate_bootstrap_missing_config_raises_actionable_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When the target project is empty (404) but bootstrap defaults are not
    configured, the helper should raise a clear error guiding the user to
    add ``prompt_agent_bootstrap`` to ``agentops.yaml``.
    """

    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / "prompt.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.write_text("instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: travel-agent:1",
                "dataset: data.jsonl",
                "prompt_file: prompt.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
            ]
        ),
        encoding="utf-8",
    )

    def fake_get(endpoint, name, version):
        raise _make_not_found()

    monkeypatch.setattr(prompt_deploy, "_get_agent_version", fake_get)
    monkeypatch.setattr(
        prompt_deploy,
        "_create_agent_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected create")),
    )

    import pytest

    with pytest.raises(ValueError) as excinfo:
        prompt_deploy.stage_prompt_agent_candidate(
            config_path=config,
            environment="dev",
            output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
            eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
        )

    message = str(excinfo.value)
    assert "prompt_agent_bootstrap" in message
    assert "travel-agent:1" in message
    assert "model:" in message


def test_stage_prompt_agent_candidate_auth_errors_propagate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """403 / non-404 errors from ``get_version`` must NOT trigger the
    bootstrap path; they must surface so the operator sees the real cause.
    """

    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / "prompt.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.write_text("instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: travel-agent:1",
                "dataset: data.jsonl",
                "prompt_file: prompt.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
                "prompt_agent_bootstrap:",
                "  model: gpt-4o-mini",
            ]
        ),
        encoding="utf-8",
    )

    def fake_get(endpoint, name, version):
        raise _make_not_found(status=403)

    monkeypatch.setattr(prompt_deploy, "_get_agent_version", fake_get)
    monkeypatch.setattr(
        prompt_deploy,
        "_create_agent_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected create")),
    )

    import pytest

    with pytest.raises(Exception) as excinfo:
        prompt_deploy.stage_prompt_agent_candidate(
            config_path=config,
            environment="dev",
            output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
            eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
        )

    # The error should be the original 403, NOT a "missing bootstrap" hint.
    message = str(excinfo.value)
    assert "prompt_agent_bootstrap" not in message
    assert getattr(excinfo.value, "status_code", None) == 403


def test_stage_prompt_agent_candidate_ignores_bootstrap_when_seed_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When the seed already exists, ``prompt_agent_bootstrap`` must be
    ignored — the reuse / next-version flow takes over. Bootstrap only
    affects the not-found code path.
    """

    config = tmp_path / "agentops.yaml"
    dataset = tmp_path / "data.jsonl"
    prompt = tmp_path / "prompt.md"
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    prompt.write_text("same instructions\n", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: travel-agent:7",
                "dataset: data.jsonl",
                "prompt_file: prompt.md",
                "project_endpoint: https://example.services.ai.azure.com/api/projects/p",
                "prompt_agent_bootstrap:",
                "  model: gpt-4o",  # different from current seed's model
            ]
        ),
        encoding="utf-8",
    )
    current = SimpleNamespace(
        id="agent-version-7",
        version="7",
        definition={
            "kind": "prompt",
            "model": "gpt-4o-mini",  # bootstrap.model would override this if used
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
        environment="prod",
        output_path=tmp_path / ".agentops/deployments/foundry-agent.json",
        eval_config_path=tmp_path / ".agentops/deployments/agentops.candidate.yaml",
    )

    assert record["action"] == "reused"
    assert record["candidate_agent"] == "travel-agent:7"


def test_is_not_found_error_handles_404_and_rejects_others() -> None:
    """Sanity-check the not-found classifier so the bootstrap fallback stays
    narrow.
    """

    assert prompt_deploy._is_not_found_error(_make_not_found(404))
    assert not prompt_deploy._is_not_found_error(_make_not_found(403))
    assert not prompt_deploy._is_not_found_error(_make_not_found(500))
    assert not prompt_deploy._is_not_found_error(Exception("no status"))
