"""Tests for category filtering and rule exclusion in `analyze`."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agentops.agent.analyzer import analyze
from agentops.agent.config import (
    AgentConfig,
    AzureResourcesSourceConfig,
    PostureCheckConfig,
    ResultsHistorySourceConfig,
    SourcesConfig,
    ChecksConfig,
)
from agentops.agent.findings import Category
from agentops.agent.sources.azure_resources import (
    AzureResourcesPayload,
    CognitiveAccountSnapshot,
    DeploymentSnapshot,
)


def _seed_runs(workspace: Path) -> None:
    """Seed three runs that trigger a coherence regression."""
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


def _insecure_resources_payload() -> AzureResourcesPayload:
    """A payload that triggers all 5 WAF security rules."""
    return AzureResourcesPayload(
        account=CognitiveAccountSnapshot(
            name="ai-test",
            disable_local_auth=False,
            public_network_access="Enabled",
            private_endpoint_count=0,
            network_acls_default_action="Allow",
            identity_type=None,
        ),
        deployments=[DeploymentSnapshot(name="legacy", model="gpt-3.5", rai_policy_name=None)],
        diagnostic_settings=[],
        diagnostics={"status": "ok"},
    )


def _agent_config_with_posture() -> AgentConfig:
    sources = SourcesConfig()
    sources.results_history = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    sources.azure_monitor.enabled = False
    sources.foundry_control.enabled = False
    sources.azure_resources = AzureResourcesSourceConfig(
        enabled=True,
        subscription_id="sub-1",
        resource_group="rg-1",
        cognitive_services_account="ai-test",
    )
    checks = ChecksConfig()
    checks.posture = PostureCheckConfig(enabled=True, pillar="security")
    return AgentConfig(sources=sources, checks=checks)


def test_categories_filter_keeps_only_security(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=_insecure_resources_payload(),
    ):
        result = analyze(tmp_path, config, categories=["security"])

    assert result.findings, "expected at least one security finding"
    assert {f.category for f in result.findings} == {Category.SECURITY}
    # The regression finding (quality) must have been filtered out.
    assert all(not f.id.startswith("regression.") for f in result.findings)


def test_categories_filter_keeps_only_quality(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=_insecure_resources_payload(),
    ):
        result = analyze(tmp_path, config, categories=["quality"])

    assert {f.category for f in result.findings} == {Category.QUALITY}


def test_invalid_categories_are_ignored(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=_insecure_resources_payload(),
    ):
        # All-invalid input → behave like no filter.
        result = analyze(tmp_path, config, categories=["bogus", "  "])

    categories = {f.category for f in result.findings}
    assert Category.SECURITY in categories
    assert Category.QUALITY in categories


def test_exclude_rules_skips_specific_posture_rule(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=_insecure_resources_payload(),
    ):
        result = analyze(
            tmp_path,
            config,
            categories=["security"],
            exclude_rules=["waf.security.local_auth_disabled"],
        )

    ids = {f.id for f in result.findings}
    assert "waf.security.local_auth_disabled" not in ids
    assert "waf.security.managed_identity" in ids


def test_exclude_rules_merges_with_config(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    config.checks.posture = PostureCheckConfig(
        enabled=True,
        pillar="security",
        exclude_rules=["waf.security.diagnostic_settings"],
    )
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=_insecure_resources_payload(),
    ):
        result = analyze(
            tmp_path,
            config,
            categories=["security"],
            exclude_rules=["waf.security.local_auth_disabled"],
        )

    ids = {f.id for f in result.findings}
    # Both the YAML-configured exclude AND the CLI exclude must apply.
    assert "waf.security.diagnostic_settings" not in ids
    assert "waf.security.local_auth_disabled" not in ids
    assert "waf.security.managed_identity" in ids


def test_resources_field_present_on_analysis_result(tmp_path: Path) -> None:
    _seed_runs(tmp_path)
    config = _agent_config_with_posture()
    payload = _insecure_resources_payload()
    with patch(
        "agentops.agent.analyzer.collect_azure_resources",
        return_value=payload,
    ):
        result = analyze(tmp_path, config)

    assert result.resources is payload
    assert result.diagnostics["azure_resources"] == {"status": "ok"}
