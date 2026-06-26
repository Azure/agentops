"""Import Azure Monitor telemetry into AgentOps JSONL datasets.

The module has two halves:

* a pure transformer that maps telemetry rows into AgentOps dataset rows
* a thin Azure Monitor query wrapper with lazy SDK imports

Users never provide raw KQL. The query builder only accepts structured time
ranges, field mappings, filters, and row limits from ``agentops.yaml``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from agentops.core.agentops_config import AgentOpsConfig, TelemetryImportConfig

DEFAULT_MAX_ROWS = 100
MAX_ROWS_CAP = 5000

_DEFAULT_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "input": (
        "input",
        "query",
        "prompt",
        "message",
        "user_message",
        "customDimensions.input",
        "customDimensions.query",
        "customDimensions.prompt",
        "customDimensions.gen_ai.prompt",
    ),
    "response": (
        "response",
        "prediction",
        "output",
        "answer",
        "completion",
        "assistant_message",
        "customDimensions.response",
        "customDimensions.prediction",
        "customDimensions.output",
        "customDimensions.gen_ai.completion",
    ),
    "context": (
        "context",
        "retrieved_context",
        "grounding",
        "customDimensions.context",
        "customDimensions.retrieved_context",
        "customDimensions.grounding",
    ),
    "retrieved_context_items": (
        "retrieved_context_items",
        "context_items",
        "customDimensions.retrieved_context_items",
        "customDimensions.context_items",
    ),
    "tool_calls": ("tool_calls", "customDimensions.tool_calls"),
    "trace_id": ("trace_id", "operation_Id", "operationId"),
    "turn_id": ("turn_id", "span_id", "id", "customDimensions.turn_id"),
    "timestamp": ("timestamp", "TimeGenerated", "time"),
}

_QUERY_COLUMNS = (
    "timestamp",
    "operation_Id = column_ifexists('operation_Id', '')",
    "operationId = column_ifexists('operationId', '')",
    "id = column_ifexists('id', '')",
    "name = column_ifexists('name', '')",
    "message = column_ifexists('message', '')",
    "duration = column_ifexists('duration', '')",
    "success = column_ifexists('success', '')",
    "customDimensions = column_ifexists('customDimensions', dynamic({}))",
)


class TelemetryImportError(RuntimeError):
    """Raised when a telemetry import cannot be validated, queried, or written."""


@dataclass(frozen=True)
class TelemetryImportPreview:
    """Result of validating/querying/transforming one telemetry import."""

    config: TelemetryImportConfig
    output_path: Path
    manifest_path: Path
    rows: list[dict[str, Any]]
    skipped: int = 0
    deduped: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def find_telemetry_import(
    config: AgentOpsConfig,
    name: str,
) -> TelemetryImportConfig:
    """Return a named telemetry import or raise a friendly error."""

    for item in config.telemetry_imports:
        if item.name == name:
            return item
    available = ", ".join(item.name for item in config.telemetry_imports) or "none"
    raise TelemetryImportError(
        f"telemetry import {name!r} was not found in agentops.yaml. "
        f"Available imports: {available}."
    )


def validate_telemetry_import(config: TelemetryImportConfig) -> list[str]:
    """Validate service-level constraints and return non-fatal warnings."""

    warnings: list[str] = []
    if config.output.label_mode == "self-similarity":
        warnings.append(
            "Generated rows use production responses as expected values for drift "
            "detection, not human-verified ground truth."
        )
    return warnings


def preview_telemetry_import(
    config: TelemetryImportConfig,
    *,
    rows: Optional[int] = None,
    apply: bool = False,
) -> TelemetryImportPreview:
    """Query Azure Monitor, transform rows, and optionally write JSONL output."""

    validate_telemetry_import(config)
    raw_rows = query_azure_monitor(config, rows=rows)
    preview = transform_telemetry_rows(config, raw_rows, rows=rows)
    if apply:
        write_telemetry_import(preview)
    return preview


def transform_telemetry_rows(
    config: TelemetryImportConfig,
    telemetry_rows: Iterable[dict[str, Any]],
    *,
    rows: Optional[int] = None,
) -> TelemetryImportPreview:
    """Pure transformation from telemetry records to AgentOps dataset rows."""

    limit = _bounded_rows(rows if rows is not None else config.max_rows)
    output_path = config.output.path
    manifest_path = config.output.manifest_path or output_path.with_name(
        f"{output_path.stem}-manifest.json"
    )
    warnings = validate_telemetry_import(config)
    converted: list[dict[str, Any]] = []
    skipped = 0
    deduped = 0
    seen: set[tuple[str, str]] = set()

    for raw in telemetry_rows:
        if len(converted) >= limit:
            break
        row = _telemetry_row_to_agentops_row(config, raw)
        if row is None:
            skipped += 1
            continue
        telemetry = row.get("telemetry")
        trace_id = ""
        turn_id = ""
        if isinstance(telemetry, dict):
            trace_id = str(telemetry.get("trace_id") or "")
            turn_id = str(telemetry.get("turn_id") or "")
        key = (trace_id or row["input"], turn_id or row.get("response", ""))
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        converted.append(row)

    truncated = len(converted) >= limit
    if not converted:
        warnings.append("No telemetry rows contained both input and response text.")
    return TelemetryImportPreview(
        config=config,
        output_path=output_path,
        manifest_path=manifest_path,
        rows=converted,
        skipped=skipped,
        deduped=deduped,
        truncated=truncated,
        warnings=warnings,
    )


def write_telemetry_import(preview: TelemetryImportPreview) -> None:
    """Write JSONL rows and a small manifest next to the output."""

    preview.output_path.parent.mkdir(parents=True, exist_ok=True)
    with preview.output_path.open("w", encoding="utf-8") as handle:
        for row in preview.rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    trace_ids = [
        str(row.get("telemetry", {}).get("trace_id"))
        for row in preview.rows
        if isinstance(row.get("telemetry"), dict) and row["telemetry"].get("trace_id")
    ]
    manifest = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "import": preview.config.name,
        "source": preview.config.source,
        "target": preview.config.target,
        "output_path": str(preview.output_path),
        "rows": len(preview.rows),
        "skipped": preview.skipped,
        "deduped": preview.deduped,
        "truncated": preview.truncated,
        "trace_ids": trace_ids,
        "warnings": preview.warnings,
    }
    preview.manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_telemetry_import_preview(preview: TelemetryImportPreview) -> str:
    """Render concise CLI output."""

    lines = [
        "AgentOps telemetry import",
        f"Import: {preview.config.name}",
        f"Target: {preview.config.target}",
        f"Output: {preview.output_path}",
        "",
        "Summary",
        f"  rows       {len(preview.rows)}",
        f"  skipped    {preview.skipped}",
        f"  deduped    {preview.deduped}",
        f"  truncated  {str(preview.truncated).lower()}",
    ]
    if preview.warnings:
        lines.append("")
        lines.append("Warnings")
        lines.extend(f"  - {warning}" for warning in preview.warnings)
    if preview.rows:
        lines.append("")
        lines.append("Sample rows")
        for index, row in enumerate(preview.rows[:3], start=1):
            lines.append(f"  {index}. {str(row.get('input', ''))[:120]}")
    return "\n".join(lines) + "\n"


def query_azure_monitor(
    config: TelemetryImportConfig,
    *,
    rows: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Run the generated KQL against Azure Monitor with lazy SDK imports."""

    try:
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:
        raise TelemetryImportError(
            "Telemetry import requires Azure authentication packages. Install "
            "them with: python -m pip install azure-identity azure-monitor-query"
        ) from exc

    kql = build_telemetry_kql(config, rows=rows)
    credential = DefaultAzureCredential(
        exclude_developer_cli_credential=True,
        process_timeout=30,
    )
    try:
        if config.target == "log-analytics":
            from azure.monitor.query import LogsQueryClient  # noqa: WPS433

            client = LogsQueryClient(credential)
            workspace_id = _resolve_value(config.workspace_id, "workspace_id")
            response = client.query_workspace(workspace_id, kql, timespan=None)
            return _flatten_logs_response(response)
        if config.resource_id:
            from azure.monitor.query import LogsQueryClient  # noqa: WPS433

            client = LogsQueryClient(credential)
            resource_id = _resolve_value(config.resource_id, "resource_id")
            response = client.query_resource(resource_id, kql, timespan=None)
            return _flatten_logs_response(response)
        app_id = _application_id(config)
        token = credential.get_token("https://api.applicationinsights.io/.default").token
        return _query_application_insights(app_id, token, kql)
    except ImportError as exc:
        raise TelemetryImportError(
            "Telemetry import with resource_id/workspace_id requires the Azure "
            "Monitor Query SDK. Install it with: python -m pip install "
            "azure-monitor-query"
        ) from exc
    except TelemetryImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TelemetryImportError(f"Azure Monitor query failed: {exc}") from exc


def build_telemetry_kql(
    config: TelemetryImportConfig,
    *,
    rows: Optional[int] = None,
) -> str:
    """Build safe KQL from structured config only."""

    limit = _bounded_rows(rows if rows is not None else config.max_rows)
    clauses = ["union isfuzzy=true requests, dependencies, traces"]
    clauses.append(f"| extend timestamp = {_timestamp_expr()}")
    clauses.append(_time_clause(config))
    for key, value in sorted(config.filters.items()):
        clauses.append(_filter_clause(key, value))
    columns = ", ".join(_QUERY_COLUMNS)
    clauses.append(f"| project {columns}")
    clauses.append("| order by timestamp desc")
    clauses.append(f"| take {limit}")
    return "\n".join(clauses)


def _telemetry_row_to_agentops_row(
    config: TelemetryImportConfig,
    raw: dict[str, Any],
) -> Optional[dict[str, Any]]:
    input_text = _mapped_text(config, raw, "input")
    response_text = _mapped_text(config, raw, "response")
    if not input_text or not response_text:
        return None

    label_mode = config.output.label_mode
    telemetry = {
        "trace_id": _mapped_text(config, raw, "trace_id"),
        "turn_id": _mapped_text(config, raw, "turn_id"),
        "timestamp": _mapped_text(config, raw, "timestamp"),
        "source": config.source,
        "target": config.target,
        "import": config.name,
    }
    row: dict[str, Any] = {
        "input": _clean_value(input_text, config),
        "response": _clean_value(response_text, config),
        "prediction": _clean_value(response_text, config),
        "expected": _clean_value(response_text, config) if label_mode == "self-similarity" else "",
        "telemetry": {k: v for k, v in telemetry.items() if v not in (None, "")},
        "metadata": {
            "source": "azure_monitor_telemetry",
            "label_mode": label_mode,
            "needs_review": True,
        },
    }
    context = _mapped_value(config, raw, "context")
    if context not in (None, "", [], {}):
        row["context"] = _clean_value(context, config)
        row["retrieved_context"] = row["context"]
    context_items = _mapped_value(config, raw, "retrieved_context_items")
    if context_items not in (None, "", [], {}):
        row["retrieved_context_items"] = _clean_value(context_items, config)
    tool_calls = _mapped_value(config, raw, "tool_calls")
    if tool_calls not in (None, "", [], {}):
        row["tool_calls"] = _clean_value(tool_calls, config)
    if config.privacy.include_raw:
        row["raw"] = _clean_value(raw, config)
    return row


def _mapped_text(config: TelemetryImportConfig, raw: dict[str, Any], name: str) -> Optional[str]:
    value = _mapped_value(config, raw, name)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return text if text not in ("{}", "[]") else None
    text = str(value).strip()
    return text or None


def _mapped_value(config: TelemetryImportConfig, raw: dict[str, Any], name: str) -> Any:
    mapping = config.fields.get(name)
    if mapping:
        return _lookup(raw, mapping)
    for candidate in _DEFAULT_FIELD_CANDIDATES.get(name, ()):
        value = _lookup(raw, candidate)
        if value not in (None, "", [], {}):
            return value
    return None


def _lookup(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _clean_value(value: Any, config: TelemetryImportConfig, key: str = "") -> Any:
    lowered = key.lower()
    if any(fragment.lower() in lowered for fragment in config.privacy.redact_fields):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: _clean_value(v, config, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_value(item, config, key) for item in value]
    if isinstance(value, str) and len(value) > config.privacy.max_field_length:
        return value[: config.privacy.max_field_length] + "...[truncated]"
    return value


def _flatten_logs_response(response: Any) -> list[dict[str, Any]]:
    tables = getattr(response, "tables", None) or []
    if not tables:
        return []
    table = tables[0]
    columns: list[str] = []
    for column in getattr(table, "columns", None) or []:
        name = getattr(column, "name", None) if not isinstance(column, dict) else column.get("name")
        if isinstance(name, str):
            columns.append(name)
    rows: list[dict[str, Any]] = []
    for raw in getattr(table, "rows", None) or []:
        rows.append(dict(zip(columns, raw)))
    return rows


def _application_id(config: TelemetryImportConfig) -> str:
    if config.application_id:
        return _resolve_value(config.application_id, "application_id")
    if config.connection_string:
        connection_string = _resolve_value(config.connection_string, "connection_string")
        match = re.search(r"ApplicationId=([0-9a-fA-F-]+)", connection_string)
        if match:
            return match.group(1)
    raise TelemetryImportError(
        "application-insights imports require resource_id, application_id, or "
        "a connection_string containing ApplicationId"
    )


def _query_application_insights(app_id: str, bearer: str, kql: str) -> list[dict[str, Any]]:
    import json as _json
    from urllib import request

    body = _json.dumps({"query": kql}).encode("utf-8")
    req = request.Request(
        url=f"https://api.applicationinsights.io/v1/apps/{app_id}/query",
        data=body,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:  # noqa: S310
        parsed = _json.loads(response.read())
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise TelemetryImportError(f"Application Insights query failed: {message}")
    tables = parsed.get("tables") if isinstance(parsed, dict) else None
    if not tables:
        return []
    table = tables[0]
    columns = [column.get("name") for column in table.get("columns", [])]
    return [dict(zip(columns, row)) for row in table.get("rows", [])]


def _time_clause(config: TelemetryImportConfig) -> str:
    tr = config.time_range
    if tr.from_ and tr.to:
        return (
            f"| where timestamp between (datetime({_kql_string(tr.from_)}) .. "
            f"datetime({_kql_string(tr.to)}))"
        )
    days = tr.lookback_days or 7
    return f"| where timestamp >= ago({days}d)"


def _filter_clause(key: str, value: str | list[str]) -> str:
    expr = _safe_column_expr(key)
    values = value if isinstance(value, list) else [value]
    escaped = ", ".join(_kql_string(str(item)) for item in values)
    if len(values) == 1:
        return f"| where {expr} == {escaped}"
    return f"| where {expr} in ({escaped})"


def _safe_column_expr(key: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", key):
        raise TelemetryImportError(
            f"unsafe telemetry filter field {key!r}; use a column name or customDimensions.name"
        )
    if key.startswith("customDimensions."):
        subkey = key.split(".", 1)[1]
        return (
            "tostring(column_ifexists('customDimensions', dynamic({}))"
            f"[{_kql_string(subkey)}])"
        )
    return f"tostring(column_ifexists({_kql_string(key)}, ''))"


def _timestamp_expr() -> str:
    return (
        "coalesce("
        "column_ifexists('timestamp', datetime(null)), "
        "column_ifexists('TimeGenerated', datetime(null)), "
        "column_ifexists('time', datetime(null))"
        ")"
    )


def _kql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _resolve_value(value: Optional[str], label: str) -> str:
    if not value:
        raise TelemetryImportError(f"telemetry import is missing {label}")
    value = value.strip()
    env_name: Optional[str] = None
    if value.startswith("env:"):
        env_name = value[4:]
    elif value.startswith("$") and len(value) > 1:
        env_name = value[1:].strip("{}")
    if env_name:
        resolved = os.getenv(env_name)
        if not resolved:
            raise TelemetryImportError(
                f"environment variable {env_name} referenced by {label} is not set"
            )
        return resolved
    return value


def _bounded_rows(rows: int) -> int:
    if rows <= 0:
        raise TelemetryImportError("rows must be greater than zero")
    return min(rows, MAX_ROWS_CAP)
