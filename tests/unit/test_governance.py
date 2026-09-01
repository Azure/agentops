"""Tests for Build 2026 governance artifact support."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentops.agent.checks.governance import run_governance_check
from agentops.agent.findings import Severity
from agentops.core.governance import (
    REDTEAM_STATE_CANNOT_VERIFY,
    REDTEAM_STATE_MALFORMED,
    REDTEAM_STATE_MISSING_CATEGORIES,
    REDTEAM_STATE_NO_EVIDENCE,
    REDTEAM_STATE_READY,
    REDTEAM_STATE_STALE,
    REDTEAM_STATE_TARGET_MISMATCH,
    REDTEAM_STATE_THRESHOLD_BREACH,
    compute_redteam_fingerprint,
    summarize_acs,
    summarize_assert,
    summarize_redteam,
    summarize_redteam_readiness,
)
from agentops.services.evidence_pack import build_release_evidence


def test_governance_summaries_capture_assert_acs_and_redteam_without_payload_leak(
    tmp_path: Path,
) -> None:
    assert_dir = tmp_path / ".assert"
    assert_dir.mkdir()
    (assert_dir / "evaluation-policy.yaml").write_text(
        """
version: 1
evaluation_name: travel policy eval
results:
  total: 10
  passed: 9
  failed: 1
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "acs.yaml").write_text(
        """
version: 1
name: travel controls
checkpoints:
  - input
  - llm
  - state
  - tool
  - output
""".lstrip(),
        encoding="utf-8",
    )
    redteam = tmp_path / ".agentops" / "governance"
    redteam.mkdir(parents=True)
    (redteam / "redteam-results.json").write_text(
        json.dumps(
            {
                "name": "travel red team",
                "total": 5,
                "failed": 0,
                "payload": "SECRET JAILBREAK PAYLOAD MUST NOT APPEAR",
            }
        ),
        encoding="utf-8",
    )

    assert_summary = summarize_assert(tmp_path)
    acs_summary = summarize_acs(tmp_path)
    redteam_summary = summarize_redteam(tmp_path)

    assert assert_summary.status == "present"
    assert assert_summary.counts["failed"] == 1
    assert acs_summary.status == "present"
    assert acs_summary.checkpoints_missing == ()
    redteam_payload = redteam_summary.to_dict()
    assert redteam_payload["status"] == "present"
    assert "SECRET JAILBREAK" not in json.dumps(redteam_payload)


def test_governance_check_is_silent_when_artifacts_not_configured(tmp_path: Path) -> None:
    """Governance *artifact* checks stay silent when nothing is configured.

    Red-team readiness is a separate always-on gate for a configured agent, so
    the only finding on a bare workspace is the red-team no-evidence warning.
    """
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    findings = run_governance_check(tmp_path)

    artifact_findings = [
        finding for finding in findings if not finding.id.startswith("governance.redteam_")
    ]
    assert artifact_findings == []
    assert [finding.id for finding in findings] == ["governance.redteam_no_evidence"]
    assert findings[0].severity is Severity.WARNING


def test_governance_check_redteam_readiness_not_applicable_without_agent(tmp_path: Path) -> None:
    """Observability-only workspaces (no agent) get no red-team finding."""
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    assert run_governance_check(tmp_path) == []


def test_governance_check_warns_for_configured_missing_artifact(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\nacs_path: missing-acs.yaml\n",
        encoding="utf-8",
    )

    findings = run_governance_check(tmp_path)

    assert [finding.id for finding in findings] == [
        "governance.acs_missing",
        "governance.redteam_no_evidence",
    ]
    assert findings[0].evidence["status"] == "missing"


def test_release_evidence_includes_governance_artifacts(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\nacs_path: acs.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "acs.yaml").write_text(
        """
version: 1
checkpoints:
  - input
  - llm
  - state
  - tool
  - output
""".lstrip(),
        encoding="utf-8",
    )

    evidence = build_release_evidence(tmp_path)

    assert evidence.governance["acs"]["status"] == "present"
    assert any(check.name == "Governance artifacts" for check in evidence.checks)


# ---------------------------------------------------------------------------
# Red-team readiness classifier (issue #454)
# ---------------------------------------------------------------------------

_DEFAULT_CATEGORIES = ["violence", "hate_unfairness", "self_harm", "sexual"]
_DEFAULT_STRATEGIES = ["base64", "rot13", "morse"]


def _default_fingerprint(target: dict | None = None) -> str:
    return compute_redteam_fingerprint(
        target=target or {},
        risk_categories=_DEFAULT_CATEGORIES,
        attack_strategies=_DEFAULT_STRATEGIES,
        num_objectives=10,
        fail_threshold=0.2,
    )


def _write_redteam_evidence(
    workspace: Path,
    *,
    attack_success_rate=0.1,
    risk_categories=None,
    target_fingerprint: str | None = "__default__",
    generated_at: str | None = "__now__",
    target: dict | None = None,
    raw: str | None = None,
) -> Path:
    path = workspace / ".agentops" / "redteam" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    if generated_at == "__now__":
        generated_at = datetime.now(timezone.utc).isoformat()
    if target_fingerprint == "__default__":
        target_fingerprint = _default_fingerprint(target)
    payload = {
        "target": target or {},
        "attack_success_rate": attack_success_rate,
        "risk_categories": _DEFAULT_CATEGORIES if risk_categories is None else risk_categories,
        "generated_at": generated_at,
        "target_fingerprint": target_fingerprint,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_redteam_readiness_no_evidence(tmp_path: Path) -> None:
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_NO_EVIDENCE
    assert not readiness.is_ready
    assert "agentops redteam run" in readiness.remediation


def test_redteam_readiness_malformed(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, raw="{not json")
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_MALFORMED


def test_redteam_readiness_malformed_missing_fields(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, raw=json.dumps({"generated_at": "x"}))
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_MALFORMED


def test_redteam_readiness_target_mismatch(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, target_fingerprint="deadbeef")
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_TARGET_MISMATCH


def test_redteam_readiness_missing_categories(tmp_path: Path) -> None:
    _write_redteam_evidence(
        tmp_path, risk_categories=["violence"], target_fingerprint=None
    )
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_MISSING_CATEGORIES
    assert "hate_unfairness" in readiness.missing_categories


def test_redteam_readiness_threshold_breach(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, attack_success_rate=0.5)
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_THRESHOLD_BREACH
    assert readiness.is_breach
    assert readiness.attack_success_rate == pytest.approx(0.5)


def test_redteam_readiness_stale(tmp_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    _write_redteam_evidence(tmp_path, generated_at=old)
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_STALE


def test_redteam_readiness_cannot_verify_without_fingerprint(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, target_fingerprint=None)
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_CANNOT_VERIFY


def test_redteam_readiness_cannot_verify_without_timestamp(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path, generated_at=None)
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_CANNOT_VERIFY


def test_redteam_readiness_ready(tmp_path: Path) -> None:
    _write_redteam_evidence(tmp_path)
    readiness = summarize_redteam_readiness(tmp_path, None)
    assert readiness.state == REDTEAM_STATE_READY
    assert readiness.is_ready
    assert readiness.target_verified
    assert readiness.remediation == ""


def test_redteam_readiness_reads_configured_output_path(tmp_path: Path) -> None:
    """A user-overridden output_path is honored when locating evidence."""
    custom = tmp_path / ".agentops" / "custom" / "scan.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = compute_redteam_fingerprint(
        target={},
        risk_categories=_DEFAULT_CATEGORIES,
        attack_strategies=_DEFAULT_STRATEGIES,
        num_objectives=10,
        fail_threshold=0.2,
    )
    custom.write_text(
        json.dumps(
            {
                "target": {},
                "attack_success_rate": 0.1,
                "risk_categories": _DEFAULT_CATEGORIES,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "target_fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    config = {"redteam_run": {"output_path": ".agentops/custom/scan.json"}}
    readiness = summarize_redteam_readiness(tmp_path, config)
    assert readiness.state == REDTEAM_STATE_READY


def test_redteam_readiness_doctor_threshold_breach_is_critical(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    _write_redteam_evidence(tmp_path, attack_success_rate=0.9)

    findings = run_governance_check(tmp_path)

    breach = [f for f in findings if f.id == "governance.redteam_threshold_breach"]
    assert breach
    assert breach[0].severity is Severity.CRITICAL


def test_redteam_readiness_doctor_ready_is_silent(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    _write_redteam_evidence(tmp_path)

    findings = run_governance_check(tmp_path)

    assert not [f for f in findings if f.id.startswith("governance.redteam_")]


def test_redteam_readiness_evidence_pack_threshold_breach_blocks(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    _write_redteam_evidence(tmp_path, attack_success_rate=0.9)

    evidence = build_release_evidence(tmp_path)

    redteam_checks = [c for c in evidence.checks if c.name == "Red team readiness"]
    assert redteam_checks
    assert redteam_checks[0].status == "blocked"


def test_redteam_readiness_evidence_pack_ready(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    _write_redteam_evidence(tmp_path)

    evidence = build_release_evidence(tmp_path)

    redteam_checks = [c for c in evidence.checks if c.name == "Red team readiness"]
    assert redteam_checks
    assert redteam_checks[0].status == "ready"


def test_redteam_readiness_evidence_pack_skipped_without_agent(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    evidence = build_release_evidence(tmp_path)

    assert not [c for c in evidence.checks if c.name == "Red team readiness"]
