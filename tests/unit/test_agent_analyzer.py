"""Tests for the analyzer + Markdown report renderer."""

from __future__ import annotations

import json
from pathlib import Path

from agentops.agent.analyzer import analyze
from agentops.agent.config import AgentConfig, ResultsHistorySourceConfig, SourcesConfig
from agentops.agent.findings import Severity
from agentops.agent.report import render_report, short_chat_summary


def _seed_runs(workspace: Path) -> None:
    root = workspace / ".agentops" / "results"
    root.mkdir(parents=True, exist_ok=True)
    for idx, (run_id, ts, coh) in enumerate(
        [
            ("run-1", "2024-05-01T10:00:00Z", 4.5),
            ("run-2", "2024-05-02T10:00:00Z", 4.5),
            ("run-3", "2024-05-03T10:00:00Z", 3.0),
        ]
    ):
        run_dir = root / run_id
        run_dir.mkdir(exist_ok=True)
        (run_dir / "results.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "timestamp": ts,
                    "metrics": {"coherence": coh},
                    "summary": {
                        "run_pass": idx < 2,
                        "items_total": 5,
                        "items_passed_all": 4 if idx < 2 else 2,
                    },
                }
            ),
            encoding="utf-8",
        )


def _config_with_disabled_remote_sources() -> AgentConfig:
    sources = SourcesConfig()
    sources.results_history = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    sources.azure_monitor.enabled = False
    sources.foundry_control.enabled = False
    return AgentConfig(sources=sources)


def test_analyzer_produces_regression_finding(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    result = analyze(tmp_path, _config_with_disabled_remote_sources())

    ids = [f.id for f in result.findings]
    assert "regression.coherence" in ids
    assert result.max_severity == Severity.CRITICAL


def test_render_report_contains_verdict_and_findings(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    result = analyze(tmp_path, _config_with_disabled_remote_sources())

    report = render_report(result)
    assert "AgentOps Doctor Report" in report
    assert "Verdict:" in report
    assert "regression.coherence" in report
    assert "Recent runs" in report


def test_short_chat_summary_no_findings_path() -> None:
    from agentops.agent.analyzer import AnalysisResult

    summary = short_chat_summary(AnalysisResult())
    assert "No issues" in summary
