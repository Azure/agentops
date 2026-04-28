"""Unit tests for the WAF-AI posture rule registry."""

from __future__ import annotations

from agentops.agent.checks.posture import run_posture_check
from agentops.agent.checks.posture_rules import RULE_REGISTRY
from agentops.agent.checks.posture_rules.content_filter import (
    evaluate as content_filter_rule,
)
from agentops.agent.checks.posture_rules.diagnostics import (
    evaluate as diagnostics_rule,
)
from agentops.agent.checks.posture_rules.local_auth import (
    evaluate as local_auth_rule,
)
from agentops.agent.checks.posture_rules.managed_identity import (
    evaluate as managed_identity_rule,
)
from agentops.agent.checks.posture_rules.network import evaluate as network_rule
from agentops.agent.config import PostureCheckConfig
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.azure_resources import (
    AzureResourcesPayload,
    CognitiveAccountSnapshot,
    DeploymentSnapshot,
    DiagnosticSettingSnapshot,
)


_SENTINEL = object()


def _payload(
    *,
    disable_local_auth: bool = True,
    public_network_access: str = "Disabled",
    private_endpoint_count: int = 0,
    network_acls_default_action: str | None = None,
    identity_type: str | None = "SystemAssigned",
    deployments=_SENTINEL,
    diagnostic_settings=_SENTINEL,
) -> AzureResourcesPayload:
    if deployments is _SENTINEL:
        deployments = [
            DeploymentSnapshot(name="gpt-4o", model="gpt-4o", rai_policy_name="Microsoft.Default")
        ]
    if diagnostic_settings is _SENTINEL:
        diagnostic_settings = [
            DiagnosticSettingSnapshot(
                name="default",
                workspace_id="/subscriptions/.../workspaces/log",
                enabled_log_categories=["Audit", "RequestResponse"],
            )
        ]
    return AzureResourcesPayload(
        account=CognitiveAccountSnapshot(
            name="ai-test",
            disable_local_auth=disable_local_auth,
            public_network_access=public_network_access,
            private_endpoint_count=private_endpoint_count,
            network_acls_default_action=network_acls_default_action,
            identity_type=identity_type,
        ),
        deployments=deployments,
        diagnostic_settings=diagnostic_settings,
        diagnostics={"status": "ok"},
    )


# ---------------------------------------------------------------------------
# local_auth_disabled
# ---------------------------------------------------------------------------


def test_local_auth_rule_passes_when_disabled() -> None:
    assert local_auth_rule(_payload(disable_local_auth=True), "azure_resources") == []


def test_local_auth_rule_fires_when_enabled() -> None:
    findings = local_auth_rule(_payload(disable_local_auth=False), "azure_resources")
    assert len(findings) == 1
    assert findings[0].id == "waf.security.local_auth_disabled"
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].category is Category.SECURITY
    assert findings[0].evidence["disable_local_auth"] is False


def test_local_auth_rule_fires_when_unknown() -> None:
    # `None` means "we don't know" — treat as a finding so the user investigates.
    findings = local_auth_rule(_payload(disable_local_auth=None), "azure_resources")
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# public_network_access
# ---------------------------------------------------------------------------


def test_network_rule_passes_when_disabled() -> None:
    assert network_rule(_payload(public_network_access="Disabled"), "azure_resources") == []


def test_network_rule_passes_when_private_endpoint() -> None:
    assert (
        network_rule(
            _payload(public_network_access="Enabled", private_endpoint_count=1),
            "azure_resources",
        )
        == []
    )


def test_network_rule_passes_when_acl_deny() -> None:
    assert (
        network_rule(
            _payload(
                public_network_access="Enabled",
                network_acls_default_action="Deny",
            ),
            "azure_resources",
        )
        == []
    )


def test_network_rule_fires_when_open() -> None:
    findings = network_rule(
        _payload(
            public_network_access="Enabled",
            private_endpoint_count=0,
            network_acls_default_action="Allow",
        ),
        "azure_resources",
    )
    assert len(findings) == 1
    assert findings[0].id == "waf.security.public_network_access"
    assert findings[0].severity is Severity.WARNING


# ---------------------------------------------------------------------------
# managed_identity
# ---------------------------------------------------------------------------


def test_managed_identity_rule_passes_when_system_assigned() -> None:
    assert (
        managed_identity_rule(_payload(identity_type="SystemAssigned"), "azure_resources")
        == []
    )


def test_managed_identity_rule_passes_when_user_assigned() -> None:
    assert (
        managed_identity_rule(_payload(identity_type="UserAssigned"), "azure_resources")
        == []
    )


def test_managed_identity_rule_fires_when_none() -> None:
    findings = managed_identity_rule(_payload(identity_type="None"), "azure_resources")
    assert len(findings) == 1


def test_managed_identity_rule_fires_when_missing() -> None:
    findings = managed_identity_rule(_payload(identity_type=None), "azure_resources")
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# diagnostic_settings
# ---------------------------------------------------------------------------


def test_diagnostics_rule_passes_when_workspace_and_categories() -> None:
    assert diagnostics_rule(_payload(), "azure_resources") == []


def test_diagnostics_rule_fires_when_no_destination() -> None:
    findings = diagnostics_rule(
        _payload(
            diagnostic_settings=[
                DiagnosticSettingSnapshot(
                    name="default",
                    workspace_id=None,
                    enabled_log_categories=["Audit"],
                )
            ]
        ),
        "azure_resources",
    )
    assert len(findings) == 1
    assert findings[0].id == "waf.security.diagnostic_settings"


def test_diagnostics_rule_fires_when_no_categories() -> None:
    findings = diagnostics_rule(
        _payload(
            diagnostic_settings=[
                DiagnosticSettingSnapshot(
                    name="default",
                    workspace_id="/some/workspace",
                    enabled_log_categories=[],
                )
            ]
        ),
        "azure_resources",
    )
    assert len(findings) == 1


def test_diagnostics_rule_fires_when_empty() -> None:
    findings = diagnostics_rule(
        _payload(diagnostic_settings=[]), "azure_resources"
    )
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# content_filter
# ---------------------------------------------------------------------------


def test_content_filter_rule_passes_when_all_have_policy() -> None:
    assert content_filter_rule(_payload(), "azure_resources") == []


def test_content_filter_rule_fires_when_any_missing() -> None:
    findings = content_filter_rule(
        _payload(
            deployments=[
                DeploymentSnapshot(
                    name="gpt-4o", model="gpt-4o", rai_policy_name="Microsoft.Default"
                ),
                DeploymentSnapshot(name="legacy", model="gpt-3.5", rai_policy_name=None),
            ]
        ),
        "azure_resources",
    )
    assert len(findings) == 1
    assert findings[0].evidence["deployments_missing_filter"] == [
        {"name": "legacy", "model": "gpt-3.5"}
    ]
    assert findings[0].severity is Severity.CRITICAL


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


def test_run_posture_check_returns_nothing_when_disabled() -> None:
    payload = _payload(disable_local_auth=False)
    findings = run_posture_check(payload, PostureCheckConfig(enabled=False))
    assert findings == []


def test_run_posture_check_returns_nothing_when_source_skipped() -> None:
    payload = AzureResourcesPayload(diagnostics={"status": "skipped"})
    findings = run_posture_check(payload, PostureCheckConfig(enabled=True))
    assert findings == []


def test_run_posture_check_aggregates_rules() -> None:
    payload = _payload(
        disable_local_auth=False,
        public_network_access="Enabled",
        identity_type=None,
    )
    findings = run_posture_check(payload, PostureCheckConfig(enabled=True))
    ids = {f.id for f in findings}
    assert "waf.security.local_auth_disabled" in ids
    assert "waf.security.public_network_access" in ids
    assert "waf.security.managed_identity" in ids
    assert all(f.category is Category.SECURITY for f in findings)


def test_run_posture_check_honours_excluded_rules() -> None:
    payload = _payload(disable_local_auth=False, identity_type=None)
    config = PostureCheckConfig(
        enabled=True, exclude_rules=["waf.security.local_auth_disabled"]
    )
    findings = run_posture_check(payload, config)
    ids = {f.id for f in findings}
    assert "waf.security.local_auth_disabled" not in ids
    assert "waf.security.managed_identity" in ids


def test_rule_registry_has_five_mvp_rules() -> None:
    assert set(RULE_REGISTRY.keys()) == {
        "waf.security.local_auth_disabled",
        "waf.security.public_network_access",
        "waf.security.managed_identity",
        "waf.security.diagnostic_settings",
        "waf.security.content_filter",
    }
