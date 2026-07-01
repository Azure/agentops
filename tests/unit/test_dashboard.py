"""Unit tests for the Foundry operations dashboard service.

These cover the pure, Azure-free surface of
:mod:`agentops.services.dashboard`: template loading, the portal URL builder
(deployed vs. gallery fallback), the diagnostic-settings helpers, the ARM
template shape, and the RBAC preflight messaging with the authorization
helpers mocked.
"""

from __future__ import annotations

import json

import pytest

from agentops.services import dashboard as dash


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------
def test_load_workbook_template_returns_valid_json() -> None:
    raw = dash.load_workbook_template()
    assert isinstance(raw, str) and raw.strip()
    parsed = json.loads(raw)
    # The packaged asset is a gallery-template workbook.
    assert isinstance(parsed, dict)


def test_load_workbook_content_matches_template() -> None:
    assert dash.load_workbook_content() == json.loads(dash.load_workbook_template())


# ---------------------------------------------------------------------------
# Portal URL
# ---------------------------------------------------------------------------
def test_make_workbook_resource_id_is_deterministic() -> None:
    a = dash.make_workbook_resource_id("sub", "rg", "AgentOps Foundry operations")
    b = dash.make_workbook_resource_id("sub", "rg", "AgentOps Foundry operations")
    assert a == b
    assert a.startswith("/subscriptions/sub/resourceGroups/rg/providers/")
    assert dash.WORKBOOK_RESOURCE_TYPE in a


def test_portal_url_deployed_when_target_known() -> None:
    url = dash.build_workbook_portal_url(
        subscription_id="sub",
        resource_group="rg",
        name="AgentOps Foundry operations",
        tenant_id="tenant-123",
    )
    assert url.startswith("https://portal.azure.com/#@tenant-123/resource")
    assert url.endswith("/workbook")


def test_portal_url_deployed_without_tenant() -> None:
    url = dash.build_workbook_portal_url(
        subscription_id="sub", resource_group="rg", name="wb"
    )
    assert url.startswith("https://portal.azure.com/#/resource")
    assert "@" not in url.split("/resource", 1)[0]


def test_portal_url_falls_back_to_gallery_when_target_unknown() -> None:
    url = dash.build_workbook_portal_url()
    assert "WorkbookMenuBlade" in url
    assert url.endswith("/gallery")


def test_portal_url_prefers_explicit_resource_id() -> None:
    rid = "/subscriptions/s/resourceGroups/g/providers/x/y/z"
    url = dash.build_workbook_portal_url(workbook_resource_id=rid, tenant_id="t")
    assert url == f"https://portal.azure.com/#@t/resource{rid}/workbook"


# ---------------------------------------------------------------------------
# Diagnostic settings
# ---------------------------------------------------------------------------
def test_missing_diagnostic_categories_none_when_all_present() -> None:
    assert (
        dash.missing_diagnostic_categories(
            ["RequestResponse", "AzureOpenAIRequestUsage", "Audit"]
        )
        == []
    )


def test_missing_diagnostic_categories_reports_absent() -> None:
    assert dash.missing_diagnostic_categories(["RequestResponse"]) == [
        "AzureOpenAIRequestUsage"
    ]
    assert dash.missing_diagnostic_categories([]) == list(
        dash.REQUIRED_DIAGNOSTIC_CATEGORIES
    )


def test_build_diagnostic_settings_command_shape() -> None:
    cmd = dash.build_diagnostic_settings_command(
        aoai_resource_id="/subscriptions/s/aoai",
        workspace_id="/subscriptions/s/ws",
    )
    assert cmd.startswith("az monitor diagnostic-settings create ")
    assert "--resource /subscriptions/s/aoai" in cmd
    assert "--workspace /subscriptions/s/ws" in cmd
    assert "RequestResponse" in cmd and "AzureOpenAIRequestUsage" in cmd


def test_build_diagnostic_settings_command_uses_placeholders() -> None:
    cmd = dash.build_diagnostic_settings_command(
        aoai_resource_id=None, workspace_id=None
    )
    assert "<azure-openai-resource-id>" in cmd
    assert "<log-analytics-workspace-id>" in cmd


# ---------------------------------------------------------------------------
# ARM template
# ---------------------------------------------------------------------------
def test_build_arm_template_shape() -> None:
    target = dash.DashboardTarget(
        name="AgentOps Foundry operations",
        subscription_id="sub",
        resource_group="rg",
        workspace_id="/subscriptions/sub/ws",
    )
    template = dash.build_arm_template(target=target)
    assert template["$schema"].startswith("https://schema.management.azure.com")
    assert len(template["resources"]) == 1
    resource = template["resources"][0]
    assert resource["type"] == dash.WORKBOOK_RESOURCE_TYPE
    assert resource["properties"]["displayName"] == "AgentOps Foundry operations"
    # serializedData is the workbook content re-serialized as a string.
    assert json.loads(resource["properties"]["serializedData"]) == (
        dash.load_workbook_content()
    )
    assert resource["properties"]["sourceId"] == "/subscriptions/sub/ws"


def test_build_arm_template_name_matches_resource_id() -> None:
    target = dash.DashboardTarget(name="wb", subscription_id="sub", resource_group="rg")
    template = dash.build_arm_template(target=target)
    rid = dash.make_workbook_resource_id("sub", "rg", "wb")
    assert template["resources"][0]["name"] == rid.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# RBAC preflight
# ---------------------------------------------------------------------------
def test_check_rbac_errors_without_subscription() -> None:
    result = dash.check_rbac(
        subscription_id=None, resource_group="rg", workspace_id=None
    )
    assert result.ok is False
    assert result.level == "error"
    assert any("subscription" in m.lower() for m in result.messages)


def _patch_rbac(monkeypatch, *, rg_roles, ws_roles=None, raise_error=None):
    """Install fakes for the lazy-imported authorization helpers."""
    import agentops.agent.checks._rbac_authorization as rbac_mod

    class _AuthErr(Exception):
        pass

    monkeypatch.setattr(rbac_mod, "AuthorizationCheckError", _AuthErr)
    monkeypatch.setattr(
        rbac_mod, "resolve_signed_in_principal_object_id", lambda: "principal-oid"
    )

    def _list(*, subscription_id, scope, principal_object_id):
        if raise_error is not None:
            raise _AuthErr(raise_error)
        if scope.endswith("/resourceGroups/rg"):
            return list(rg_roles)
        return list(ws_roles or [])

    monkeypatch.setattr(rbac_mod, "list_principal_role_definition_ids", _list)
    return _AuthErr


def test_check_rbac_passes_when_roles_present(monkeypatch) -> None:
    _patch_rbac(
        monkeypatch,
        rg_roles=[dash._ROLE_WORKBOOK_CONTRIBUTOR],
        ws_roles=[dash._ROLE_LOG_ANALYTICS_READER],
    )
    result = dash.check_rbac(
        subscription_id="sub",
        resource_group="rg",
        workspace_id="/subscriptions/sub/ws",
    )
    assert result.ok is True
    assert result.level == "ok"
    assert any("passed" in m.lower() for m in result.messages)


def test_check_rbac_fails_when_workbook_role_missing(monkeypatch) -> None:
    _patch_rbac(monkeypatch, rg_roles=[dash._ROLE_READER], ws_roles=[])
    result = dash.check_rbac(
        subscription_id="sub", resource_group="rg", workspace_id=None
    )
    assert result.ok is False
    assert result.level == "error"
    joined = " ".join(result.messages)
    assert "Workbook Contributor" in joined
    assert "'rg'" in joined
    assert "--dry-run" in joined


def test_check_rbac_fails_when_workspace_role_missing(monkeypatch) -> None:
    _patch_rbac(
        monkeypatch,
        rg_roles=[dash._ROLE_WORKBOOK_CONTRIBUTOR],
        ws_roles=[dash._ROLE_READER.replace("a", "z")],  # unrelated role guid
    )
    result = dash.check_rbac(
        subscription_id="sub",
        resource_group="rg",
        workspace_id="/subscriptions/sub/ws",
    )
    assert result.ok is False
    assert any("Log Analytics Reader" in m for m in result.messages)


def test_check_rbac_fails_open_on_authorization_error(monkeypatch) -> None:
    _patch_rbac(monkeypatch, rg_roles=[], raise_error="listing denied")
    result = dash.check_rbac(
        subscription_id="sub", resource_group="rg", workspace_id=None
    )
    # Fails OPEN: warn but allow deploy to proceed.
    assert result.ok is True
    assert result.level == "warn"
    assert any("listing denied" in m for m in result.messages)


def test_check_rbac_fails_open_when_helpers_unavailable(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "agentops.agent.checks._rbac_authorization":
            raise ImportError("no authorization helpers")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    result = dash.check_rbac(
        subscription_id="sub", resource_group="rg", workspace_id=None
    )
    assert result.ok is True
    assert result.level == "warn"


# ---------------------------------------------------------------------------
# deploy_workbook guards (no live Azure)
# ---------------------------------------------------------------------------
def test_deploy_workbook_requires_subscription() -> None:
    target = dash.DashboardTarget(name="wb")
    with pytest.raises(dash.DashboardError) as exc:
        dash.deploy_workbook(target=target)
    assert "subscription" in str(exc.value).lower()


def test_deploy_workbook_errors_when_az_missing(monkeypatch) -> None:
    monkeypatch.setattr(dash, "_az_executable", lambda: None)
    target = dash.DashboardTarget(name="wb", subscription_id="sub", resource_group="rg")
    with pytest.raises(dash.DashboardError) as exc:
        dash.deploy_workbook(target=target)
    assert "az" in str(exc.value).lower()
