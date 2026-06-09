from __future__ import annotations

import json
from pathlib import Path

from agentops.agent.checks.observability import run_observability_check


def test_observability_check_flags_missing_build_2026_readiness(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    findings = run_observability_check(tmp_path)
    ids = {finding.id for finding in findings}

    assert "observability.multiturn_coverage_missing" in ids
    assert "observability.trace_sampling_missing" in ids
    assert "observability.trace_replay_missing" in ids


def test_observability_check_accepts_declared_readiness(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/conversations.jsonl\n"
        "dataset_kind: multi-turn\n"
        "observability:\n"
        "  trace_sampling:\n"
        "    enabled: true\n"
        "    mode: foundry\n"
        "  trace_replay_url: https://ai.azure.com/traces/trace-1\n",
        encoding="utf-8",
    )

    findings = run_observability_check(tmp_path)

    assert findings == []


def test_observability_check_accepts_trace_manifest_lineage(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    manifest = tmp_path / ".agentops" / "data" / "trace-regression-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "lineage": {
                    "multi_turn_rows": 2,
                    "sampling_policies": ["foundry-intelligent-sampling"],
                    "replay_urls": ["https://ai.azure.com/traces/trace-1"],
                }
            }
        ),
        encoding="utf-8",
    )

    findings = run_observability_check(tmp_path)

    assert findings == []
