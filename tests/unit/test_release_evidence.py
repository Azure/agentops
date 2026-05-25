from __future__ import annotations

import json
import os
from pathlib import Path

from agentops.core.release_evidence import ReleaseEvidence
from agentops.pipeline.official_eval import OFFICIAL_EVAL_RUNNER
from agentops.services.evidence_pack import (
    build_release_evidence,
    write_release_evidence,
)


def _write_latest_results(workspace: Path, *, passed: bool = True) -> None:
    latest = workspace / ".agentops" / "results" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "results.json").write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )


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


def test_build_release_evidence_accepts_successful_official_eval(tmp_path: Path) -> None:
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
    assert evidence.latest_eval["passed"] is True
    assert evidence.official_eval["machine_readable_thresholds"] is False
    assert evidence.target == "support-agent:7"
    assert not any("No latest evaluation result" in item for item in evidence.blockers)
    assert any("Microsoft job result is the release gate" in item for item in evidence.ready)
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
    assert evidence.status == "ready_with_warnings"
    assert any("no pass/fail result was recorded" in item for item in evidence.warnings)


def test_build_release_evidence_prefers_newer_official_eval_over_stale_results(tmp_path: Path) -> None:
    _write_latest_results(tmp_path, passed=False)
    _write_official_eval(tmp_path, status="success")
    os.utime(tmp_path / ".agentops" / "results" / "latest" / "results.json", (100, 100))
    os.utime(tmp_path / ".agentops" / "official-eval" / "result.json", (200, 200))

    evidence = build_release_evidence(tmp_path)

    assert evidence.latest_eval["runner"] == OFFICIAL_EVAL_RUNNER
    assert evidence.latest_eval["passed"] is True
