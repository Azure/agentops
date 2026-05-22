from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.eval_analysis import analyze_eval_project, render_eval_analysis


runner = CliRunner()


def test_eval_analysis_ready_foundry_prompt_config(tmp_path: Path) -> None:
    (tmp_path / "data.jsonl").write_text(
        '{"input": "hello", "expected": "hi"}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.config_status == "ready"
    assert analysis.dataset_status == "ready"
    assert analysis.target_kind == "foundry_prompt"
    assert analysis.requires_copilot_adaptation is False
    assert "agentops eval run" in analysis.recommended_commands


def test_eval_analysis_missing_config_recommends_config_skill(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("A simple assistant app.", encoding="utf-8")

    analysis = analyze_eval_project(tmp_path)

    assert analysis.config_status == "missing"
    assert analysis.classification == "unconfigured AI project"
    assert analysis.requires_copilot_adaptation is True
    assert "agentops-config" in analysis.recommended_skills
    assert analysis.copilot_skills_installed is False
    assert analysis.copilot_prompt is not None
    assert "/agentops-config" in analysis.copilot_prompt
    assert "agentops skills install --platform copilot" in analysis.recommended_commands
    assert "agentops init" in analysis.recommended_commands


def test_eval_analysis_rag_without_dataset_recommends_dataset_and_eval_skills(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "RAG accelerator using Azure AI Search vector retrieval.",
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: model:gpt-4o\ndataset: missing.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.scenario_hint == "rag"
    assert analysis.dataset_status == "not_found"
    assert analysis.complexity.startswith("high")
    assert "agentops-dataset" in analysis.recommended_skills
    assert "agentops-eval" in analysis.recommended_skills
    assert analysis.copilot_prompt is not None
    assert "Copy" not in analysis.copilot_prompt


def test_eval_analysis_detects_tool_workflow_from_dataset_columns(tmp_path: Path) -> None:
    (tmp_path / "tools.jsonl").write_text(
        '{"input": "book it", "expected": "done", "tool_calls": [{"name": "book"}]}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: https://example.com/chat\ndataset: tools.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.scenario_hint == "agent_workflow"
    assert analysis.target_kind == "http_json"
    assert any(signal.key == "dataset_columns" for signal in analysis.signals)


def test_eval_analysis_ignores_generated_dependency_directories(tmp_path: Path) -> None:
    decoy = tmp_path / "node_modules" / "pkg"
    decoy.mkdir(parents=True)
    (decoy / "index.ts").write_text("const tool_calls = [];\n", encoding="utf-8")

    analysis = analyze_eval_project(tmp_path)

    assert not any(signal.key == "tool_signal" for signal in analysis.signals)


def test_eval_analysis_json_render_has_stable_version(tmp_path: Path) -> None:
    analysis = analyze_eval_project(tmp_path)

    data = json.loads(render_eval_analysis(analysis, "json"))

    assert data["version"] == 1
    assert data["config_status"] == "missing"
    assert isinstance(data["recommended_skills"], list)


def test_cli_eval_analyze_text(tmp_path: Path) -> None:
    (tmp_path / "data.jsonl").write_text(
        '{"input": "hello", "expected": "hi"}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: model:gpt-4o\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", "analyze", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "AgentOps eval analysis" in result.stdout
    assert "Config status: ready" in result.stdout
    assert "Copilot skills installed: no" in result.stdout


def test_cli_eval_analyze_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["eval", "analyze", "--dir", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["version"] == 1
    assert data["config_status"] == "missing"


def test_cli_eval_analyze_writes_output_file(tmp_path: Path) -> None:
    out = tmp_path / "eval-analysis.md"

    result = runner.invoke(
        app,
        [
            "eval",
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
    assert out.read_text(encoding="utf-8").startswith("# AgentOps eval analysis")


def test_cli_eval_analyze_invalid_format_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["eval", "analyze", "--dir", str(tmp_path), "--format", "xml"],
    )

    assert result.exit_code == 1
    assert "--format must be text, markdown, or json" in result.output

