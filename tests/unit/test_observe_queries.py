"""Tests for bounded KQL builders and batched query execution (T037/T039/T044/T048/T057)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from agentops.agent.observe.queries import (
    DEFAULT_LOOKBACK_HOURS,
    MAX_ROWS_PER_QUERY,
    MAX_SOURCES_PER_BATCH,
    SOURCE_TIMEOUT_SECONDS,
    TOKEN_CLASS_ALIASES,
    SourceQuery,
    SupersededRequestError,
    build_agent_detail_query,
    build_department_usage_query,
    build_drilldown_query,
    build_agents_query,
    build_appgenai_content_query,
    build_models_query,
    build_overview_query,
    build_runs_query,
    build_trends_query,
    build_tools_query,
    build_usage_query,
    build_user_usage_query,
    classify_appgenai_content_result,
    default_lookback_window,
    execute_source_batch,
)
from agentops.agent.observe.attribution import (
    classify_department_cardinality,
    principal_alias_user_keys,
    rank_and_fold_user_usage,
)
from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionUsage,
    derive_pseudonymous_user_key,
)
from agentops.core.cost import CostComponent
from agentops.core.observe import (
    GenerativeAIContent,
    ObserveFilterState,
    TelemetrySource,
)


def _filters(**overrides: Any) -> ObserveFilterState:
    start = overrides.pop("start", datetime(2024, 1, 1, tzinfo=timezone.utc))
    end = overrides.pop("end", datetime(2024, 1, 2, tzinfo=timezone.utc))
    return ObserveFilterState(start=start, end=end, **overrides)


_ATTRIBUTION_NAMESPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_ATTRIBUTION_TENANT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_PROJECT_ARM_ID = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
    "projects/project-a"
)
_FOUNDRY_ARM_ID = _PROJECT_ARM_ID.rsplit("/projects/", 1)[0]


def _cost_component() -> CostComponent:
    return CostComponent.model_validate(
        {
            "id": "shared-prod",
            "type": "provisioned_throughput",
            "billing_boundary": {
                "kind": "resource",
                "value": _FOUNDRY_ARM_ID,
            },
            "billed_source": "Shared production capacity",
            "billed_total": "100.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "commitment",
            "allocation_key": "total_tokens",
            "usage_match": {
                "source_resource_ids": [_FOUNDRY_ARM_ID],
                "project_resource_ids": [_PROJECT_ARM_ID],
                "agent_keys": ["agent-1"],
                "deployments": ["gpt-prod"],
                "models": ["gpt-4o"],
                "tool_names": ["lookup"],
            },
        }
    )


def _attribution_config() -> AttributionConfiguration:
    return AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": str(_ATTRIBUTION_NAMESPACE),
            "generation": 7,
            "departments": [
                {
                    "id": "engineering",
                    "label": "Engineering",
                    "user_keys": [
                        derive_pseudonymous_user_key(
                            deployment_namespace=_ATTRIBUTION_NAMESPACE,
                            generation=7,
                            tenant_id=_ATTRIBUTION_TENANT,
                            raw_identity="Alice@Example.com",
                        )
                    ],
                    "group_ids": [],
                },
                {
                    "id": "support",
                    "label": "Customer's Support",
                    "user_keys": [
                        derive_pseudonymous_user_key(
                            deployment_namespace=_ATTRIBUTION_NAMESPACE,
                            generation=7,
                            tenant_id=_ATTRIBUTION_TENANT,
                            raw_identity="bob@example.com",
                        )
                    ],
                    "group_ids": [],
                },
            ],
        }
    )


def _usage(invocations: int) -> AttributionUsage:
    return AttributionUsage(
        invocations=invocations,
        input_tokens=None,
        output_tokens=None,
        tool_invocations=None,
        active_session_seconds=None,
    )


# ---------------------------------------------------------------------------
# T037: KQL builders - bounds, early filters, 24h defaults.
# ---------------------------------------------------------------------------


def test_default_lookback_window_is_24_hours() -> None:
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    start, end = default_lookback_window(now=now)
    assert end == now
    assert end - start == timedelta(hours=DEFAULT_LOOKBACK_HOURS)


def test_overview_query_is_bounded_to_time_window_and_tables() -> None:
    query = build_overview_query(_filters())
    assert "AppDependencies" in query
    assert "AppRequests" in query
    assert "TimeGenerated between" in query
    assert "2024-01-01" in query
    assert "2024-01-02" in query
    assert 'operation_name == "invoke_agent"' in query
    assert 'TelemetryTable endswith "AppRequests"' in query
    assert 'TelemetryTable endswith "AppDependencies"' in query
    assert "percentileif" not in query
    assert "let candidates = materialize(" in query
    assert "has_request = countif(is_request_invocation) > 0" in query
    assert "p95_latency_ms = percentile(DurationMs, 95)" in query
    assert "max(p95_latency_ms)" not in query
    assert "| project invocations, failures, avg_latency_ms, p95_latency_ms" in query


def test_agents_query_applies_dimension_filters_and_bounds_rows() -> None:
    filters = _filters(
        project_resource_id="/subscriptions/11111111-1111-1111-1111-111111111111"
        "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
        "/projects/proj",
        agent_id="agent-1",
        model="gpt-4o",
    )
    query = build_agents_query(filters)
    assert "gen_ai.project.id" in query
    assert "gen_ai.azure_ai_project.id" in query
    assert "agent-1" in query
    assert "gpt-4o" in query
    assert (
        'coalesce(Properties["gen_ai.response.model"], '
        'Properties["gen_ai.request.model"])'
    ) in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "let total_in_scope = toscalar(agg | count);" in query
    assert "| extend total_in_scope = total_in_scope" in query
    assert 'Properties["gen_ai.provider.name"]' in query
    assert 'Properties["gen_ai.system"]' in query
    # Dimension filters must appear before the summarize (early filters).
    assert query.index("gen_ai.agent.id") < query.index("summarize")


def test_agent_and_model_queries_preserve_project_for_shared_workspace() -> None:
    project_a = (
        "/subscriptions/11111111-1111-1111-1111-111111111111"
        "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
        "/projects/project-a"
    )
    project_b = project_a.removesuffix("project-a") + "project-b"
    source = TelemetrySource(
        source_id="shared-workspace",
        resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111"
            "/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/logs"
        ),
        workspace_id="workspace-guid",
        project_resource_ids=[project_a, project_b],
        state="available",
    )

    agents_query = build_agents_query(_filters(), scope_source=source)
    models_query = build_models_query(_filters(), scope_source=source)

    assert "project_resource_id = tostring(coalesce(" in agents_query
    assert 'Properties["gen_ai.azure_ai_project.id"]' in agents_query
    assert "by project_resource_id, agent_key" in agents_query
    assert "agent_id = take_anyif(agent_id, isnotempty(agent_id))" in agents_query
    assert "model = take_anyif(model, isnotempty(model))" in agents_query
    assert "by project_resource_id, agent_key, agent_id, agent_name" not in agents_query
    assert "by project_resource_id, model, deployment" in models_query


def test_models_query_summarizes_by_model_and_deployment() -> None:
    query = build_models_query(_filters())
    assert (
        'model = tostring(coalesce(Properties["gen_ai.response.model"], '
        'Properties["gen_ai.request.model"]))'
    ) in query
    assert (
        'deployment = tostring(coalesce(Properties["gen_ai.request.deployment"], '
        'Properties["gen_ai.request.model"]))'
    ) in query
    assert "by project_resource_id, model, deployment" in query
    assert "| sort by requests desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "total_in_scope" in query


@pytest.mark.parametrize("builder", [build_agents_query, build_models_query])
def test_bounded_aggregate_queries_count_scope_then_take_max_rows(builder) -> None:
    query = builder(_filters())

    assert "let agg =" in query
    assert "let total_in_scope = toscalar(agg | count);" in query
    assert query.index("let total_in_scope") < query.index("\nagg\n")
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| extend total_in_scope = total_in_scope" in query


def test_token_class_aliases_are_unique_and_limited_to_usage_attributes() -> None:
    assert set(TOKEN_CLASS_ALIASES) == {"cache_read", "cache_write", "reasoning"}
    aliases = [alias for values in TOKEN_CLASS_ALIASES.values() for alias in values]
    assert all(alias.startswith("gen_ai.usage.") for alias in aliases)
    assert len(aliases) == len(set(aliases))


def test_models_query_projects_and_sums_normalized_token_classes() -> None:
    query = build_models_query(_filters())
    for token_class, aliases in TOKEN_CLASS_ALIASES.items():
        assert f"| extend {token_class}_tokens = toint(coalesce(" in query
        for alias in aliases:
            assert f'Properties["{alias}"]' in query
        assert f"{token_class}_tokens = sum({token_class}_tokens)" in query
    assert "by project_resource_id, model, deployment" in query


def test_models_query_preserves_missing_and_intermittent_class_reporting() -> None:
    query = build_models_query(_filters())
    assert 'operation_name !in ("invoke_agent", "execute_tool")' in query
    assert "token_reporting_records = countif(" in query
    for token_class in TOKEN_CLASS_ALIASES:
        reporting_records = f"{token_class}_reporting_records"
        assert (
            f"{reporting_records} = countif(isnotnull({token_class}_tokens))" in query
        )
        assert (
            f"{token_class}_tokens = iff({reporting_records} > 0, "
            f"{token_class}_tokens, long(null))"
        ) in query
        assert (
            f"{token_class}_tokens_partial = {reporting_records} > 0 and "
            f"{reporting_records} < token_reporting_records"
        ) in query


@pytest.mark.parametrize(
    ("view", "selector", "expected"),
    [
        ("agents", {"agent_key": "agent-a"}, "has_request_rows"),
        ("models", {"model": "gpt-5"}, "model == 'gpt-5'"),
        ("tools", {"tool_name": "weather"}, "tool_name == 'weather'"),
        ("runs", {"run_key": "run-a"}, "run_key == 'run-a'"),
    ],
)
def test_drilldown_query_is_bounded_and_metadata_only(
    view: Any, selector: dict[str, str], expected: str
) -> None:
    selector.update(
        source_id="source-1",
        project_resource_id=_PROJECT_ARM_ID,
    )
    query = build_drilldown_query(
        _filters(),
        view=view,
        selector=selector,
        limit=50,
    )

    assert expected in query
    assert f"tolower(project_resource_id) == '{_PROJECT_ARM_ID.lower()}'" in query
    assert "| take 51" in query
    assert "trace_id = tostring(OperationId)" in query
    assert (
        'deployment = tostring(coalesce(Properties["gen_ai.request.deployment"], '
        'Properties["gen_ai.request.model"]))'
    ) in query
    assert "Properties[\"gen_ai.prompt\"]" not in query
    assert "AppGenAIContent" not in query


def test_tool_drilldown_matches_the_tool_aggregate_semantics() -> None:
    query = build_drilldown_query(
        _filters(),
        view="tools",
        selector={
            "source_id": "source-1",
            "project_resource_id": _PROJECT_ARM_ID,
            "tool_name": "weather",
        },
    )

    assert "tool_name == 'weather'" in query
    assert 'operation_name == "execute_tool"' not in query


def test_models_query_projects_unmapped_usage_attributes_in_one_bounded_query() -> None:
    query = build_models_query(_filters())
    assert "materialize(" in query
    assert "bag_keys(Properties)" in query
    assert 'token_class_name startswith "gen_ai.usage."' in query
    assert "TOKEN_CLASS_ALIAS_NAMES" not in query
    for aliases in TOKEN_CLASS_ALIASES.values():
        for alias in aliases:
            assert f'"{alias}"' in query
    assert "token_class_value >= 0" in query
    assert "make_bag(pack(token_class_name, token_class_value))" in query
    assert "extra_token_classes" in query
    assert "join kind=leftouter" in query
    assert (
        query.count("union withsource=TelemetryTable AppDependencies, AppRequests") == 1
    )
    assert query.count("by project_resource_id, model, deployment") >= 2
    assert "| sort by requests desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| extend total_in_scope = total_in_scope" in query


def test_agents_query_aggregates_standard_granular_token_classes() -> None:
    query = build_agents_query(_filters())
    for token_class in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        assert f"{token_class} = sum({token_class})" in query
    for reporting_counter in (
        "cache_read_reporting_records",
        "cache_write_reporting_records",
        "reasoning_reporting_records",
    ):
        assert reporting_counter in query
    assert "extra_token_classes" not in query
    assert "bag_keys(Properties)" not in query


def test_combined_usage_query_remains_limited_to_input_and_output_tokens() -> None:
    query = build_usage_query(_filters())
    for token_class in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        assert token_class not in query
    assert "extra_token_classes" not in query
    assert "bag_keys(Properties)" not in query


def test_models_query_does_not_filter_by_runtime_or_model_family() -> None:
    query = build_models_query(_filters())
    for forbidden in (
        "runtime_type",
        "agent_type",
        "vendor",
        "Microsoft",
        "OpenAI",
        "Anthropic",
    ):
        assert forbidden not in query
    assert "| where provider_name" not in query


def test_usage_query_summarizes_tokens_by_agent_and_model() -> None:
    query = build_usage_query(_filters())
    assert "input_tokens = sum" in query
    assert "by agent_key, model" in query


def test_department_usage_query_composes_existing_filters_before_attribution() -> None:
    filters = _filters(
        project_resource_id=_PROJECT_ARM_ID,
        agent_id="agent-1",
        model="gpt-4o",
        tool_name="lookup",
        run_key="conversation-1",
    )

    query = build_department_usage_query(
        filters,
        config=_attribution_config(),
        tenant_id=_ATTRIBUTION_TENANT,
        department_id="engineering",
    )

    for expected in (
        "TimeGenerated between",
        _PROJECT_ARM_ID.lower(),
        "agent-1",
        "gpt-4o",
        "tool_name == 'lookup'",
        "run_key == 'conversation-1'",
        "department_id == 'engineering'",
    ):
        assert expected in query
    assert query.index("TimeGenerated between") < query.index("identity_state")


def test_cost_department_query_filters_real_dimensions_before_aggregation() -> None:
    query = build_department_usage_query(
        _filters(),
        config=_attribution_config(),
        tenant_id=_ATTRIBUTION_TENANT,
        scope_source=TelemetrySource(
            source_id="source-1",
            resource_id="/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law",
            foundry_resource_id=_FOUNDRY_ARM_ID,
            project_resource_ids=[_PROJECT_ARM_ID],
            workspace_id="workspace-1",
            state="available",
        ),
        cost_component=_cost_component(),
    )

    summarize = query.index("| summarize invocations")
    for expected in (
        f"| where tolower(project_resource_id) in ('{_PROJECT_ARM_ID.lower()}')",
        "| where agent_key in ('agent-1')",
        "| where deployment in ('gpt-prod')",
        "| where model in ('gpt-4o')",
        "| where tool_name in ('lookup')",
    ):
        assert expected in query
        assert query.index(expected) < summarize
    assert (
        'deployment = tostring(coalesce(Properties["gen_ai.request.deployment"], '
        'Properties["gen_ai.request.model"]))'
    ) in query
    assert "by department_id, department_label, mapping_state, identity_state" in query
    assert "provider_name" in query.rsplit("| project ", 1)[1]


def test_department_usage_query_uses_only_eligible_identity_aliases_and_conflicts() -> (
    None
):
    query = build_department_usage_query(
        _filters(), config=_attribution_config(), tenant_id=_ATTRIBUTION_TENANT
    )

    assert "UserAuthenticatedId" in query
    assert 'Properties["enduser.id"]' in query
    assert "trim(" in query
    assert "authenticated_id == otel_enduser_id" in query
    identity_section = query[
        query.index("authenticated_id =") : query.index(
            "| project-away authenticated_id"
        )
    ]
    assert "tolower(" not in identity_section
    assert '"identified"' in query
    assert '"not_reported"' in query
    assert '"ambiguous"' in query
    for forbidden in (
        "UserId",
        "enduser.pseudo.id",
        "SessionId",
        "session.id",
        "Device",
        "Browser",
        "ClientIP",
    ):
        assert forbidden not in query


def test_department_usage_query_derives_full_sha256_and_uses_one_mapping_table() -> (
    None
):
    query = build_department_usage_query(
        _filters(), config=_attribution_config(), tenant_id=_ATTRIBUTION_TENANT
    )

    canonical_prefix = (
        f"agentops-attribution-v1|{_ATTRIBUTION_NAMESPACE}|7|{_ATTRIBUTION_TENANT}|"
    )
    assert canonical_prefix in query
    assert 'strcat("usr1.g7.", hash_sha256(' in query
    assert "substring(" not in query
    assert query.count("datatable(") == 1
    assert query.count("join kind=leftouter") == 1
    assert "Customer\\'s Support" in query


def test_department_usage_query_projects_no_identity_or_user_key() -> None:
    query = build_department_usage_query(
        _filters(), config=_attribution_config(), tenant_id=_ATTRIBUTION_TENANT
    )

    final_projection = query.rsplit("| project ", 1)[1]
    for forbidden in (
        "UserAuthenticatedId",
        "enduser.id",
        "authenticated_id",
        "otel_enduser_id",
        "effective_identity",
        "raw_identity",
        "user_key",
    ):
        assert forbidden not in final_projection
    assert "ambiguous_records" in final_projection
    assert "member_count" in final_projection
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| sort by invocations desc, department_id asc, mapping_state asc" in query


def test_department_usage_query_collapses_validated_principal_alias_keys() -> None:
    config = _attribution_config()
    alias_keys = principal_alias_user_keys(
        config,
        tenant_id=str(_ATTRIBUTION_TENANT),
        principal_user_id="object-id-1",
        principal_user_name="Alice@Example.com",
    )

    query = build_department_usage_query(
        _filters(),
        config=config,
        tenant_id=_ATTRIBUTION_TENANT,
        principal_user_keys=alias_keys,
    )

    assert len(alias_keys) == 2
    assert all(f"'{key}'" in query for key in alias_keys)
    assert "principal_member = user_key in (" in query
    assert "not(principal_member)" in query
    assert "principal_member_present = max(toint(principal_member))" in query
    assert "member_count = nonprincipal_member_count + principal_member_present" in query
    final_projection = query.rsplit("| project ", 1)[1]
    assert "principal_member_present" in final_projection
    assert "user_key" not in final_projection


def test_department_cardinality_counts_principal_aliases_once_across_sources() -> None:
    rows = [
        {
            "department_id": "engineering",
            "member_count": 1,
            "principal_member_present": 1,
        },
        {
            "department_id": "engineering",
            "member_count": 1,
            "principal_member_present": 1,
        },
    ]

    assert not classify_department_cardinality(rows)
    rows[1] = {
        "department_id": "engineering",
        "member_count": 2,
        "principal_member_present": 1,
    }
    assert classify_department_cardinality(rows)


def test_department_usage_query_rejects_adversarial_principal_keys() -> None:
    with pytest.raises(ValueError, match="active-generation"):
        build_department_usage_query(
            _filters(),
            config=_attribution_config(),
            tenant_id=_ATTRIBUTION_TENANT,
            principal_user_keys=("usr1.g7." + "a" * 63 + "';",),
        )


def test_user_usage_query_uses_precomputed_allowlisted_keys_and_exact_user_key() -> (
    None
):
    config = _attribution_config()
    selected = config.departments[0].user_keys[0]
    query = build_user_usage_query(
        _filters(),
        config=config,
        tenant_id=_ATTRIBUTION_TENANT,
        department_id="engineering",
        selected_user_key=selected,
    )

    assert f"user_key == '{selected}'" in query
    assert f"user_key in ('{selected}')" in query
    assert "raw_identity ==" not in query
    assert "user_rank" not in query
    assert "other_users" not in query
    assert "union identified, unattributed" in query
    assert "invocations >" not in query


def test_cost_user_query_preserves_runtime_evidence_for_exact_matching() -> None:
    query = build_user_usage_query(
        _filters(),
        config=_attribution_config(),
        tenant_id=_ATTRIBUTION_TENANT,
        scope_source=TelemetrySource(
            source_id="source-1",
            resource_id="/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law",
            foundry_resource_id=_FOUNDRY_ARM_ID,
            project_resource_ids=[_PROJECT_ARM_ID],
            workspace_id="workspace-1",
            state="available",
        ),
        cost_component=_cost_component(),
    )

    assert "| where deployment in ('gpt-prod')" in query
    assert (
        'deployment = tostring(coalesce(Properties["gen_ai.request.deployment"], '
        'Properties["gen_ai.request.model"]))'
    ) in query
    assert "by user_key, raw_identity, project_resource_id" in query
    final_projection = query.rsplit("| project ", 1)[1]
    for dimension in ("agent_id", "agent_name", "provider_name", "system"):
        assert dimension in final_projection


@pytest.mark.parametrize("population", [499, 500, 501])
def test_user_usage_query_never_folds_per_source_at_global_boundaries(
    population: int,
) -> None:
    query = build_user_usage_query(
        _filters(),
        config=_attribution_config(),
        tenant_id=_ATTRIBUTION_TENANT,
    )

    assert population in (499, 500, 501)
    assert "| take 499" not in query
    assert "| take 500" not in query
    assert "| top 499" not in query
    assert "| top 500" not in query
    assert "other_users" not in query


@pytest.mark.parametrize(
    ("population", "visible_count", "omitted_count"),
    [(499, 499, 0), (500, 500, 0), (501, 499, 2)],
)
def test_global_user_ranking_has_exact_boundaries(
    population: int,
    visible_count: int,
    omitted_count: int,
) -> None:
    visible, omitted, other_usage = rank_and_fold_user_usage(
        (
            (f"usr1.g7.{index:064x}", _usage(1))
            for index in range(population)
        )
    )

    assert len(visible) == visible_count
    assert omitted == omitted_count
    assert (other_usage.invocations if other_usage else 0) == omitted_count


def test_global_user_ranking_merges_overlapping_sources_before_ranking() -> None:
    rows = [
        ("shared-user", _usage(260)),
        ("source-a-leader", _usage(400)),
        ("shared-user", _usage(260)),
        ("source-b-leader", _usage(401)),
    ]

    visible, omitted, other_usage = rank_and_fold_user_usage(rows)

    assert [key for key, _usage in visible] == [
        "shared-user",
        "source-b-leader",
        "source-a-leader",
    ]
    assert visible[0][1].invocations == 520
    assert omitted == 0
    assert other_usage is None


def test_user_usage_query_escapes_adversarial_source_filters_before_merge() -> None:
    query = build_user_usage_query(
        _filters(
            agent_id="agent' | take 1 //",
            tool_name="tool' | summarize count() //",
            run_key="run\\' | union * //",
        ),
        config=_attribution_config(),
        tenant_id=_ATTRIBUTION_TENANT,
    )

    assert "agent\\' | take 1 //" in query
    assert "tool_name == 'tool\\' | summarize count() //'" in query
    assert "run_key == 'run\\\\\\' | union * //'" in query
    assert query.count("let identified = identity_rows") == 1


def test_user_usage_query_rejects_unallowlisted_department_and_stale_key() -> None:
    config = _attribution_config()
    with pytest.raises(ValueError, match="allowlisted"):
        build_user_usage_query(
            _filters(),
            config=config,
            tenant_id=_ATTRIBUTION_TENANT,
            department_id="not-configured",
        )
    with pytest.raises(ValueError, match="active generation"):
        build_user_usage_query(
            _filters(),
            config=config,
            tenant_id=_ATTRIBUTION_TENANT,
            selected_user_key=f"usr1.g6.{'a' * 64}",
        )


def test_trends_query_is_bucketed_and_ordered() -> None:
    query = build_trends_query(_filters(), bucket=timedelta(minutes=30))
    assert "bin(TimeGenerated, 1800s)" in query
    assert "order by TimeGenerated asc" in query
    assert "| take 500" in query


def test_agent_detail_query_filters_to_single_agent_key() -> None:
    query = build_agent_detail_query(_filters(), agent_key="agent-42")
    assert "agent_key == 'agent-42'" in query
    assert "order by TimeGenerated asc" in query


def test_agent_detail_query_requires_agent_key() -> None:
    with pytest.raises(ValueError):
        build_agent_detail_query(_filters(), agent_key="")


def test_kql_string_filters_are_escaped_against_injection() -> None:
    filters = _filters(agent_id="o\\'brien")
    query = build_agents_query(filters)
    assert "o\\\\\\'brien" in query


def test_tool_filter_kql_metacharacters_remain_inside_the_string_literal() -> None:
    query = build_tools_query(_filters(tool_name="a' | take 1 //"))

    assert "tool_name == 'a\\' | take 1 //'" in query


def test_tools_query_uses_tool_metadata_without_reading_tool_content() -> None:
    query = build_tools_query(_filters(tool_name="lookup'o''ticket"))

    assert query.startswith(
        "let base = materialize(\n"
        "union withsource=TelemetryTable AppDependencies, AppRequests"
    )
    assert 'Properties["gen_ai.tool.name"]' in query
    assert 'Properties["gen_ai.operation.name"]' in query
    assert "| where isnotempty(tool_name)" in query
    assert "unattributed_count" in query
    assert "_metadata_only = true" in query
    assert "lookup\\'o\\'\\'ticket" in query
    assert "AppGenAIContent" not in query
    assert "gen_ai.tool.message" not in query
    assert (
        "| join kind=leftouter runtime_evidence "
        "on project_resource_id, agent_key, OperationId"
    ) in query
    assert (
        'runtime_evidence = base\n| where operation_name == "invoke_agent"'
    ) in query
    assert (
        "provider_name = iff(isnotempty(provider_name), "
        "provider_name, runtime_provider_name)"
    ) in query
    assert "provider_name = take_anyif(provider_name, isnotempty(provider_name))" in query
    assert "by project_resource_id, agent_key, tool_name" in query
    assert "| sort by invocations desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query


def test_runs_query_prefers_conversation_then_foundry_thread_before_trace() -> None:
    query = build_runs_query(_filters(run_key="run'o''key"))

    assert 'Properties["gen_ai.conversation.id"]' in query
    assert 'Properties["gen_ai.thread.id"]' in query
    assert (
        query.index("conversation_id")
        < query.index("foundry_thread_id")
        < query.index("tostring(OperationId)")
    )
    assert '"conversation", "trace"' in query
    assert (
        "| where isnotempty(conversation_id) or isnotempty(foundry_thread_id) "
        'or operation_name == "invoke_agent"'
    ) in query
    assert "run\\'o\\'\\'key" in query
    assert "| sort by last_activity_at desc" in query
    assert "turns = dcount(OperationId)" in query
    assert "provider_name = take_anyif(provider_name, isnotempty(provider_name))" in query
    assert "by project_resource_id, agent_key, run_key, run_key_kind" in query
    assert "input_token_reports = countif(isnotnull(input_tokens))" in query
    assert "iff(input_token_reports == 0, long(null), input_tokens)" in query


def test_runs_query_projects_granular_tokens_with_reporting_counts() -> None:
    query = build_runs_query(_filters())

    for token_class, aliases in TOKEN_CLASS_ALIASES.items():
        for alias in aliases:
            assert f'Properties["{alias}"]' in query
        assert f"{token_class}_tokens = sum({token_class}_tokens)" in query
        reports = f"{token_class}_token_reports"
        assert f"{reports} = countif(isnotnull({token_class}_tokens))" in query
        assert (
            f"{token_class}_tokens = iff({reports} == 0, long(null), "
            f"{token_class}_tokens)"
        ) in query


def test_runs_query_projects_only_direct_non_negative_credit_signals() -> None:
    query = build_runs_query(_filters())

    assert 'Properties["gen_ai.operation.name"]' in query
    assert 'Properties["gen_ai.usage.credits"]' in query
    assert "reported_credits >= 0" in query
    assert "credits = sum(credits)" in query
    assert "credit_reports = countif(isnotnull(credits))" in query
    assert "credits = iff(credit_reports == 0, decimal(null), credits)" in query
    assert "credit_event = iff(isnotempty(operation_name), 1, long(null))" in query
    assert "credit_events = sum(credit_event)" in query
    assert "credit_event_reports = countif(isnotnull(credit_event))" in query
    assert (
        "credit_events = iff(credit_event_reports == 0, long(null), credit_events)"
        in query
    )
    assert "by project_resource_id, agent_key, run_key, run_key_kind" in query
    assert "agent_id = take_anyif(agent_id, isnotempty(agent_id))" in query
    assert "provider_name = take_anyif(provider_name, isnotempty(provider_name))" in query
    assert "run_key, run_key_kind, operation_name" not in query

    for inferred_signal in ("credit_rate", "token_rate", "message_rate"):
        assert inferred_signal not in query


def test_runs_query_uses_exact_period_boundaries_and_retains_bounds() -> None:
    start = datetime(2024, 5, 1, 1, 2, 3, 456789, tzinfo=timezone.utc)
    end = datetime(2024, 5, 2, 4, 5, 6, 789012, tzinfo=timezone.utc)
    query = build_runs_query(_filters(start=start, end=end))

    assert (
        "TimeGenerated >= datetime(2024-05-01T01:02:03.456789Z) and "
        "TimeGenerated < datetime(2024-05-02T04:05:06.789012Z)"
    ) in query
    assert "TimeGenerated between" not in query
    assert "let total_in_scope = toscalar(agg | count);" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| extend total_in_scope = total_in_scope" in query


def test_runs_query_excludes_protected_content_and_payload_fields() -> None:
    query = build_runs_query(_filters())

    for forbidden in (
        "AppGenAIContent",
        "gen_ai.system.message",
        "gen_ai.user.message",
        "gen_ai.assistant.message",
        "gen_ai.tool.message",
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "Content",
    ):
        assert forbidden not in query


# ---------------------------------------------------------------------------
# T048/T039: AppGenAIContent correlation-key queries, no legacy fallback.
# ---------------------------------------------------------------------------


def test_appgenai_content_query_uses_correlation_keys_only() -> None:
    query = build_appgenai_content_query(trace_id="trace-1", span_id="span-1")
    assert query.startswith("AppGenAIContent")
    assert "TraceId == 'trace-1'" in query
    assert "SpanId == 'span-1'" in query
    assert "customDimensions" not in query
    assert "AppTraces" not in query
    assert "AppDependencies" not in query


def test_appgenai_content_query_requires_trace_id() -> None:
    with pytest.raises(ValueError):
        build_appgenai_content_query(trace_id="")


def test_classify_appgenai_content_zero_rows_is_protected_or_unavailable() -> None:
    content = classify_appgenai_content_result(
        [],
        source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y",
        trace_id="trace-1",
    )
    assert isinstance(content, GenerativeAIContent)
    assert content.protection_state == "protected_or_unavailable"
    assert content.input_messages is None
    assert content.output_messages is None


def test_classify_appgenai_content_maps_event_rows_to_fields() -> None:
    rows = [
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.system.message",
            "Content": "sys",
        },
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.user.message",
            "Content": "hi",
        },
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.assistant.message",
            "Content": "hello",
        },
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.tool.message",
            "Content": "tool",
        },
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.evaluation.result",
            "Content": "eval",
        },
    ]
    content = classify_appgenai_content_result(
        rows,
        source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y",
        trace_id="trace-1",
    )
    assert content.protection_state == "available"
    assert content.system_instructions == ["sys"]
    assert content.input_messages == ["hi"]
    assert content.output_messages == ["hello"]
    assert content.tool_content == ["tool"]
    assert content.evaluation_explanation == ["eval"]


def test_classify_appgenai_content_ignores_unknown_event_names_missing_dimension() -> (
    None
):
    rows = [
        {
            "TraceId": "trace-1",
            "SpanId": "span-1",
            "EventName": "gen_ai.unknown.event",
            "Content": "x",
        }
    ]
    content = classify_appgenai_content_result(
        rows,
        source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y",
        trace_id="trace-1",
    )
    assert content.protection_state == "available"
    assert content.input_messages is None
    assert content.output_messages is None
    assert content.system_instructions is None
    assert content.tool_content is None
    assert content.evaluation_explanation is None


# ---------------------------------------------------------------------------
# T044/T057: batched execution, classification, timeout/deadline, supersede.
# ---------------------------------------------------------------------------


@dataclass
class _FakeError:
    code: str
    message: str


@dataclass
class _FakeBatchItem:
    tables: Any = None
    error: _FakeError | None = None
    partial_error: _FakeError | None = None
    partial_data: Any = None
    status: str = "Success"


@dataclass
class FakeBatchClient:
    responses: list[Any] = field(default_factory=list)
    delay_seconds: float = 0.0
    received_requests: list[Any] = field(default_factory=list)

    async def query_batch(self, requests: list[Any]) -> list[Any]:
        self.received_requests = list(requests)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.responses


def _queries(n: int) -> list[SourceQuery]:
    return [
        SourceQuery(source_id=f"source-{i}", workspace_id=f"ws-{i}", query="table")
        for i in range(n)
    ]


def test_execute_source_batch_rejects_more_than_ten_sources() -> None:
    client = FakeBatchClient(responses=[])
    with pytest.raises(ValueError):
        asyncio.run(
            execute_source_batch(_queries(MAX_SOURCES_PER_BATCH + 1), client=client)
        )


def test_execute_source_batch_returns_empty_for_no_sources() -> None:
    client = FakeBatchClient(responses=[])
    result = asyncio.run(execute_source_batch([], client=client))
    assert result == []


def test_execute_source_batch_classifies_success_partial_throttled_and_error() -> None:
    queries = _queries(4)
    client = FakeBatchClient(
        responses=[
            _FakeBatchItem(tables=["ok"], status="Success"),
            _FakeBatchItem(
                partial_error=_FakeError(code="PartialError", message="slow table")
            ),
            _FakeBatchItem(
                error=_FakeError(
                    code="TooManyRequests", message="throttled, retry later"
                )
            ),
            _FakeBatchItem(
                error=_FakeError(code="BadGatewayTimeout", message="query timeout")
            ),
        ]
    )
    results = asyncio.run(execute_source_batch(queries, client=client))
    statuses = {result.source_id: result.status for result in results}
    assert statuses == {
        "source-0": "success",
        "source-1": "partial",
        "source-2": "throttled",
        "source-3": "timeout",
    }
    assert results[2].reason is not None


def test_execute_source_batch_maps_generic_errors_to_error_status() -> None:
    queries = _queries(1)
    client = FakeBatchClient(
        responses=[
            _FakeBatchItem(
                error=_FakeError(code="BadRequest", message="malformed query")
            )
        ]
    )
    results = asyncio.run(execute_source_batch(queries, client=client))
    assert results[0].status == "error"
    assert "malformed query" in results[0].reason


def test_execute_source_batch_enforces_request_deadline_as_timeout() -> None:
    queries = _queries(2)
    client = FakeBatchClient(
        responses=[_FakeBatchItem(), _FakeBatchItem()], delay_seconds=0.2
    )
    results = asyncio.run(
        execute_source_batch(queries, client=client, request_deadline_seconds=0.01)
    )
    assert all(result.status == "timeout" for result in results)


def test_execute_source_batch_forwards_source_timeout_to_requests() -> None:
    queries = _queries(1)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    asyncio.run(
        execute_source_batch(
            queries, client=client, source_timeout_seconds=SOURCE_TIMEOUT_SECONDS
        )
    )
    assert client.received_requests[0].server_timeout_seconds == SOURCE_TIMEOUT_SECONDS


def test_execute_source_batch_raises_when_superseded() -> None:
    queries = _queries(1)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    with pytest.raises(SupersededRequestError):
        asyncio.run(
            execute_source_batch(queries, client=client, is_superseded=lambda: True)
        )


def test_execute_source_batch_rejects_mismatched_response_count() -> None:
    queries = _queries(2)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    with pytest.raises(ValueError):
        asyncio.run(execute_source_batch(queries, client=client))
