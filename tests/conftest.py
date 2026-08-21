"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from agentops.core.observe import ObserveScope


@pytest.fixture
def observe_scope_project() -> ObserveScope:
    return ObserveScope.model_validate(
        {
            "version": 1,
            "mode": "projects",
            "project_resource_ids": [
                "/subscriptions/00000000-0000-0000-0000-000000000001/"
                "resourceGroups/rg-agentops/providers/Microsoft.CognitiveServices/"
                "accounts/foundry/projects/project-a"
            ],
        }
    )


@pytest.fixture
def observe_scope_expanded() -> ObserveScope:
    return ObserveScope.model_validate(
        {
            "version": 1,
            "mode": "projects",
            "project_resource_ids": [
                "/subscriptions/00000000-0000-0000-0000-000000000001/"
                "resourceGroups/rg-agentops/providers/Microsoft.CognitiveServices/"
                "accounts/foundry/projects/project-a",
                "/subscriptions/00000000-0000-0000-0000-000000000002/"
                "resourceGroups/rg-agentops/providers/Microsoft.CognitiveServices/"
                "accounts/foundry/projects/project-b",
            ],
        }
    )


@pytest.fixture
def observe_runtime_context() -> dict[str, str]:
    return {
        "user_object_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }
