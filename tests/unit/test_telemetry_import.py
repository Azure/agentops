from __future__ import annotations

import builtins
import json

import pytest

from agentops.core.agentops_config import AgentOpsConfig
from agentops.services.telemetry_import import (
    TelemetryImportError,
    build_telemetry_kql,
    find_telemetry_import,
    query_azure_monitor,
    transform_telemetry_rows,
    write_telemetry_import,
)


def _config(**overrides):
    data = {
        "version": 1,
        "agent": "support-agent:1",
        "dataset": ".agentops/data/smoke.jsonl",
        "telemetry_imports": [
            {
                "name": "prod",
                "target": "application-insights",
                "resource_id": "$APPINSIGHTS_RESOURCE_ID",
                "fields": {
                    "input": "customDimensions.question",
                    "response": "customDimensions.answer",
                    "context": "customDimensions.context",
                },
                "output": {"path": ".agentops/data/prod.jsonl"},
                **overrides,
            }
        ],
    }
    return AgentOpsConfig.model_validate(data).telemetry_imports[0]


def test_transform_rows_dedupes_redacts_and_writes_manifest(tmp_path) -> None:
    cfg = _config(
        output={"path": str(tmp_path / "prod.jsonl")},
        privacy={"redact_fields": ["token"], "max_field_length": 100, "include_raw": True},
    )
    raw = [
        {
            "operation_Id": "trace-1",
            "id": "turn-1",
            "customDimensions": {
                "question": "How do I reset my password?",
                "answer": "Open account settings.",
                "context": "Reset article",
                "token": "secret-token",
            },
        },
        {
            "operation_Id": "trace-1",
            "id": "turn-1",
            "customDimensions": {
                "question": "How do I reset my password?",
                "answer": "Open account settings.",
            },
        },
        {"customDimensions": {"question": "missing response"}},
    ]

    preview = transform_telemetry_rows(cfg, raw)
    write_telemetry_import(preview)

    assert len(preview.rows) == 1
    assert preview.deduped == 1
    assert preview.skipped == 1
    row = preview.rows[0]
    assert row["input"] == "How do I reset my password?"
    assert row["response"] == "Open account settings."
    assert row["expected"] == "Open account settings."
    assert row["context"] == "Reset article"
    assert row["telemetry"]["trace_id"] == "trace-1"
    assert row["raw"]["customDimensions"]["token"] == "[redacted]"
    assert (tmp_path / "prod.jsonl").exists()
    manifest = json.loads((tmp_path / "prod-manifest.json").read_text(encoding="utf-8"))
    assert manifest["rows"] == 1
    assert manifest["deduped"] == 1


def test_build_kql_uses_safe_generated_filters() -> None:
    cfg = _config(filters={"customDimensions.agent": ["support", "sales"]}, max_rows=1000)

    kql = build_telemetry_kql(cfg, rows=5)

    assert "union isfuzzy=true requests, dependencies, traces" in kql
    assert "| extend timestamp = coalesce(" in kql
    assert "column_ifexists('timestamp', datetime(null))" in kql
    assert "column_ifexists('TimeGenerated', datetime(null))" in kql
    assert "coalesce(timestamp, TimeGenerated)" not in kql
    assert "ago(7d)" in kql
    assert (
        "tostring(column_ifexists('customDimensions', dynamic({}))['agent']) "
        "in ('support', 'sales')"
    ) in kql
    assert "operation_Id = column_ifexists('operation_Id', '')" in kql
    assert "TimeGenerated =" not in kql
    assert "| order by timestamp desc" in kql
    assert "take 5" in kql


def test_build_kql_guards_plain_filter_columns() -> None:
    cfg = _config(filters={"name": "agent.response"})

    kql = build_telemetry_kql(cfg, rows=10)

    assert "tostring(column_ifexists('name', '')) == 'agent.response'" in kql
    assert "tostring(name)" not in kql


def test_build_kql_rejects_unsafe_filter_field() -> None:
    cfg = _config(filters={"name); drop table traces; //": "x"})

    with pytest.raises(TelemetryImportError, match="unsafe"):
        build_telemetry_kql(cfg)


def test_find_telemetry_import_reports_available_names() -> None:
    cfg = AgentOpsConfig.model_validate(
        {
            "version": 1,
            "agent": "support-agent:1",
            "dataset": ".agentops/data/smoke.jsonl",
            "telemetry_imports": [
                {"name": "prod", "target": "log-analytics", "workspace_id": "workspace"}
            ],
        }
    )

    with pytest.raises(TelemetryImportError, match="prod"):
        find_telemetry_import(cfg, "missing")


def test_query_azure_monitor_reports_missing_sdk(monkeypatch) -> None:
    cfg = _config()
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "azure.identity":
            raise ImportError("no azure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(TelemetryImportError, match="azure-identity"):
        query_azure_monitor(cfg, rows=1)
