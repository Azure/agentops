"""Reusable fakes for hosted Cockpit deployment tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeCommandResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeCommandRunner:
    results: list[FakeCommandResult] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, args: list[str], **_: Any) -> FakeCommandResult:
        self.calls.append(tuple(args))
        if self.results:
            return self.results.pop(0)
        return FakeCommandResult()


@dataclass
class FakeArmClient:
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    role_assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    mutations: list[tuple[str, str]] = field(default_factory=list)

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        return self.resources.get(resource_id.lower())

    def get_role_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        return self.role_assignments.get(assignment_id.lower())


@dataclass
class FakeGraphClient:
    application: dict[str, Any] = field(default_factory=dict)
    federated_credentials: list[dict[str, Any]] = field(default_factory=list)
    created_credentials: list[dict[str, Any]] = field(default_factory=list)

    def get_application(self, object_id: str) -> dict[str, Any]:
        return dict(self.application, id=object_id)

    def list_federated_credentials(self, _: str) -> list[dict[str, Any]]:
        return list(self.federated_credentials)

    def create_federated_credential(
        self, _: str, credential: dict[str, Any]
    ) -> dict[str, Any]:
        self.created_credentials.append(credential)
        return credential
