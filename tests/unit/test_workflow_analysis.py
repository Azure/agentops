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
    # Without prompt_agent_bootstrap, analyze must warn the user so first
    # deploy into an empty Foundry project does not 404.
    assert any(
        signal.key == "prompt_agent_bootstrap_missing" for signal in analysis.signals
    )


def test_analysis_silent_when_prompt_agent_bootstrap_present(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "agent: quickstart-agent:2",
                "prompt_file: .agentops/prompts/agent-instructions.md",
                "dataset: data.jsonl",
                "prompt_agent_bootstrap:",
                "  model: gpt-4o-mini",
            ]
        ),
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)

    assert not any(
        signal.key == "prompt_agent_bootstrap_missing" for signal in analysis.signals
    )


def test_analysis_recommends_cloud_eval_for_supported_prompt_agent(tmp_path: Path) -> None:
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
    markdown = render_workflow_analysis(analysis, "markdown")

    assert analysis.recommended_deploy_mode == "prompt-agent"
    assert analysis.recommended_eval_runner == "agentops-cloud"
    assert recommended_eval_runner(tmp_path) == "agentops-cloud"
    assert "builtin.coherence" in analysis.official_evaluators
    assert any(signal.key == "agentops_cloud_evaluation" for signal in analysis.signals)
    assert "Recommendation" in rendered
    assert "evaluate" in rendered
    assert "AgentOps cloud eval in Foundry" in rendered
    assert "Foundry cloud eval" in rendered
    assert "AgentOps threshold enforcement" in rendered
    assert "workflow edits" in rendered
    assert "not needed - generated workflow should work as-is" in rendered
    assert "Copilot skills" in rendered
    assert "not needed - no Copilot handoff for this project shape" in rendered
    assert "Foundry eval" in rendered
    assert "Run `agentops eval run` locally" in rendered
    assert "Set AZURE_OPENAI_DEPLOYMENT" in rendered
    assert analysis.next_steps[0].startswith("Run `agentops eval run` locally")
    assert analysis.next_steps[1].startswith("Set AZURE_OPENAI_DEPLOYMENT")
    assert "- [x]" not in rendered
    assert "builtin." not in rendered
    assert "| Check" not in rendered
    assert "JSON shape" not in rendered
    assert "| Check | Status | Explanation |" in markdown


def test_analysis_recommends_azd_eval_when_eval_yaml_exists(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\nexecution: azd\neval_recipe: eval.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"input": "Hello", "expected": "Hello!"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.yaml").write_text(
        """
name: quickstart-eval
evaluators:
  - name: builtin.coherence
  - name: travel_quality_rubric
    kind: rubric
    dimensions:
      - id: booking_accuracy
""".lstrip(),
        encoding="utf-8",
    )

    analysis = analyze_workflow_project(tmp_path)
    rendered = render_workflow_analysis(analysis, "text")

    assert analysis.recommended_eval_runner == "azd-ai-agent-eval"
    assert recommended_eval_runner(tmp_path) == "azd-ai-agent-eval"
    assert "booking_accuracy" in analysis.official_evaluators
    assert any(signal.key == "azd_ai_agent_eval" for signal in analysis.signals)
    assert "azd AI agent eval" in rendered
    assert analysis.next_steps[0].startswith("Run `agentops eval run` locally")
    assert "eval.yaml" in analysis.next_steps[0]
    assert "generated evaluator/rubric assets" in analysis.next_steps[0]


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
    assert "Recommendation" in result.stdout
    assert "deploy" in result.stdout
    assert "azd" in result.stdout
    assert "no Copilot handoff" in result.stdout
    assert "- [x]" not in result.stdout


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
