from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services import eval_analysis
from agentops.services.eval_analysis import analyze_eval_project, render_eval_analysis
from agentops.services.dataset_source import DatasetSourceDiagnosis


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


def test_eval_analysis_recommends_dataset_fix_when_input_column_is_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.jsonl").write_text(
        '{"expected": "hi"}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.dataset_status == "missing_input_column"
    assert analysis.config_status == "incomplete"
    assert "agentops-dataset" in analysis.recommended_skills
    assert any("Create or fix the dataset JSONL" in step for step in analysis.next_steps)
    assert analysis.copilot_prompt is not None
    assert analysis.copilot_prompt.startswith("/agentops-dataset")


def test_eval_analysis_uses_shared_remote_resolver_and_reports_columns(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_uri = (
        "https://examplestorage.blob.core.windows.net/evals/golden.jsonl"
    )
    (tmp_path / "agentops.yaml").write_text(
        f"version: 1\nagent: support-agent:2\ndataset: {source_uri}\n",
        encoding="utf-8",
    )
    calls: list[object] = []

    def diagnose(value, **kwargs):
        calls.append((value, kwargs))
        return DatasetSourceDiagnosis(
            status="ready",
            source=source_uri,
            columns=frozenset({"input", "expected", "context"}),
            message="Remote dataset is ready.",
            access_checked=True,
        )

    monkeypatch.setattr(eval_analysis, "diagnose_dataset_source", diagnose)

    analysis = analyze_eval_project(tmp_path)

    assert len(calls) == 1
    assert analysis.dataset_status == "ready"
    assert analysis.config_status == "ready"
    assert analysis.scenario_hint == "rag"
    assert any(
        signal.key == "dataset_ref" and source_uri in signal.detail
        for signal in analysis.signals
    )


def test_eval_analysis_preserves_stable_remote_failure_diagnosis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_uri = (
        "https://examplestorage.dfs.core.windows.net/evals/denied.jsonl"
    )
    (tmp_path / "agentops.yaml").write_text(
        f"version: 1\nagent: support-agent:2\ndataset: {source_uri}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        eval_analysis,
        "diagnose_dataset_source",
        lambda value, **kwargs: DatasetSourceDiagnosis(
            status="authorization_failed",
            source=source_uri,
            columns=frozenset(),
            message=(
                f"authorization_failed: cannot read {source_uri}. Assign Storage "
                "Blob Data Reader and review ADLS ACLs."
            ),
            access_checked=True,
        ),
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.dataset_status == "authorization_failed"
    assert analysis.config_status == "incomplete"
    assert any("Storage Blob Data Reader" in warning for warning in analysis.warnings)
    assert not any("credential chain" in warning.lower() for warning in analysis.warnings)


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
    assert "Readiness" in result.stdout
    assert "config" in result.stdout
    assert "ready" in result.stdout
    assert "Copilot skills" in result.stdout
    assert "not needed - no Copilot handoff for eval setup" in result.stdout


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


HOSTED_AGENT_URL = (
    "https://acct.services.ai.azure.com/api/projects/proj/agents/helpdesk/versions/11"
)


def _write_reference_answer_project(root: Path, agent: str) -> None:
    """Write a project whose dataset has ``input`` + ``expected`` and nothing else."""

    (root / "data.jsonl").write_text(
        '{"input": "Why can the user not sign in?", "expected": "The token expired."}\n',
        encoding="utf-8",
    )
    (root / "agentops.yaml").write_text(
        f"version: 1\nagent: {agent}\ndataset: data.jsonl\n",
        encoding="utf-8",
    )


def test_eval_analysis_hosted_agent_with_expected_is_conversational(tmp_path: Path) -> None:
    """A hosted agent answering `input` is conversational, not model quality.

    Regression test for #363: `_scenario_hint` used to label any dataset with an
    `expected` column as model quality regardless of the resolved target.
    """

    _write_reference_answer_project(tmp_path, HOSTED_AGENT_URL)

    analysis = analyze_eval_project(tmp_path)

    assert analysis.target_kind == "foundry_hosted"
    assert analysis.scenario_hint == "conversational"
    assert "model_quality" not in analysis.classification


def test_eval_analysis_prompt_agent_with_expected_is_conversational(tmp_path: Path) -> None:
    _write_reference_answer_project(tmp_path, "quickstart-agent:2")

    analysis = analyze_eval_project(tmp_path)

    assert analysis.target_kind == "foundry_prompt"
    assert analysis.scenario_hint == "conversational"


def test_eval_analysis_model_target_with_expected_is_model_quality(tmp_path: Path) -> None:
    """Model targets keep the model-quality label; #363 must not overcorrect."""

    _write_reference_answer_project(tmp_path, "model:gpt-4o")

    analysis = analyze_eval_project(tmp_path)

    assert analysis.target_kind == "model_direct"
    assert analysis.scenario_hint == "model_quality"


def test_eval_analysis_text_softens_hosted_agent_kind(tmp_path: Path) -> None:
    """Rendered text must not leak the raw `foundry_hosted` kind string."""

    _write_reference_answer_project(tmp_path, HOSTED_AGENT_URL)

    text = render_eval_analysis(analyze_eval_project(tmp_path), "text")

    assert "Foundry hosted agent" in text
    assert "foundry_hosted" not in text


def test_eval_analysis_agentless_config_degrades_to_observability_only(tmp_path: Path) -> None:
    """`agentops eval analyze` is a read-only inspection: an agent-less config
    is a normal analyzed state (project observability only), not an error."""
    (tmp_path / "data.jsonl").write_text(
        '{"input": "hello", "expected": "hi"}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    analysis = analyze_eval_project(tmp_path)

    assert analysis.config_status == "observability_only"


def test_cli_eval_analyze_agentless_exits_zero(tmp_path: Path) -> None:
    """The CLI degrades gracefully (exit 0) for an agent-less workspace."""
    (tmp_path / "data.jsonl").write_text(
        '{"input": "hello", "expected": "hi"}\n',
        encoding="utf-8",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", "analyze", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "observability" in result.stdout.lower()
