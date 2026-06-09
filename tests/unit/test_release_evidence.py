from __future__ import annotations

import json
import os
from pathlib import Path

from agentops.agent.analyzer import AnalysisResult
from agentops.agent.findings import Category, Finding, Severity
from agentops.core.release_evidence import ReleaseEvidence
from agentops.pipeline.official_eval import OFFICIAL_EVAL_RUNNER
from agentops.services.evidence_pack import (
    build_release_evidence,
    write_release_evidence,
)


def _write_latest_results(workspace: Path, *, passed: bool = True, cloud: bool = False) -> None:
    latest = workspace / ".agentops" / "results" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "target": {"kind": "foundry_prompt", "raw": "support-agent:7"},
        "summary": {
            "overall_passed": passed,
            "items_total": 2,
            "items_passed_all": 2 if passed else 1,
        },
        "thresholds": [{"metric": "coherence", "passed": passed}],
        "metrics": {"coherence": 4.2, "run_pass": 1.0 if passed else 0.0},
    }
    if cloud:
        payload["config"] = {
            "cloud_evaluation": {
                "run_id": "evalrun_123",
                "report_url": "https://ai.azure.com/evaluations/evalrun_123",
            }
        }
    (latest / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_official_eval(
    workspace: Path,
    *,
    status: str | None = "success",
) -> None:
    directory = workspace / ".agentops" / "official-eval"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "runner": OFFICIAL_EVAL_RUNNER,
                "action": "microsoft/ai-agent-evals@v3-beta",
                "agent_ids": "support-agent:7",
                "deployment_name": "gpt-4o-mini",
                "data_path": str(directory / "input.json"),
                "items_total": 2,
                "official_evaluators": ["builtin.coherence"],
                "machine_readable_thresholds": False,
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    if status is not None:
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "runner": OFFICIAL_EVAL_RUNNER,
                    "status": status,
                    "system": "github-actions",
                    "machine_readable_thresholds": False,
                }
            ),
            encoding="utf-8",
        )


def test_build_release_evidence_blocks_without_eval(tmp_path: Path) -> None:
    evidence = build_release_evidence(tmp_path)

    assert evidence.version == 1
    assert evidence.status == "blocked"
    assert any("No latest evaluation result" in item for item in evidence.blockers)


def test_build_release_evidence_ready_with_warning_without_baseline(tmp_path: Path) -> None:
    _write_latest_results(tmp_path, passed=True)
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: support-agent:7\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "thresholds:\n"
        "  coherence: '>=4'\n",
        encoding="utf-8",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "agentops-pr.yml").write_text("name: AgentOps PR\n", encoding="utf-8")

    evidence = build_release_evidence(tmp_path)

    assert evidence.status == "ready_with_warnings"
    assert evidence.target == "support-agent:7"
    assert any(check.name == "Latest eval gate" and check.status == "ready" for check in evidence.checks)
    assert any("No baseline comparison" in warning for warning in evidence.warnings)


def test_build_release_evidence_includes_observability_links_and_rubric_status(
    tmp_path: Path,
) -> None:
    _write_latest_results(tmp_path, passed=True)
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:7\n"
        "dataset: .agentops/data/travel-conversations.jsonl\n"
        "dataset_kind: multi-turn\n"
        "execution: azd\n"
        "rubrics:\n"
        "  - name: travel-concierge-quality\n"
        "    evaluator: travel-concierge-quality\n"
        "    dimensions:\n"
        "      - name: task_success\n"
        "        description: Completes the requested trip plan.\n"
        "observability:\n"
        "  trace_sampling:\n"
        "    enabled: true\n"
        "    mode: foundry\n"
        "  trace_replay_url: https://ai.azure.com/traces/replay/abc\n"
        "  evaluations_url: https://ai.azure.com/evaluations/run-1\n"
        "  datasets_url: https://ai.azure.com/datasets/travel\n",
        encoding="utf-8",
    )

    evidence = build_release_evidence(tmp_path)

    assert evidence.observability["multi_turn_ready"] is True
    assert evidence.observability["rubrics_count"] == 1
    assert evidence.observability["trace_sampling_enabled"] is True
    assert evidence.observability["trace_replay_urls"] == [
        "https://ai.azure.com/traces/replay/abc"
    ]
    assert any(check.name == "Foundry observability" and check.status == "ready" for check in evidence.checks)
    labels = {link.label: link.url for link in evidence.links}
    assert labels["Foundry trace replay"] == "https://ai.azure.com/traces/replay/abc"
    assert labels["Foundry evaluation"] == "https://ai.azure.com/evaluations/run-1"
    assert labels["Foundry datasets"] == "https://ai.azure.com/datasets/travel"


def test_write_release_evidence_redacts_secret_values(tmp_path: Path) -> None:
    evidence = ReleaseEvidence(
        generated_at="2026-01-01T00:00:00+00:00",
        workspace=str(tmp_path),
        status="ready",
        target="InstrumentationKey=11111111-1111-1111-1111-111111111111",
        monitoring={
            "connection_string": (
                "InstrumentationKey=11111111-1111-1111-1111-111111111111;"
                "IngestionEndpoint=https://example.test"
            ),
            "Authorization": "Authorization: Bearer abc.def.ghi",
            "client_secret": "client_secret=super-secret",
        },
    )

    result = write_release_evidence(tmp_path, evidence=evidence)
    payload = result.json_path.read_text(encoding="utf-8")
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert "11111111-1111-1111-1111-111111111111" not in payload
    assert "abc.def.ghi" not in payload
    assert "super-secret" not in payload
    assert "InstrumentationKey=<redacted>" in payload
    assert "<redacted>" in markdown


def test_release_evidence_markdown_includes_doctor_finding_summary(tmp_path: Path) -> None:
    analysis = AnalysisResult(
        findings=[
            Finding(
                id="regression.coherence",
                severity=Severity.CRITICAL,
                category=Category.QUALITY,
                title="Regression detected on `coherence`",
                summary="Current run is below baseline.",
                recommendation="Review the prompt change and rerun the eval.",
                source="results_history",
            ),
            Finding(
                id="opex.no_thresholds",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="agentops.yaml has no explicit thresholds",
                summary="Defaults are being used.",
                recommendation="Add explicit release thresholds.",
                source="workspace",
            ),
        ]
    )

    result = write_release_evidence(tmp_path, analysis=analysis)
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert "## Doctor finding summary" in markdown
    assert "**Findings:** 2 (1 critical · 1 warning)" in markdown
    assert (
        "1. **critical** [quality] `regression.coherence` - "
        "Regression detected on `coherence`"
    ) in markdown
    assert (
        "2. **warning** [operational excellence] `opex.no_thresholds` - "
        "agentops.yaml has no explicit thresholds"
    ) in markdown


def test_build_release_evidence_blocks_successful_official_eval_without_threshold_evidence(tmp_path: Path) -> None:
    _write_official_eval(tmp_path, status="success")
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: support-agent:7\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "thresholds:\n"
        "  coherence: '>=4'\n",
        encoding="utf-8",
    )

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == OFFICIAL_EVAL_RUNNER
    assert evidence.official_eval["machine_readable_thresholds"] is False
    assert evidence.target == "support-agent:7"
    assert evidence.status == "blocked"
    assert any("does not emit AgentOps-normalized threshold evidence" in item for item in evidence.blockers)
    assert any("does not emit AgentOps-normalized threshold evidence" in item for item in evidence.warnings)


def test_build_release_evidence_blocks_failed_official_eval(tmp_path: Path) -> None:
    _write_official_eval(tmp_path, status="failed")

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == OFFICIAL_EVAL_RUNNER
    assert evidence.latest_eval["passed"] is False
    assert evidence.status == "blocked"
    assert any("Official AI Agent Evaluation did not complete successfully" in item for item in evidence.blockers)


def test_build_release_evidence_warns_when_official_eval_result_is_missing(tmp_path: Path) -> None:
    _write_official_eval(tmp_path, status=None)

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == OFFICIAL_EVAL_RUNNER
    assert evidence.latest_eval["passed"] is None
    assert evidence.status == "blocked"
    assert any("no AgentOps-normalized pass/fail result was recorded" in item for item in evidence.blockers)


def test_build_release_evidence_prefers_normalized_results_over_newer_official_eval(tmp_path: Path) -> None:
    _write_latest_results(tmp_path, passed=False)
    _write_official_eval(tmp_path, status="success")
    os.utime(tmp_path / ".agentops" / "results" / "latest" / "results.json", (100, 100))
    os.utime(tmp_path / ".agentops" / "official-eval" / "result.json", (200, 200))

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == "agentops-local"
    assert evidence.latest_eval["passed"] is False


def test_build_release_evidence_marks_cloud_eval_runner(tmp_path: Path) -> None:
    _write_latest_results(tmp_path, passed=True, cloud=True)

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == "agentops-cloud"
    assert evidence.latest_eval["foundry_report_url"] == "https://ai.azure.com/evaluations/evalrun_123"
