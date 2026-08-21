"""Parity checks for the published Observe scope JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentops.core.observe import ObserveScope


SCHEMA = (
    Path(__file__).parents[2]
    / "specs"
    / "011-deploy-hosted-cockpit"
    / "contracts"
    / "observe-scope.schema.json"
)


def test_model_modes_match_published_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    annotation = ObserveScope.model_fields["mode"].annotation
    assert set(annotation.__args__) == set(schema["properties"]["mode"]["enum"])


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "mode": "projects", "project_resource_ids": ["/bad"]},
        {"version": 1, "mode": "projects", "project_resource_ids": []},
        {
            "version": 1,
            "mode": "foundry",
            "root_resource_id": "/subscriptions/x",
        },
        {
            "version": 1,
            "mode": "resource_group",
            "root_resource_id": "/subscriptions/x/resourceGroups/rg",
            "project_resource_ids": ["/bad"],
        },
    ],
)
def test_invalid_schema_shapes_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ObserveScope.model_validate(payload)
