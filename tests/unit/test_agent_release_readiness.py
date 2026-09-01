from __future__ import annotations

from pathlib import Path

from agentops.agent.checks.release_readiness import run_release_readiness_check
from agentops.agent.sources.results_history import ResultsHistory


def test_release_findings_not_applicable_without_evaluation_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    findings = run_release_readiness_check(tmp_path, ResultsHistory(runs=[]), None)

    assert findings == []


def test_missing_eval_evidence_warns_when_target_is_configured(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:3\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    findings = run_release_readiness_check(tmp_path, ResultsHistory(runs=[]), None)

    assert "opex.release.no_eval_evidence" in {finding.id for finding in findings}
