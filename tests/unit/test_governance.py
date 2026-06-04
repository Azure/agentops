"""Tests for Build 2026 governance artifact support."""

from __future__ import annotations

import json
from pathlib import Path

from agentops.agent.checks.governance import run_governance_check
from agentops.core.governance import summarize_acs, summarize_assert, summarize_redteam
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


def test_governance_check_is_silent_when_not_configured(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    assert run_governance_check(tmp_path) == []


def test_governance_check_warns_for_configured_missing_artifact(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: data.jsonl\nacs_path: missing-acs.yaml\n",
        encoding="utf-8",
    )

    findings = run_governance_check(tmp_path)

    assert [finding.id for finding in findings] == ["governance.acs_missing"]
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
