"""Tests for the agent identity section of the release evidence bundle."""

from __future__ import annotations

import json
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
    build_release_evidence,
    render_release_evidence_markdown,
    write_release_evidence,
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


def test_attribution_mapping_values_do_not_corrupt_evidence_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "deployment_namespace": "11111111-2222-4333-8444-555555555555",
                "generation": 1,
                "departments": [
                    {
                        "id": "ready",
                        "label": "warning",
                        "user_keys": [],
                        "group_ids": [],
                    }
                ],
            }
        ),
    )
    evidence = ReleaseEvidence(
        generated_at="2026-01-01T00:00:00+00:00",
        workspace=str(tmp_path),
        status="ready",
    )

    written = write_release_evidence(tmp_path, evidence=evidence)

    assert written.evidence.status == "ready"
    assert json.loads(written.json_path.read_text(encoding="utf-8"))["status"] == "ready"


def test_attribution_personal_data_is_excluded_from_release_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_identity = "alice@example.test"
    user_key = f"usr1.g7.{'a' * 64}"
    group_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    user_token = "at1~u~g7~config~scope~user~principal"
    department_token = "at1~d~g7~config~scope~department"
    mapping = {
        "id": "engineering",
        "label": "Engineering",
        "user_keys": [user_key],
        "group_ids": [group_id],
    }
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "deployment_namespace": "11111111-2222-4333-8444-555555555555",
                "generation": 7,
                "departments": [mapping],
            }
        ),
    )

    unsafe_doctor = {
        "status": "ok",
        "findings_total": 3,
        "counts": {"critical": 0, "warning": 1, "info": 2},
        "attribution": {
            "state": "available",
            "status": "alice-local-account",
            "group_by": "user",
            "enabled": True,
            "generation": 7,
            "fingerprint": "f" * 64,
            "eligible_records": 12,
            "attributed_records": 9,
            "principal": "alice-local-account",
            "detail": "Attribution for Alice in Engineering",
            "nested": {"owner": "alice-local-account"},
            "rows": [
                {
                    "kind": "user",
                    "raw_identity": raw_identity,
                    "user_key": user_key,
                    "filter_token": user_token,
                }
            ],
            "mapping": mapping,
            "group_ids": [group_id],
            "user_filter_token": user_token,
            "department_filter_token": department_token,
        },
    }
    monkeypatch.setattr(
        "agentops.services.evidence_pack._doctor_status",
        lambda _analysis: unsafe_doctor,
    )

    evidence = build_release_evidence(tmp_path)
    written = write_release_evidence(tmp_path, evidence=evidence)
    outputs = (
        json.dumps(evidence.model_dump(), default=str),
        render_release_evidence_markdown(evidence),
        written.json_path.read_text(encoding="utf-8"),
        written.markdown_path.read_text(encoding="utf-8"),
    )
    for output in outputs:
        assert mapping["id"] not in output
        assert mapping["label"] not in output
        assert raw_identity not in output
        assert user_key not in output
        assert group_id not in output
        assert user_token not in output
        assert department_token not in output
        assert "alice-local-account" not in output
        assert "Attribution for Alice in Engineering" not in output
        assert '"rows"' not in output

    assert evidence.doctor["status"] == "ok"
    assert evidence.doctor["findings_total"] == 3
    assert evidence.doctor["counts"]["warning"] == 1
    assert evidence.doctor["attribution"] == {
        "state": "available",
        "group_by": "user",
        "enabled": True,
        "generation": 7,
        "fingerprint": "f" * 64,
        "eligible_records": 12,
        "attributed_records": 9,
    }
