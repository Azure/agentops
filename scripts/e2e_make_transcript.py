"""Render a human-readable transcript for one E2E scenario.

Reads ``<scenario_dir>/HEADER.md`` (rendered by ``e2e_render_config.py``) and
``<scenario_dir>/.agentops/results/latest/results.json`` (produced by
``agentops eval run``) and writes ``<scenario_dir>/transcript.txt``.

The transcript is meant to be a single self-contained text file that
explains what was being evaluated, what the agent answered for each row,
which evaluators ran and their per-row scores, and the final pass/fail
verdict. It is intentionally easy to share in PR reviews and incident
reports.

Usage:
    python scripts/e2e_make_transcript.py <scenario_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


SECTION = "=" * 78


def _format_metric(name: str, value: Any) -> str:
    if isinstance(value, float):
        return f"{name} = {value:.4f}"
    return f"{name} = {value}"


def _format_value(label: str, value: Any) -> str:
    if value is None or value == "":
        return f"  {label}: <none>\n"
    if isinstance(value, str):
        text = value.strip()
        indent = "    "
        body = "\n".join(indent + line for line in text.splitlines())
        return f"  {label}:\n{body}\n"
    return f"  {label}: {json.dumps(value, ensure_ascii=False, indent=2)}\n"


def _render_row(idx: int, row: Dict[str, Any]) -> str:
    parts: List[str] = []
    parts.append(f"--- Row {idx} ---")
    parts.append(_format_value("input", row.get("input")))
    if row.get("context"):
        parts.append(_format_value("context", row["context"]))
    if row.get("expected") is not None:
        parts.append(_format_value("expected", row["expected"]))
    parts.append(_format_value("response", row.get("response", "")))

    tool_calls = row.get("tool_calls")
    if tool_calls:
        parts.append(_format_value("tool_calls", tool_calls))

    latency = row.get("latency_seconds")
    if latency is not None:
        parts.append(f"  latency_seconds: {latency:.3f}\n")

    metrics = row.get("metrics") or []
    if metrics:
        parts.append("  metrics:")
        for m in metrics:
            name = m.get("name", "?")
            value = m.get("value")
            err = m.get("error")
            if err:
                parts.append(f"    - {name}: ERROR ({err})")
            else:
                parts.append(f"    - {_format_metric(name, value)}")

    err = row.get("error")
    if err:
        parts.append(f"  ROW ERROR: {err}")

    return "\n".join(parts) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: e2e_make_transcript.py <scenario_dir>", file=sys.stderr)
        return 2

    scenario_dir = Path(sys.argv[1]).resolve()
    if not scenario_dir.is_dir():
        print(f"Not a directory: {scenario_dir}", file=sys.stderr)
        return 2

    header_path = scenario_dir / "HEADER.md"
    results_path = scenario_dir / ".agentops" / "results" / "latest" / "results.json"
    out_path = scenario_dir / "transcript.txt"

    if not results_path.exists():
        print(f"results.json not found at {results_path}", file=sys.stderr)
        return 1

    results = json.loads(results_path.read_text(encoding="utf-8"))
    header = (
        header_path.read_text(encoding="utf-8")
        if header_path.exists()
        else f"# Scenario: {scenario_dir.name}\n"
    )

    summary = results.get("summary") or {}
    target = results.get("target") or {}
    metrics_aggregate: Dict[str, float] = results.get("aggregate_metrics") or {}
    threshold_results = results.get("thresholds") or []
    rows = results.get("rows") or []

    lines: List[str] = []
    lines.append(SECTION)
    lines.append(header.rstrip())
    lines.append(SECTION)
    lines.append("")
    lines.append("Target")
    lines.append("------")
    for k, v in target.items():
        lines.append(f"  {k}: {v}")
    lines.append("")

    lines.append("Per-row transcript")
    lines.append("------------------")
    for i, row in enumerate(rows, start=1):
        lines.append(_render_row(i, row))

    lines.append(SECTION)
    lines.append("Aggregate metrics")
    lines.append("-----------------")
    if metrics_aggregate:
        for name, value in sorted(metrics_aggregate.items()):
            lines.append(f"  {_format_metric(name, value)}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Thresholds")
    lines.append("----------")
    if threshold_results:
        for t in threshold_results:
            name = t.get("metric", "?")
            criteria = t.get("criteria", "")
            expected = t.get("expected", "?")
            actual = t.get("actual", "?")
            passed = t.get("passed")
            verdict = "PASS" if passed else "FAIL"
            lines.append(
                f"  [{verdict}] {name} {criteria} {expected} (actual: {actual})"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    overall = summary.get("overall_passed")
    lines.append(SECTION)
    lines.append(
        "VERDICT: "
        + (
            "PASS"
            if overall
            else "FAIL"
            if overall is False
            else "UNKNOWN"
        )
    )
    if summary:
        lines.append(
            f"  items: {summary.get('items_passed_all', '?')}/{summary.get('items_total', '?')} "
            f"passed (rate: {summary.get('items_pass_rate', 0):.2%})"
        )
        lines.append(
            f"  thresholds: {summary.get('thresholds_passed', '?')}/{summary.get('thresholds_total', '?')} "
            f"passed (rate: {summary.get('threshold_pass_rate', 0):.2%})"
        )
    lines.append(SECTION)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
