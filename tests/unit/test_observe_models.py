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
