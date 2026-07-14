"""Unit tests for the Foundry operations dashboard service.

These cover the pure, Azure-free surface of
:mod:`agentops.services.dashboard`: template loading, the portal URL builder
(deployed vs. gallery fallback), the diagnostic-settings helpers, the ARM
template shape, and the RBAC preflight messaging with the authorization
helpers mocked.
"""

from __future__ import annotations

import json
from importlib.resources import files as package_files
from pathlib import Path

import pytest

from agentops.services import dashboard as dash


_FIXTURES = Path(__file__).parents[1] / "fixtures"
_EVALUATOR_KEYS = ("gen_ai.evaluation.name", "evaluator")
_SCORE_KEYS = (
    "gen_ai.evaluation.score.value",
    "gen_ai.evaluation.score",
    "score",
)
_LABEL_KEYS = (
    "gen_ai.evaluation.score.label",
    "gen_ai.evaluation.result",
    "label",
)


def _first_property(properties: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _fixture_projection(event: dict[str, object]) -> dict[str, object]:
    properties = event["properties"]
    assert isinstance(properties, dict)
    evaluator = _first_property(properties, _EVALUATOR_KEYS)
    score_text = _first_property(properties, _SCORE_KEYS)
    label = _first_property(properties, _LABEL_KEYS)
    try:
        numeric_score = float(score_text) if score_text else None
    except ValueError:
        numeric_score = None
    agent_id = str(properties.get("gen_ai.agent.id", ""))
    version = str(properties.get("gen_ai.agent.version", ""))
    if not version and ":" in agent_id:
        version = agent_id.split(":", 1)[1]
    return {
        "recognized": bool(evaluator or score_text or label),
        "numeric_score": numeric_score,
        "version": version or "Version not reported",
        "raw_properties": properties,
    }


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


def test_agent_behavior_tab_is_additive_and_preserves_existing_navigation() -> None:
    content = dash.load_workbook_content()
    tabs = next(item for item in content["items"] if item["name"] == "tabs")
    labels = [link["linkLabel"] for link in tabs["content"]["links"]]
    assert labels == [
        "Capacity",
        "Traffic and tokens",
        "Latency",
        "Errors and throttling",
        "Agent behavior",
    ]
    groups = {item["name"]: item for item in content["items"] if item["type"] == 12}
    assert {
        "group-capacity",
        "group-traffic",
        "group-latency",
        "group-errors",
    }.issubset(groups)
    behavior = groups["group-agent-behavior"]
    assert behavior["conditionalVisibility"]["value"] == "agent-behavior"


def test_agent_behavior_tab_surfaces_states_filters_and_preview_boundary() -> None:
    content = dash.load_workbook_content()
    behavior = next(
        item for item in content["items"] if item["name"] == "group-agent-behavior"
    )
    items = behavior["content"]["items"]
    note = next(item for item in items if item["name"] == "agent-behavior-note")
    note_text = note["content"]["json"]
    for state in (
        "Schema unavailable",
        "No access",
        "No data",
        "Filter empty",
        "Possible ingestion delay",
    ):
        assert state in note_text
    assert "Preview" in note_text
    assert "Foundry" in note_text
    assert "does not create, schedule, gate, or edit evaluations" in note_text

    filters = next(item for item in items if item["name"] == "agent-behavior-filters")
    assert [parameter["name"] for parameter in filters["content"]["parameters"]] == [
        "AgentEnvironment",
        "AgentName",
        "AgentVersion",
        "Evaluator",
    ]


def test_agent_behavior_queries_use_bounded_versioned_normalization() -> None:
    content = dash.load_workbook_content()
    behavior = next(
        item for item in content["items"] if item["name"] == "group-agent-behavior"
    )
    query_items = [
        item
        for item in behavior["content"]["items"]
        if item["type"] == 3 and "query" in item["content"]
    ]
    assert len(query_items) == 7
    assert all(
        item["content"]["timeContextFromParameter"] == "TimeRange"
        for item in query_items
    )
    combined = "\n".join(item["content"]["query"] for item in query_items)
    for fragment in (
        "set best_effort=true",
        "union isfuzzy=true",
        "AppEvents",
        "customEvents",
        "Name == 'gen_ai.evaluation.result'",
        "name == 'gen_ai.evaluation.result'",
        "Properties",
        "customDimensions",
        "gen_ai.evaluation.score.value",
        "gen_ai.evaluation.score.label",
        "Version not reported",
        "RawProperties",
    ):
        assert fragment in combined
    status = next(
        item for item in query_items if item["name"] == "agent-behavior-status"
    )
    status_query = status["content"]["query"]
    assert "agent_behavior/v1" in status_query
    assert "ObservedInvokeAgentInvocations" in status_query
    assert "EvaluatedTraces" in status_query
    assert "EvaluationEvents" in status_query
    assert "Coverage" not in status_query
    schema_diagnostics = next(
        item
        for item in query_items
        if item["name"] == "agent-behavior-schema-diagnostics"
    )
    assert "RawProperties" in schema_diagnostics["content"]["query"]
    assert "| take 100" in schema_diagnostics["content"]["query"]


def test_agent_behavior_does_not_combine_unlike_raw_score_scales() -> None:
    content = dash.load_workbook_content()
    behavior = next(
        item for item in content["items"] if item["name"] == "group-agent-behavior"
    )
    items = {item["name"]: item for item in behavior["content"]["items"]}
    assert items["agent-behavior-score-trend"]["content"]["visualization"] == "table"
    assert items["agent-behavior-pass-trend"]["content"]["visualization"] == "timechart"
    assert (
        items["agent-behavior-volume-trend"]["content"]["visualization"] == "timechart"
    )
    assert (
        "do not compare evaluators"
        in items["agent-behavior-score-trend"]["content"]["title"]
    )


def test_agent_behavior_authoring_query_is_packaged_and_bounded() -> None:
    resource = (
        package_files("agentops.templates")
        .joinpath("workbooks/queries/agent_behavior.kql")
        .read_text(encoding="utf-8")
    )
    assert "agent_behavior/v1" in resource
    assert "between (_startTime .. _endTime)" in resource
    assert "AppEvents" in resource and "customEvents" in resource
    assert "AppDependencies" in resource and "dependencies" in resource
    assert "RawProperties" in resource


def test_agent_behavior_schema_fixtures_cover_supported_shapes_and_edges() -> None:
    events = json.loads(
        (_FIXTURES / "workbook_agent_behavior_events.json").read_text(encoding="utf-8")
    )
    assert {event["source_table"] for event in events} == {
        "AppEvents",
        "customEvents",
    }
    expected_columns = {
        "AppEvents": ("Name", "Properties"),
        "customEvents": ("name", "customDimensions"),
    }
    projections = {}
    for event in events:
        name_column, properties_column = expected_columns[event["source_table"]]
        assert event["name_column"] == name_column
        assert event["properties_column"] == properties_column
        assert event["event_name"] == "gen_ai.evaluation.result"
        projection = _fixture_projection(event)
        projections[event["case"]] = projection
        assert projection["recognized"] is event["expected"]["recognized"]
        assert projection["numeric_score"] == event["expected"]["numeric_score"]
        assert projection["version"] == event["expected"]["version"]
        assert projection["raw_properties"] == event["properties"]

    assert projections["missing_optional_fields"]["version"] == "Version not reported"
    assert projections["nonnumeric_score"]["numeric_score"] is None
    assert projections["unrecognized_schema"]["recognized"] is False
    evaluators = {
        _first_property(event["properties"], _EVALUATOR_KEYS)
        for event in events
        if event["expected"]["recognized"]
    }
    assert {"Relevance", "Groundedness", "IntentResolution", "Fluency"} <= evaluators
    same_trace = [
        event for event in events if event["properties"].get("trace_id") == "trace-2"
    ]
    assert {
        _first_property(event["properties"], _EVALUATOR_KEYS) for event in same_trace
    } == {"Groundedness", "Fluency"}


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


def test_build_arm_template_location_defaults_to_resource_group_expression() -> None:
    # Workbooks reject "global"; the template must resolve to the RG region.
    target = dash.DashboardTarget(name="wb", subscription_id="sub", resource_group="rg")
    template = dash.build_arm_template(target=target)
    assert template["resources"][0]["location"] == "[resourceGroup().location]"


def test_build_arm_template_location_honors_explicit_region() -> None:
    target = dash.DashboardTarget(
        name="wb", subscription_id="sub", resource_group="rg", location="eastus2"
    )
    template = dash.build_arm_template(target=target)
    assert template["resources"][0]["location"] == "eastus2"


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
