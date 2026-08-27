from __future__ import annotations

import json
from pathlib import Path

from agentops.agent.checks.observability import run_observability_check


def _write_config(root: Path, body: str) -> None:
    (root / "agentops.yaml").write_text(body, encoding="utf-8")


def _write_dataset(root: Path, rel: str, rows: list[dict]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_uninitialized_workspace_emits_no_findings(tmp_path: Path) -> None:
    # No agentops.yaml and no .agentops -> cannot verify, never "missing".
    assert run_observability_check(tmp_path) == []


def test_single_turn_dataset_is_not_applicable(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "dataset_kind: single-turn\n",
    )
    _write_dataset(tmp_path, ".agentops/data/smoke.jsonl", [{"input": "hi"}])

    assert run_observability_check(tmp_path) == []


def test_multiturn_without_conversations_is_flagged(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "dataset_kind: multi-turn\n",
    )
    _write_dataset(tmp_path, ".agentops/data/smoke.jsonl", [{"input": "hi"}])

    ids = {finding.id for finding in run_observability_check(tmp_path)}
    assert "observability.multiturn_coverage_missing" in ids


def test_multiturn_with_conversation_rows_is_covered(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/conversations.jsonl\n"
        "dataset_kind: multi-turn\n",
    )
    _write_dataset(
        tmp_path,
        ".agentops/data/conversations.jsonl",
        [{"messages": [{"role": "user", "content": "hi"}]}],
    )

    assert run_observability_check(tmp_path) == []


def test_multiturn_missing_dataset_is_cannot_verify(tmp_path: Path) -> None:
    # dataset_kind multi-turn declared but dataset file missing -> cannot
    # verify, so no "missing" finding is emitted.
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/does-not-exist.jsonl\n"
        "dataset_kind: multi-turn\n",
    )

    assert run_observability_check(tmp_path) == []


def test_auto_dataset_without_conversations_is_not_applicable(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n",
    )
    _write_dataset(tmp_path, ".agentops/data/smoke.jsonl", [{"input": "hi"}])

    assert run_observability_check(tmp_path) == []


def test_auto_dataset_with_conversations_is_covered(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/conversations.jsonl\n",
    )
    _write_dataset(
        tmp_path,
        ".agentops/data/conversations.jsonl",
        [{"messages": [{"role": "user", "content": "hi"}]}],
    )

    assert run_observability_check(tmp_path) == []


def test_multiturn_covered_by_trace_manifest_lineage(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "version: 1\n"
        "agent: travel-agent:2\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "dataset_kind: multi-turn\n",
    )
    _write_dataset(tmp_path, ".agentops/data/smoke.jsonl", [{"input": "hi"}])
    manifest = tmp_path / ".agentops" / "data" / "trace-regression-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"lineage": {"multi_turn_rows": 2}}),
        encoding="utf-8",
    )

    assert run_observability_check(tmp_path) == []
