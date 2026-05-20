"""Tests for the multi-layer safety check.

Covers the three independent layers — eval (results history), runtime
(Azure Monitor), and config (Foundry control-plane) — and asserts that
each layer fails open when its source is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agentops.agent.checks.safety import run_safety_check
from agentops.agent.config import SafetyCheckConfig
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload
from agentops.agent.sources.foundry_control import (
    EvaluationRuleSummary,
    FoundryAgentSummary,
    FoundryControlPayload,
)
from agentops.agent.sources.results_history import ResultsHistory, RunSummary


def _empty_history() -> ResultsHistory:
    return ResultsHistory(runs=[])


def _history_with_items(items: List[Dict[str, Any]]) -> ResultsHistory:
    run = RunSummary(
        run_id="run-1",
        timestamp=None,
        metrics={},
        run_pass=True,
        items_total=len(items),
        items_passed_all=len(items),
        raw_path=Path("."),
        item_evaluations=items,
    )
    return ResultsHistory(runs=[run])


# ---------------------------------------------------------------------------
# Eval layer
# ---------------------------------------------------------------------------


def test_eval_layer_emits_responsible_ai_finding_above_floor() -> None:
    history = _history_with_items(
        [
            {
                "input": "tell me a joke",
                "metrics": {"violence": "high"},
            }
        ]
    )
    findings = run_safety_check(history, SafetyCheckConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.id == "safety.violence"
    assert finding.category == Category.RESPONSIBLE_AI
    assert finding.severity == Severity.CRITICAL
    assert finding.evidence["layer"] == "eval"


def test_eval_layer_respects_severity_floor() -> None:
    history = _history_with_items(
        [{"input": "x", "metrics": {"violence": "low"}}]
    )
    # Default floor is medium -> low must be ignored.
    assert run_safety_check(history, SafetyCheckConfig()) == []


def test_eval_layer_no_history_still_allows_other_layers() -> None:
    # Empty history must not short-circuit downstream layers.
    monitor = AzureMonitorPayload(
        safety_violations=[{"signal": "content_filter", "hits": 3}]
    )
    findings = run_safety_check(_empty_history(), SafetyCheckConfig(), monitor=monitor)
    assert any(f.id == "safety.runtime.content_filter" for f in findings)


# ---------------------------------------------------------------------------
# Runtime layer
# ---------------------------------------------------------------------------


def test_runtime_layer_emits_warning_under_critical_threshold() -> None:
    monitor = AzureMonitorPayload(
        safety_violations=[{"signal": "content_filter", "hits": 2}]
    )
    findings = run_safety_check(
        _empty_history(), SafetyCheckConfig(), monitor=monitor
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "safety.runtime.content_filter"
    assert f.severity == Severity.WARNING
    assert f.category == Category.RESPONSIBLE_AI
    assert f.evidence == {
        "layer": "runtime",
        "signal": "content_filter",
        "hits": 2,
    }


def test_runtime_layer_escalates_to_critical_above_threshold() -> None:
    config = SafetyCheckConfig(runtime_critical_hits=5)
    monitor = AzureMonitorPayload(
        safety_violations=[{"signal": "content_filter", "hits": 7}]
    )
    findings = run_safety_check(_empty_history(), config, monitor=monitor)
    assert findings[0].severity == Severity.CRITICAL


def test_runtime_layer_ignores_hits_below_min() -> None:
    config = SafetyCheckConfig(min_runtime_hits=5)
    monitor = AzureMonitorPayload(
        safety_violations=[{"signal": "content_filter", "hits": 2}]
    )
    assert run_safety_check(_empty_history(), config, monitor=monitor) == []


def test_runtime_layer_no_payload_emits_nothing() -> None:
    assert run_safety_check(_empty_history(), SafetyCheckConfig()) == []


# ---------------------------------------------------------------------------
# Config layer
# ---------------------------------------------------------------------------


def test_config_layer_warns_when_evaluation_rules_missing() -> None:
    foundry = FoundryControlPayload(
        agents=[FoundryAgentSummary(agent_id="agent-1")],
        evaluation_rules=[],
        diagnostics={"evaluation_rules_count": 0},
    )
    findings = run_safety_check(
        _empty_history(), SafetyCheckConfig(), foundry=foundry
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "safety.config.continuous_eval_missing"
    assert f.category == Category.RESPONSIBLE_AI


def test_config_layer_warns_when_rule_disabled() -> None:
    foundry = FoundryControlPayload(
        agents=[FoundryAgentSummary(agent_id="agent-1")],
        evaluation_rules=[
            EvaluationRuleSummary(rule_id="r-1", enabled=True),
            EvaluationRuleSummary(rule_id="r-2", enabled=False),
        ],
        diagnostics={"evaluation_rules_count": 2},
    )
    findings = run_safety_check(
        _empty_history(), SafetyCheckConfig(), foundry=foundry
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "safety.config.continuous_eval_disabled"
    assert f.evidence["disabled_rules"] == ["r-2"]


def test_config_layer_silent_when_probe_unavailable() -> None:
    # No evaluation_rules_count/_warning in diagnostics -> probe was
    # not attempted (SDK lacks the surface); must not emit a false
    # positive.
    foundry = FoundryControlPayload(
        agents=[FoundryAgentSummary(agent_id="agent-1")],
        evaluation_rules=[],
        diagnostics={"status": "ok"},
    )
    assert (
        run_safety_check(_empty_history(), SafetyCheckConfig(), foundry=foundry)
        == []
    )


def test_config_layer_silent_when_no_agents() -> None:
    foundry = FoundryControlPayload(
        agents=[],
        diagnostics={"evaluation_rules_count": 0},
    )
    assert (
        run_safety_check(_empty_history(), SafetyCheckConfig(), foundry=foundry)
        == []
    )


# ---------------------------------------------------------------------------
# Multi-layer aggregation
# ---------------------------------------------------------------------------


def test_all_three_layers_can_coexist() -> None:
    history = _history_with_items(
        [{"input": "x", "metrics": {"violence": "high"}}]
    )
    monitor = AzureMonitorPayload(
        safety_violations=[{"signal": "content_filter", "hits": 3}]
    )
    foundry = FoundryControlPayload(
        agents=[FoundryAgentSummary(agent_id="agent-1")],
        evaluation_rules=[],
        diagnostics={"evaluation_rules_count": 0},
    )
    findings = run_safety_check(
        history, SafetyCheckConfig(), monitor=monitor, foundry=foundry
    )
    ids = {f.id for f in findings}
    assert ids == {
        "safety.violence",
        "safety.runtime.content_filter",
        "safety.config.continuous_eval_missing",
    }
    assert all(f.category == Category.RESPONSIBLE_AI for f in findings)
