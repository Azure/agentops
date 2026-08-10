"""Tests for the agent identity section of the release evidence bundle."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.release_evidence import ReleaseEvidence, ReleaseEvidenceCheck
from agentops.services.agent_identity import (
    AGENT_ID_ENV,
    AgentIdentityBlueprint,
    write_identity_record,
)
from agentops.services.evidence_pack import (
    _add_agent_identity_check,
    _agent_identity_status,
)


@pytest.fixture(autouse=True)
def _clear_agent_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)


def test_status_reports_not_registered_for_a_fresh_workspace(tmp_path: Path) -> None:
    status = _agent_identity_status(tmp_path)
    assert status["status"] == "not_registered"
    assert status["agent_id"] is None


def test_status_reports_registered_from_the_record(tmp_path: Path) -> None:
    write_identity_record(
        tmp_path,
        AgentIdentityBlueprint(
            app_id="app-1", object_id="object-1", display_name="support-agent"
        ),
    )
    status = _agent_identity_status(tmp_path)
    assert status["status"] == "registered"
    assert status["agent_id"] == "app-1"
    assert status["source"] == "record"
    assert status["display_name"] == "support-agent"
    assert status["object_id"] == "object-1"
    assert "record_path" in status


def test_status_reports_environment_sourced_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AGENT_ID_ENV, "app-from-ci")
    status = _agent_identity_status(tmp_path)
    assert status["status"] == "registered"
    assert status["agent_id"] == "app-from-ci"
    assert status["source"] == "environment"


def test_unregistered_identity_produces_an_actionable_warning() -> None:
    checks: list[ReleaseEvidenceCheck] = []
    warnings: list[str] = []
    ready: list[str] = []

    _add_agent_identity_check(
        checks, warnings, ready, {"status": "not_registered", "agent_id": None}
    )

    assert len(checks) == 1
    assert checks[0].status == "warning"
    assert checks[0].name == "Agent identity"
    assert ready == []
    assert len(warnings) == 1
    assert "agentops agent register" in warnings[0]


def test_registered_identity_produces_a_ready_signal() -> None:
    checks: list[ReleaseEvidenceCheck] = []
    warnings: list[str] = []
    ready: list[str] = []

    _add_agent_identity_check(
        checks, warnings, ready, {"status": "registered", "agent_id": "app-1"}
    )

    assert len(checks) == 1
    assert checks[0].status == "ready"
    assert warnings == []
    assert len(ready) == 1
    assert "app-1" in ready[0]


def test_release_evidence_model_accepts_the_agent_identity_section() -> None:
    """The model forbids extra keys, so the field must be declared."""

    evidence = ReleaseEvidence(
        generated_at="2026-01-01T00:00:00+00:00",
        workspace="/tmp/ws",
        status="ready",
        agent_identity={"status": "registered", "agent_id": "app-1"},
    )
    assert evidence.agent_identity["agent_id"] == "app-1"
    round_tripped = ReleaseEvidence.model_validate(evidence.model_dump())
    assert round_tripped.agent_identity == evidence.agent_identity


def test_release_evidence_defaults_agent_identity_to_an_empty_dict() -> None:
    evidence = ReleaseEvidence(
        generated_at="2026-01-01T00:00:00+00:00",
        workspace="/tmp/ws",
        status="ready",
    )
    assert evidence.agent_identity == {}
