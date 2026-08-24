"""Contract tests for hosted Cockpit and Observe models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentops.core.observe import (
    AgentDetailRequest,
    CoverageResult,
    DeploymentJournal,
    GenerativeAIContent,
    MutationRecord,
    ObserveFilterState,
    ObserveQueryRequest,
    ObserveScope,
    ObservedAgent,
    ObservedRun,
    ObservedTool,
    ResultBounds,
    RoleAssignmentPlan,
    TraceContentRequest,
)


PROJECT = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
    "projects/project-a"
)


def test_project_scope_canonicalizes_and_contains_project() -> None:
    scope = ObserveScope(
        mode="projects",
        project_resource_ids=[PROJECT.upper(), PROJECT],
        default_project_resource_id=PROJECT,
    )

    assert scope.project_resource_ids == [PROJECT.lower()]
    assert scope.contains(PROJECT)
    assert not scope.contains(PROJECT.replace("project-a", "project-b"))


@pytest.mark.parametrize(
    ("mode", "resource_id"),
    [
        (
            "foundry",
            "/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry",
        ),
        (
            "resource_group",
            "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg",
        ),
        ("subscription", "/subscriptions/11111111-1111-1111-1111-111111111111"),
    ],
)
def test_parent_scope_accepts_only_matching_root(mode: str, resource_id: str) -> None:
    scope = ObserveScope(mode=mode, root_resource_id=resource_id)
    assert scope.contains(PROJECT)


def test_scope_rejects_cross_mode_fields() -> None:
    with pytest.raises(ValidationError):
        ObserveScope(
            mode="subscription",
            root_resource_id="/subscriptions/11111111-1111-1111-1111-111111111111",
            project_resource_ids=[PROJECT],
        )


def test_filter_range_must_be_ordered_and_inside_scope() -> None:
    scope = ObserveScope(mode="projects", project_resource_ids=[PROJECT])
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        ObserveFilterState(start=now, end=now - timedelta(minutes=1))

    filters = ObserveFilterState(
        project_resource_id=PROJECT,
        start=now - timedelta(hours=1),
        end=now,
    )
    filters.validate_scope(scope)

    with pytest.raises(ValueError, match="outside"):
        filters.model_copy(
            update={"project_resource_id": PROJECT.replace("project-a", "project-b")}
        ).validate_scope(scope)


def test_narrowing_filters_are_trimmed_and_length_bounded() -> None:
    now = datetime.now(timezone.utc)
    filters = ObserveFilterState(
        tool_name="  search  ",
        run_key="  conversation-1  ",
        start=now - timedelta(hours=1),
        end=now,
    )

    assert filters.tool_name == "search"
    assert filters.run_key == "conversation-1"
    for field in ("tool_name", "run_key"):
        with pytest.raises(ValidationError):
            ObserveFilterState(
                **{field: "   "},
                start=now - timedelta(hours=1),
                end=now,
            )
        with pytest.raises(ValidationError):
            ObserveFilterState(
                **{field: "x" * 257},
                start=now - timedelta(hours=1),
                end=now,
            )


def test_result_bounds_enforce_total_and_truncation_invariants() -> None:
    assert ResultBounds(rows_shown=0).rows_total_in_scope is None
    assert ResultBounds(rows_shown=500, rows_total_in_scope=501, truncated=True)

    with pytest.raises(ValidationError, match="less than"):
        ResultBounds(rows_shown=2, rows_total_in_scope=1)
    with pytest.raises(ValidationError, match="MAX_ROWS_PER_QUERY"):
        ResultBounds(rows_shown=499, truncated=True)
    with pytest.raises(ValidationError):
        ResultBounds(rows_shown=1, unexpected=True)


def test_observe_api_requests_are_strict_and_canonical() -> None:
    now = datetime.now(timezone.utc)
    filters = {
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": now.isoformat(),
    }

    query = ObserveQueryRequest(view="agents", filters=filters)
    detail = AgentDetailRequest(agent_key="agent-a", filters=filters, refresh=True)
    content = TraceContentRequest(
        source_resource_id=PROJECT.upper(),
        trace_id="trace-a",
    )

    assert query.refresh is False
    assert ObserveQueryRequest(view="tools", filters=filters).view == "tools"
    assert ObserveQueryRequest(view="runs", filters=filters).view == "runs"
    assert detail.refresh is True
    assert content.source_resource_id == PROJECT.lower()
    with pytest.raises(ValidationError):
        ObserveQueryRequest(view="unknown", filters=filters)
    with pytest.raises(ValidationError):
        TraceContentRequest(
            source_resource_id=PROJECT,
            trace_id="trace-a",
            token="must-not-be-accepted",
        )


def test_uami_role_plan_rejects_privileged_or_write_role() -> None:
    with pytest.raises(ValidationError):
        RoleAssignmentPlan(
            assignment_id=uuid4(),
            principal_id=uuid4(),
            role="Privileged Monitoring Data Reader",
            role_definition_id="/providers/Microsoft.Authorization/roleDefinitions/x",
            scope_resource_id=PROJECT,
            reason="not allowed",
        )


def test_journal_is_versioned_and_secret_free() -> None:
    journal = DeploymentJournal(
        attempt_id=uuid4(),
        selection_fingerprint="sha256:abc",
        mutations=[
            MutationRecord(
                target_resource_id=PROJECT,
                action="reuse",
                pre_existing=True,
                status="completed",
                resulting_resource_id=PROJECT,
            )
        ],
        resource_ids=[PROJECT],
        updated_at=datetime.now(timezone.utc),
    )

    payload = journal.model_dump_json()
    assert '"version":1' in payload
    assert "token" not in payload.lower()
    assert "secret" not in payload.lower()


def test_missing_coverage_is_not_numeric_zero() -> None:
    result = CoverageResult(
        source_id="source",
        dimension="token_usage",
        state="not_reported",
        reason="The source did not report token fields.",
        next_action="Enable supported semantic-convention fields.",
        refreshed_at=datetime.now(timezone.utc),
    )
    assert result.state == "not_reported"
    assert not hasattr(result, "value")


@pytest.mark.parametrize("dimension", ["tool_attribution", "run_correlation"])
def test_new_coverage_dimensions_are_accepted(dimension: str) -> None:
    result = CoverageResult(
        source_id="source",
        dimension=dimension,
        state="not_reported",
        reason="The source did not report the required attribution.",
        next_action="Enable supported semantic-convention fields.",
        refreshed_at=datetime.now(timezone.utc),
    )
    assert result.dimension == dimension


def test_observed_tool_requires_source_and_omits_token_fields() -> None:
    now = datetime.now(timezone.utc)
    fields = {
        "source_id": "source-a",
        "tool_name": "search",
        "agent_key": "agent-a",
        "source_kind": "foundry_hosted",
        "last_seen": now,
        "invocations": 2,
        "failures": 1,
    }
    tool = ObservedTool(**fields)
    assert tool.p95_latency_ms is None
    assert ObservedTool(**(fields | {"p95_latency_ms": 0.0})).p95_latency_ms == 0.0

    with pytest.raises(ValidationError):
        ObservedTool(**(fields | {"source_id": ""}))
    with pytest.raises(ValidationError, match="failures cannot exceed"):
        ObservedTool(**(fields | {"failures": 3}))
    for token_field in ("input_tokens", "output_tokens"):
        with pytest.raises(ValidationError):
            ObservedTool(**(fields | {token_field: 0}))


def test_observed_run_validates_counters_times_and_success() -> None:
    now = datetime.now(timezone.utc)
    fields = {
        "source_id": "source-a",
        "run_key": "conversation-a",
        "run_key_kind": "conversation",
        "agent_key": "agent-a",
        "source_kind": "foundry_prompt",
        "started_at": now - timedelta(minutes=1),
        "last_activity_at": now,
        "status": "succeeded",
        "turns": 2,
        "failed_turns": 0,
        "tool_invocations": 1,
        "tool_failures": 0,
    }
    run = ObservedRun(**fields)
    assert run.input_tokens is None
    assert run.output_tokens is None
    assert ObservedRun(**(fields | {"input_tokens": 0, "output_tokens": 0})).input_tokens == 0

    invalid_fields = (
        {"source_id": ""},
        {"turns": 0},
        {"failed_turns": 3},
        {"tool_failures": 2},
        {"last_activity_at": now - timedelta(minutes=2)},
        {"failed_turns": 1},
    )
    for changes in invalid_fields:
        with pytest.raises(ValidationError):
            ObservedRun(**(fields | changes))


@pytest.mark.parametrize(
    "source_kind",
    [
        "foundry_hosted",
        "foundry_prompt",
        "external_registered",
        "external_unregistered",
        "copilot_studio",
        "unknown",
    ],
)
def test_observed_agent_accepts_only_refined_runtime_kinds(source_kind: str) -> None:
    agent = ObservedAgent(
        source_id="source-a",
        key="agent-a",
        source_kind=source_kind,
        last_seen=datetime.now(timezone.utc),
        invocations=1,
        failures=0,
    )
    assert agent.source_kind == source_kind


@pytest.mark.parametrize("source_id", [None, ""])
def test_observed_agent_requires_nonempty_source_id(source_id: str | None) -> None:
    fields = {
        "key": "agent-a",
        "source_kind": "unknown",
        "last_seen": datetime.now(timezone.utc),
        "invocations": 1,
        "failures": 0,
    }
    if source_id is not None:
        fields["source_id"] = source_id
    with pytest.raises(ValidationError):
        ObservedAgent(**fields)


@pytest.mark.parametrize("source_kind", ["foundry", "external"])
def test_observed_agent_rejects_retired_runtime_kinds(source_kind: str) -> None:
    with pytest.raises(ValidationError):
        ObservedAgent(
            source_id="source-a",
            key="agent-a",
            source_kind=source_kind,
            last_seen=datetime.now(timezone.utc),
            invocations=1,
            failures=0,
        )


def test_denied_content_omits_raw_fields() -> None:
    content = GenerativeAIContent(
        trace_id="trace",
        source_resource_id=PROJECT,
        protection_state="protected_or_unavailable",
    )
    assert content.model_dump(exclude_none=True) == {
        "trace_id": "trace",
        "source_resource_id": PROJECT.lower(),
        "protection_state": "protected_or_unavailable",
    }
