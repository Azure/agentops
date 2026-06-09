from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.trace_promotion import promote_traces


runner = CliRunner()


def test_promote_traces_preview_extracts_input_response(tmp_path: Path) -> None:
    source = tmp_path / "traces.jsonl"
    source.write_text(
        json.dumps(
            {
                "operation_Id": "trace-1",
                "customDimensions": {
                    "input": "How do I reset my password?",
                    "response": "Open account settings and choose Reset password.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    preview = promote_traces(
        source=source,
        output_path=tmp_path / ".agentops" / "data" / "trace-regression.jsonl",
        apply=False,
    )

    assert len(preview.rows) == 1
    assert preview.rows[0]["input"] == "How do I reset my password?"
    assert preview.rows[0]["expected"] == "Open account settings and choose Reset password."
    assert preview.rows[0]["metadata"]["needs_review"] is True
    assert not preview.output_path.exists()


def test_promote_traces_apply_writes_dataset_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "traces.json"
    source.write_text(
        json.dumps(
            [
                {
                    "input": "What is covered?",
                    "response": "Coverage includes approved travel expenses.",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / ".agentops" / "data" / "trace-regression.jsonl"

    preview = promote_traces(
        source=source,
        output_path=output,
        label_mode="pending",
        apply=True,
    )

    assert output.exists()
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert row["expected"] == ""
    assert row["metadata"]["label_mode"] == "pending"
    manifest = json.loads(preview.manifest_path.read_text(encoding="utf-8"))
    assert manifest["human_review_required"] is True
    assert manifest["rows"] == 1
    assert manifest["lineage"]["trace_ids"] == []


def test_promote_traces_preserves_foundry_lineage_and_multiturn(tmp_path: Path) -> None:
    source = tmp_path / "traces.jsonl"
    source.write_text(
        json.dumps(
            {
                "operation_Id": "op-1",
                "trace_replay_url": "https://ai.azure.com/traces/op-1",
                "evaluation_url": "https://ai.azure.com/evaluations/eval-1",
                "agent": "travel-agent",
                "agent_version": "7",
                "sampling_policy": "foundry-intelligent-sampling",
                "messages": [
                    {"role": "user", "content": "Plan Rome"},
                    {"role": "assistant", "content": "How many days?"},
                    {"role": "user", "content": "Three"},
                ],
                "customDimensions": {
                    "input": "Plan Rome",
                    "response": "Here is a three-day Rome itinerary.",
                    "source_system": "foundry",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / ".agentops" / "data" / "trace-regression.jsonl"

    preview = promote_traces(source=source, output_path=output, apply=True)

    row = preview.rows[0]
    assert row["metadata"]["trace_id"] == "op-1"
    assert row["metadata"]["replay_url"] == "https://ai.azure.com/traces/op-1"
    assert row["metadata"]["evaluation_url"] == "https://ai.azure.com/evaluations/eval-1"
    assert row["metadata"]["sampling_policy"] == "foundry-intelligent-sampling"
    assert row["messages"][1]["role"] == "assistant"
    manifest = json.loads(preview.manifest_path.read_text(encoding="utf-8"))
    assert manifest["lineage"]["trace_ids"] == ["op-1"]
    assert manifest["lineage"]["replay_urls"] == ["https://ai.azure.com/traces/op-1"]
    assert manifest["lineage"]["evaluation_urls"] == ["https://ai.azure.com/evaluations/eval-1"]
    assert manifest["lineage"]["agents"] == ["travel-agent"]
    assert manifest["lineage"]["agent_versions"] == ["7"]
    assert manifest["lineage"]["sampling_policies"] == ["foundry-intelligent-sampling"]
    assert manifest["lineage"]["multi_turn_rows"] == 1


def test_promote_traces_cli_preview_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "traces.jsonl"
    source.write_text('{"input":"hello","response":"world"}\n', encoding="utf-8")
    output = tmp_path / "candidate.jsonl"

    result = runner.invoke(
        app,
        [
            "eval",
            "promote-traces",
            "--source",
            str(source),
            "--out",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Preview only" in result.stdout
    assert not output.exists()


def test_promote_traces_cli_does_not_double_bullet_truncated_rows(tmp_path: Path) -> None:
    source = tmp_path / "traces.jsonl"
    source.write_text(
        json.dumps({"input": "word " * 25, "response": "ok"}) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["eval", "promote-traces", "--source", str(source)],
        env={"AGENTOPS_NO_COLOR": "1"},
    )

    assert result.exit_code == 0, result.stdout
    assert "\n-- word" not in result.stdout
    assert "\n  1. word" in result.stdout
