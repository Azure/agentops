"""Map Foundry cloud eval output items into AgentOps result shapes.

When ``execution: cloud`` is used in ``agentops.yaml``, the agent and
evaluators run server-side via the Foundry / OpenAI Evals API. We then
download per-row ``output_items`` from Foundry and reshape them into the
same :class:`RowResult` / :class:`RunResult` schema that local execution
produces, so downstream consumers (``report.md``, ``--baseline`` diffing,
CI gates) behave identically regardless of where the run executed.

The cloud output schema is intentionally loose: we accept multiple field
spellings (``output_text`` / ``output`` / ``message``; ``score`` /
``value`` / ``passed``) and fall back gracefully when a field is absent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentops.core.results import RowMetric, RowResult


def rows_from_cloud_output_items(
    output_items: List[Dict[str, Any]],
) -> List[RowResult]:
    """Build a list of :class:`RowResult` from raw Foundry output items.

    ``output_items`` is the list returned by
    ``cloud_publisher._list_output_items``. Each item is a dict with at
    least ``datasource_item``, ``sample`` and ``results``; missing keys
    yield blank fields rather than raising.
    """
    rows: List[RowResult] = []
    for index, item in enumerate(output_items):
        rows.append(_row_from_item(index, item))
    return rows


def _row_from_item(index: int, item: Dict[str, Any]) -> RowResult:
    datasource = _as_dict(item.get("datasource_item")) or {}
    sample = _as_dict(item.get("sample")) or {}
    results = item.get("results") or []

    metrics: List[RowMetric] = []
    if isinstance(results, list):
        for entry in results:
            metric = _metric_from_result(entry)
            if metric is not None:
                metrics.append(metric)

    return RowResult(
        row_index=index,
        input=_as_str(datasource.get("input")),
        expected=_optional_str(datasource.get("expected")),
        response=_extract_response_text(sample),
        context=_optional_str(datasource.get("context")),
        latency_seconds=None,  # Foundry-side latency is not client-perceived.
        tool_calls=datasource.get("tool_calls") if isinstance(datasource.get("tool_calls"), list) else None,
        metrics=metrics,
        error=_extract_item_error(item),
    )


def _metric_from_result(entry: Any) -> Optional[RowMetric]:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name") or entry.get("metric")
    if not isinstance(name, str) or not name:
        return None
    score = _coerce_float(
        entry.get("score"),
        entry.get("value"),
        entry.get("result"),
    )
    if score is None and isinstance(entry.get("passed"), bool):
        score = 1.0 if entry["passed"] else 0.0
    reason = entry.get("reason") if isinstance(entry.get("reason"), str) else None
    err = entry.get("error") if isinstance(entry.get("error"), str) else None
    return RowMetric(name=name, value=score, error=err, reason=reason)


def _extract_response_text(sample: Dict[str, Any]) -> str:
    """Reach into a Foundry sample payload and pull a plain text response."""
    for key in ("output_text", "text", "content"):
        value = sample.get(key)
        if isinstance(value, str) and value:
            return value
    # Some shapes nest the response under "output" or "messages" (list of
    # role/content dicts). Pick the last assistant message's text.
    for key in ("output", "messages", "output_items"):
        value = sample.get(key)
        if isinstance(value, list):
            for entry in reversed(value):
                if not isinstance(entry, dict):
                    continue
                text = entry.get("content") or entry.get("text") or entry.get("output_text")
                if isinstance(text, str) and text:
                    return text
                if isinstance(text, list):
                    for inner in text:
                        if isinstance(inner, dict):
                            inner_text = inner.get("text") or inner.get("output_text")
                            if isinstance(inner_text, str) and inner_text:
                                return inner_text
    return ""


def _extract_item_error(item: Dict[str, Any]) -> Optional[str]:
    err = item.get("error")
    if isinstance(err, str) and err:
        return err
    if isinstance(err, dict):
        msg = err.get("message") or err.get("error")
        if isinstance(msg, str) and msg:
            return msg
    status = item.get("status")
    if isinstance(status, str) and status.lower() in {"failed", "error"}:
        return f"output item status: {status}"
    return None


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _coerce_float(*candidates: Any) -> Optional[float]:
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None
