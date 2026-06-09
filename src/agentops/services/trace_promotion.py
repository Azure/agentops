"""Promote production trace exports into reviewable regression datasets."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

_TEXT_WRAP_WIDTH = 92


LabelMode = Literal["self-similarity", "pending"]


@dataclass(frozen=True)
class TracePromotionPreview:
    """Result of transforming traces into candidate dataset rows."""

    source: Path
    output_path: Path
    manifest_path: Path
    rows: list[dict[str, Any]]
    skipped: int
    label_mode: LabelMode
    warnings: list[str] = field(default_factory=list)


def promote_traces(
    *,
    source: Path,
    output_path: Path,
    max_rows: int = 50,
    label_mode: LabelMode = "self-similarity",
    apply: bool = False,
) -> TracePromotionPreview:
    """Convert a JSON/JSONL trace export into AgentOps dataset rows.

    ``self-similarity`` stores the production response as ``expected`` so future
    evals catch behavior drift against a known production answer. ``pending``
    leaves ``expected`` empty and marks every row for human completion.
    """

    if max_rows <= 0:
        raise ValueError("max_rows must be greater than zero")
    if label_mode not in {"self-similarity", "pending"}:
        raise ValueError("label_mode must be self-similarity or pending")
    if not source.exists():
        raise FileNotFoundError(
            f"trace source not found: {source}. Export Foundry/App Insights traces "
            "first, or use the sample created by `agentops init` at "
            ".agentops/traces/sample-traces.jsonl."
        )

    traces = _load_trace_export(source)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped = 0
    seen: set[tuple[str, str]] = set()

    for trace in traces:
        if len(rows) >= max_rows:
            break
        candidate = _trace_to_row(trace, label_mode)
        if candidate is None:
            skipped += 1
            continue
        key = (candidate["input"], candidate.get("expected", ""))
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        rows.append(candidate)

    if not rows:
        warnings.append("No usable input/response pairs were found in the trace export.")
    if label_mode == "self-similarity":
        warnings.append(
            "Rows use production responses as expected values. Treat this as drift detection, not human-verified ground truth."
        )
    else:
        warnings.append(
            "Rows are pending human labels. Fill expected/context/tool fields before using this file as a blocking gate."
        )

    manifest_path = output_path.with_name("trace-regression-manifest.json")
    preview = TracePromotionPreview(
        source=source,
        output_path=output_path,
        manifest_path=manifest_path,
        rows=rows,
        skipped=skipped,
        label_mode=label_mode,
        warnings=warnings,
    )
    if apply:
        _write_trace_dataset(preview)
    return preview


def render_trace_promotion_preview(preview: TracePromotionPreview) -> str:
    """Render a concise terminal-friendly summary."""

    lines = [
        "AgentOps trace-to-dataset preview",
        f"Source: {preview.source}",
        f"Output: {preview.output_path}",
        "",
        "Summary",
    ]
    lines.extend(
        _render_text_fields(
            [
                ("rows", str(len(preview.rows))),
                ("skipped", str(preview.skipped)),
                ("label mode", preview.label_mode),
            ]
        )
    )
    if preview.warnings:
        lines.append("")
        lines.append("Warnings")
        for warning in preview.warnings:
            lines.extend(_wrapped_status_line("warn", "warning", warning))
    if preview.rows:
        lines.append("")
        lines.append("Sample rows")
        for index, row in enumerate(preview.rows[:3], start=1):
            lines.extend(_wrapped_numbered_line(index, str(row["input"])[:100]))
    return "\n".join(lines) + "\n"


def _render_text_fields(rows: list[tuple[str, str]]) -> list[str]:
    width = max(len(label) for label, _ in rows)
    lines: list[str] = []
    for label, value in rows:
        lines.extend(_wrap_text(value, indent=f"  {label.ljust(width)}  "))
    return lines


def _wrapped_status_line(status: str, label: str, text: str) -> list[str]:
    prefix = f"  {status.ljust(4)} {label.ljust(10)} "
    wrapped = textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [prefix.rstrip()]


def _wrapped_numbered_line(index: int, text: str) -> list[str]:
    prefix = f"  {index}. "
    wrapped = textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [prefix.rstrip()]


def _wrap_text(text: str, *, indent: str) -> list[str]:
    return textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [indent.rstrip()]


def _load_trace_export(source: Path) -> Iterable[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        if not isinstance(payload, list):
            raise ValueError("JSON trace export must be an array of objects")
        return [item for item in payload if isinstance(item, dict)]

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        item = line.strip()
        if not item:
            continue
        try:
            payload = json.loads(item)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}: invalid JSON on line {line_number}: {exc}") from exc
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _trace_to_row(trace: dict[str, Any], label_mode: LabelMode) -> Optional[dict[str, Any]]:
    input_text = _first_text(
        trace,
        "input",
        "query",
        "prompt",
        "message",
        "user_message",
        "request",
        "customDimensions.input",
        "customDimensions.query",
        "customDimensions.gen_ai.prompt",
    )
    response_text = _first_text(
        trace,
        "response",
        "output",
        "answer",
        "completion",
        "assistant_message",
        "customDimensions.response",
        "customDimensions.output",
        "customDimensions.gen_ai.completion",
    )
    if not input_text or not response_text:
        return None

    metadata = {
        "source": "production_trace",
        "trace_id": _first_text(trace, "trace_id", "operation_Id", "operationId", "id"),
        "operation_id": _first_text(trace, "operation_Id", "operationId"),
        "span_id": _first_text(trace, "span_id", "spanId", "id"),
        "timestamp": _first_text(trace, "timestamp", "time", "TimeGenerated"),
        "label_mode": label_mode,
        "needs_review": True,
        "source_system": _first_text(trace, "source_system", "source", "customDimensions.source_system"),
        "agent": _first_text(trace, "agent", "agent_id", "customDimensions.agent"),
        "agent_version": _first_text(
            trace,
            "agent_version",
            "customDimensions.agent_version",
            "customDimensions.agentops.agent.version",
        ),
        "foundry_project": _first_text(
            trace,
            "foundry_project",
            "project",
            "customDimensions.foundry_project",
        ),
        "replay_url": _first_text(
            trace,
            "replay_url",
            "trace_replay_url",
            "customDimensions.replay_url",
            "customDimensions.trace_replay_url",
        ),
        "evaluation_url": _first_text(
            trace,
            "evaluation_url",
            "eval_url",
            "customDimensions.evaluation_url",
            "customDimensions.eval_url",
        ),
        "sampling_policy": _first_text(
            trace,
            "sampling_policy",
            "sample_reason",
            "customDimensions.sampling_policy",
            "customDimensions.sample_reason",
        ),
    }
    row: dict[str, Any] = {
        "input": input_text,
        "expected": response_text if label_mode == "self-similarity" else "",
        "metadata": {k: v for k, v in metadata.items() if v not in (None, "")},
    }
    context = _first_text(trace, "context", "grounding", "retrieved_context", "customDimensions.context")
    if context:
        row["context"] = context
    tool_calls = _first_value(trace, "tool_calls", "customDimensions.tool_calls")
    if tool_calls:
        row["tool_calls"] = tool_calls
    messages = _first_value(trace, "messages", "conversation", "turns", "customDimensions.messages")
    if messages:
        row["messages"] = messages
    return row


def _write_trace_dataset(preview: TracePromotionPreview) -> None:
    preview.output_path.parent.mkdir(parents=True, exist_ok=True)
    with preview.output_path.open("w", encoding="utf-8") as handle:
        for row in preview.rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    lineage = _lineage_from_rows(preview.rows)
    manifest = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(preview.source),
        "dataset_path": str(preview.output_path),
        "rows": len(preview.rows),
        "skipped": preview.skipped,
        "label_mode": preview.label_mode,
        "human_review_required": True,
        "lineage": lineage,
    }
    preview.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _lineage_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trace_ids: list[str] = []
    replay_urls: list[str] = []
    evaluation_urls: list[str] = []
    source_systems: set[str] = set()
    agents: set[str] = set()
    agent_versions: set[str] = set()
    sampling_policies: set[str] = set()
    multi_turn_rows = 0

    for row in rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        _append_unique(trace_ids, metadata.get("trace_id"))
        _append_unique(replay_urls, metadata.get("replay_url"))
        _append_unique(evaluation_urls, metadata.get("evaluation_url"))
        _add_text(source_systems, metadata.get("source_system"))
        _add_text(agents, metadata.get("agent"))
        _add_text(agent_versions, metadata.get("agent_version"))
        _add_text(sampling_policies, metadata.get("sampling_policy"))
        if row.get("messages"):
            multi_turn_rows += 1

    return {
        "trace_ids": trace_ids,
        "replay_urls": replay_urls,
        "evaluation_urls": evaluation_urls,
        "source_systems": sorted(source_systems),
        "agents": sorted(agents),
        "agent_versions": sorted(agent_versions),
        "sampling_policies": sorted(sampling_policies),
        "multi_turn_rows": multi_turn_rows,
    }


def _append_unique(values: list[str], value: Any) -> None:
    text = str(value).strip() if value is not None else ""
    if text and text not in values:
        values.append(text)


def _add_text(values: set[str], value: Any) -> None:
    text = str(value).strip() if value is not None else ""
    if text:
        values.add(text)


def _first_text(data: dict[str, Any], *keys: str) -> Optional[str]:
    value = _first_value(data, *keys)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return text if text not in ("{}", "[]") else None
    text = str(value).strip()
    return text or None


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = _lookup(data, key)
        if value not in (None, "", [], {}):
            return value
    return None


def _lookup(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
