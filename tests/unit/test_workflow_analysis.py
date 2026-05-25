from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.workflow_analysis import (
    analyze_workflow_project,
    recommended_deploy_mode,
    recommended_eval_runner,
    render_workflow_analysis,
)


runner = CliRunner()


def test_analysis_recommends_azd_for_azure_yaml(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: sample\n", encoding="utf-8")
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)

    assert analysis.recommended_deploy_mode == "azd"
    assert recommended_deploy_mode(tmp_path) == "azd"
    assert any(signal.key == "azd_project" for signal in analysis.signals)
    assert any(stage.owner == "azd" for stage in analysis.stages)


def test_analysis_recommends_prompt_agent_without_azd(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "agent: quickstart-agent:2",
                "prompt_file: .agentops/prompts/agent-instructions.md",
                "dataset: data.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)

    assert analysis.recommended_deploy_mode == "prompt-agent"
    assert analysis.classification == "Foundry prompt-agent project"
    assert any(signal.key == "prompt_file" for signal in analysis.signals)
    assert any("prompt_deploy stage" in " ".join(stage.commands) for stage in analysis.stages)


def test_analysis_recommends_official_eval_for_supported_prompt_agent(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"input": "Hello", "expected": "Hello!"}) + "\n",
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)
    rendered = render_workflow_analysis(analysis, "text")

    assert analysis.recommended_deploy_mode == "prompt-agent"
    assert analysis.recommended_eval_runner == "official-ai-agent-evaluation"
    assert recommended_eval_runner(tmp_path) == "official-ai-agent-evaluation"
    assert "builtin.coherence" in analysis.official_evaluators
    assert any(signal.key == "official_ai_agent_evaluation" for signal in analysis.signals)
    assert "Recommended eval runner: official-ai-agent-evaluation" in rendered


def test_analysis_uses_placeholder_for_generic_repo(tmp_path: Path) -> None:
    analysis = analyze_workflow_project(tmp_path)

    assert analysis.recommended_deploy_mode == "placeholder"
    assert analysis.classification == "custom AI application"
    assert analysis.requires_copilot_adaptation is True
    assert analysis.copilot_skills_installed is False
    assert analysis.copilot_prompt is not None
    assert "/agentops-workflow" in analysis.copilot_prompt
    assert "agentops skills install --platform copilot" in analysis.recommended_commands
    assert any("No agentops.yaml" in warning for warning in analysis.warnings)


def test_analysis_detects_landing_zone_and_network_isolation(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: accelerator\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        '{"ailz_version": "1.0.0", "components": ["hub", "spoke"]}',
        encoding="utf-8",
    )
    infra = tmp_path / "infra"
    infra.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "Invoke-PreflightChecks.ps1").write_text("Write-Host preflight\n", encoding="utf-8")
    (infra / "main.bicep").write_text(
        "resource pe 'Microsoft.Network/privateEndpoints@2023-05-01' = {}\n"
        "param NETWORK_ISOLATION bool = true\n",
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)

    assert analysis.classification == "Azure AI accelerator / landing-zone application"
    assert analysis.complexity.startswith("high")
    assert any(signal.key == "ailz_manifest" for signal in analysis.signals)
    assert any(signal.key == "ailz_preflight" for signal in analysis.signals)
    assert any(signal.key == "network_isolation" for signal in analysis.signals)
    assert any("self-hosted runner" in warning for warning in analysis.warnings)
    assert any("Invoke-PreflightChecks.ps1" in command for command in analysis.recommended_commands)
    assert any(stage.name == "AI Landing Zone preflight" for stage in analysis.stages)


def test_readme_network_hint_is_medium_confidence(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "This sample can use network isolation and private endpoints.",
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)
    hint = next(signal for signal in analysis.signals if signal.key == "network_isolation_hint")

    assert hint.confidence == "medium"
    assert not any(signal.key == "network_isolation" for signal in analysis.signals)
    assert not analysis.complexity.startswith("high")


def test_scan_ignores_generated_dependency_directories(tmp_path: Path) -> None:
    decoy = tmp_path / "node_modules" / "pkg"
    decoy.mkdir(parents=True)
    (decoy / "main.bicep").write_text(
        "resource fw 'Microsoft.Network/azureFirewalls@2023-05-01' = {}\n",
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)

    assert not any(signal.key == "bicep_infra" for signal in analysis.signals)
    assert not any(signal.key == "network_isolation" for signal in analysis.signals)


def test_json_render_has_stable_version(tmp_path: Path) -> None:
    analysis = analyze_workflow_project(tmp_path)

    data = json.loads(render_workflow_analysis(analysis, "json"))

    assert data["version"] == 1
    assert data["recommended_deploy_mode"] == "placeholder"
    assert data["recommended_eval_runner"] == "agentops-local"
    assert isinstance(data["signals"], list)
    assert isinstance(data["stages"], list)


def test_cli_workflow_analyze_text(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: sample\n", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "analyze", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "AgentOps workflow analysis" in result.stdout
    assert "Recommended deploy mode: azd" in result.stdout
    assert "Copilot skills installed: no" in result.stdout


def test_cli_workflow_analyze_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "analyze", "--dir", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["version"] == 1
    assert data["recommended_deploy_mode"] == "placeholder"


def test_cli_workflow_analyze_writes_output_file(tmp_path: Path) -> None:
    out = tmp_path / "analysis.md"

    result = runner.invoke(
        app,
        [
            "workflow",
            "analyze",
            "--dir",
            str(tmp_path),
            "--format",
            "markdown",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Wrote" in result.stdout
    assert out.read_text(encoding="utf-8").startswith("# AgentOps workflow analysis")


def test_cli_workflow_analyze_invalid_format_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "analyze", "--dir", str(tmp_path), "--format", "xml"],
    )

    assert result.exit_code == 1
    assert "--format must be text, markdown, or json" in result.output
