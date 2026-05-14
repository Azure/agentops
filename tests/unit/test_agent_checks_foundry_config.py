"""Tests for the Foundry control-plane config check (GenAIOps)."""

from __future__ import annotations

from agentops.agent.checks.foundry_config import run_foundry_config_check
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.foundry_control import (
    FoundryAgentSummary,
    FoundryControlPayload,
)


def test_silent_when_source_disabled() -> None:
    foundry = FoundryControlPayload(diagnostics={"status": "disabled"})
    assert run_foundry_config_check(foundry) == []


def test_silent_when_none() -> None:
    assert run_foundry_config_check(None) == []


def test_emits_when_source_skipped() -> None:
    foundry = FoundryControlPayload(
        diagnostics={"status": "skipped", "reason": "no project_endpoint"}
    )
    findings = run_foundry_config_check(foundry)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "opex.no_foundry_control_configured"
    assert f.category == Category.OPERATIONAL_EXCELLENCE
    assert f.severity == Severity.WARNING
    assert f.evidence["mode"] == "not_configured"


def test_emits_when_source_ok_but_no_agents() -> None:
    foundry = FoundryControlPayload(
        agents=[],
        diagnostics={"status": "ok", "endpoint": "https://x.api.azureml.ms"},
    )
    findings = run_foundry_config_check(foundry)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "opex.no_foundry_agents"
    assert f.evidence["mode"] == "no_agents"
    assert f.evidence["endpoint"] == "https://x.api.azureml.ms"


def test_silent_when_source_ok_with_agents() -> None:
    foundry = FoundryControlPayload(
        agents=[FoundryAgentSummary(agent_id="a-1", name="my-agent")],
        diagnostics={"status": "ok"},
    )
    assert run_foundry_config_check(foundry) == []
