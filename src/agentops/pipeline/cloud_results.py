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

import json
from typing import Any, Dict, List, Optional

from agentops.core.results import RowMetric, RowResult


def rows_from_cloud_output_items(
    output_items: List[Dict[str, Any]],
) -> List[RowResult]:
    """Build a list of :class:`RowResult` from raw Foundry output items.

    ``output_items`` is the list returned by
    ``cloud_runner._list_output_items``. Each item is a dict with at
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


_NUMERIC_SCORE_KEYS = (
    "score",
    "value",
    "result",
    "metric_value",
    "rating",
    "grader_score",
    "numeric_value",
)
_PASS_LABELS = {"pass", "passed", "true", "yes", "1", "ok", "success"}
_FAIL_LABELS = {"fail", "failed", "false", "no", "0", "error", "errored"}


def _score_from_label(value: Any) -> Optional[float]:
    """Map a textual pass/fail label onto 1.0 / 0.0."""
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token in _PASS_LABELS:
        return 1.0
    if token in _FAIL_LABELS:
        return 0.0
    return None


def _score_from_mapping(entry: Dict[str, Any]) -> Optional[float]:
    """Probe a dict-like result envelope for a score using a wide net of
    field names. Mirrors the loose-shape contract documented at the top
    of this module: Foundry / OpenAI Evals API have shipped at least
    ``score``, ``value``, ``result``, ``grader_score`` and ``rating`` as
    the numeric carrier across SDK versions and grader types, plus
    ``passed`` (bool) and ``label`` (string) as binary fallbacks."""
    score = _coerce_float(*(entry.get(k) for k in _NUMERIC_SCORE_KEYS))
    if score is not None:
        return score
    passed = entry.get("passed")
    if isinstance(passed, bool):
        return 1.0 if passed else 0.0
    label_score = _score_from_label(entry.get("label"))
    if label_score is not None:
        return label_score
    return None


def _metric_from_result(entry: Any) -> Optional[RowMetric]:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name") or entry.get("metric")
    if not isinstance(name, str) or not name:
        return None

    # First try the top-level envelope where azure_ai_evaluator graders
    # populate `score` + `passed` directly.
    score = _score_from_mapping(entry)

    # Some Foundry server-side graders (especially custom prompt-based
    # evaluators) tuck the score down inside `sample` or `details`
    # instead. Probe those as a fallback so a missing top-level score
    # doesn't mask an evaluator that actually returned a number.
    if score is None:
        sample = entry.get("sample")
        if isinstance(sample, dict):
            score = _score_from_mapping(sample)
    if score is None:
        details = entry.get("details")
        if isinstance(details, dict):
            score = _score_from_mapping(details)

    reason = entry.get("reason") if isinstance(entry.get("reason"), str) else None
    err = _extract_grader_error(entry)
    if score is None and err is None:
        # Surface the missing-score case as a structured reason instead of
        # silently writing null. The orchestrator/reporter use this string
        # to point operators at `cloud_output_items.json` for triage.
        err = (
            "no numeric score returned by Foundry grader; inspect "
            "cloud_output_items.json in the results directory."
        )
    return RowMetric(name=name, value=score, error=err, reason=reason)


def _extract_grader_error(entry: Dict[str, Any]) -> Optional[str]:
    """Pull a human-readable error out of a Foundry grader result envelope.

    The on-the-wire shape we have seen in production when an
    ``azure_ai_evaluator`` grader fails to execute (e.g., the evaluator
    service principal lacks RBAC on the model deployment) is::

        {
          "name": "coherence",
          "score": null,
          "passed": null,
          "status": "error",
          "sample": {
            "error": {
              "code": "FAILED_EXECUTION",
              "message": "(UserError) OpenAI API hits AuthenticationError: ..."
            }
          }
        }

    Without lifting ``sample.error.message`` into ``RowMetric.error``, the
    real cause is buried in ``cloud_output_items.json`` and the user only
    sees ``actual=missing`` in the threshold table. Probe (in order):

    1. Top-level ``error`` (string or ``{message, code}`` dict).
    2. ``sample.error`` (string or ``{message, code}`` dict).
    3. ``status == "error"`` as a last-resort signal so we at least flag
       the row even when no error payload is present.
    """
    primary = _normalize_error_payload(entry.get("error"))
    if primary is not None:
        return primary
    sample = entry.get("sample")
    if isinstance(sample, dict):
        nested = _normalize_error_payload(sample.get("error"))
        if nested is not None:
            return nested
    status = entry.get("status")
    if isinstance(status, str) and status.strip().lower() == "error":
        return "grader status: error (no error payload returned)"
    return None


def _normalize_error_payload(value: Any) -> Optional[str]:
    """Flatten ``error`` (string or ``{message, code}`` dict) into one line."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        message = value.get("message") or value.get("error")
        if isinstance(message, str) and message.strip():
            code = value.get("code")
            if isinstance(code, str) and code.strip():
                return f"{code.strip()}: {message.strip()}"
            return message.strip()
    return None


def _extract_response_text(sample: Dict[str, Any]) -> str:
    """Reach into a Foundry sample payload and pull a plain text response.

    Foundry's sample shape varies: sometimes the response is a clean string
    under ``output_text``, sometimes it's a list of output items under
    ``output`` / ``output_items``, and occasionally ``output_text`` is set
    to a JSON-encoded version of the structured output. Try structured
    fields first (they're authoritative), and recurse into JSON-encoded
    strings rather than passing them through as the response.
    """
    # 1. Structured fields are authoritative. Walk them first.
    for key in ("output", "messages", "output_items"):
        text = _text_from_structured(sample.get(key))
        if text:
            return text

    # 2. Flat string fields. If the value looks like JSON, parse and recurse.
    for key in ("output_text", "text", "content"):
        value = sample.get(key)
        if isinstance(value, str) and value:
            stripped = value.strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                except (ValueError, TypeError):
                    return value
                if isinstance(parsed, list):
                    text = _text_from_structured(parsed)
                    if text:
                        return text
                elif isinstance(parsed, dict):
                    return _extract_response_text(parsed)
                # Fall through to raw value if we couldn't extract.
            return value
        if isinstance(value, list):
            text = _text_from_structured(value)
            if text:
                return text
    return ""


def _text_from_structured(value: Any) -> str:
    """Walk a list-of-dicts (output / messages / output_items shape) and
    return the first textual payload encountered, or ``""`` when none is
    found. Iterates in reverse so the assistant's final message wins.
    """
    if not isinstance(value, list):
        return ""
    for entry in reversed(value):
        if not isinstance(entry, dict):
            continue
        # Try flat text fields first.
        for field in ("output_text", "text"):
            candidate = entry.get(field)
            if isinstance(candidate, str) and candidate:
                return candidate
        # Some shapes nest under "content" as either a string or a list of
        # content blocks (OpenAI Responses API: content: [{type, text}, ...]).
        nested = entry.get("content")
        if isinstance(nested, str) and nested:
            return nested
        if isinstance(nested, list):
            text = _text_from_structured(nested)
            if text:
                return text
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
