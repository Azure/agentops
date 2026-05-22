"""Render a Markdown transcript for one E2E scenario.

Reads ``<scenario_dir>/HEADER.md`` (rendered by ``e2e_render_config.py``) and
``<scenario_dir>/.agentops/results/latest/results.json`` (produced by
``agentops eval run``) and writes ``<scenario_dir>/transcript.md``.

The transcript is meant to be a single self-contained markdown document
that explains what was being evaluated, what the agent answered for each
row, which evaluators ran and their per-row scores, and the final
pass/fail verdict. Markdown so it renders nicely in the GitHub Actions
artifact viewer and PR reviews.

Usage:
    python scripts/e2e_make_transcript.py <scenario_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _fmt_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _fenced(value: Any, lang: str = "") -> str:
    if value is None or value == "":
        return "_(none)_"
    if isinstance(value, str):
        return f"```{lang}\n{value.rstrip()}\n```"
    return f"```{lang or 'json'}\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```"


def _render_row(idx: int, row: Dict[str, Any]) -> str:
    parts: List[str] = []
    parts.append(f"### Row {idx}")
    parts.append("")
    parts.append("**Input**")
    parts.append("")
    parts.append(_fenced(row.get("input")))
    parts.append("")

    if row.get("context"):
        parts.append("**Context**")
        parts.append("")
        parts.append(_fenced(row["context"]))
        parts.append("")

    if row.get("expected") is not None:
        parts.append("**Expected**")
        parts.append("")
        parts.append(_fenced(row["expected"]))
        parts.append("")

    parts.append("**Response**")
    parts.append("")
    parts.append(_fenced(row.get("response", "")))
    parts.append("")

    tool_calls = row.get("tool_calls")
    if tool_calls:
        parts.append("**Tool calls**")
        parts.append("")
        parts.append(_fenced(tool_calls, "json"))
        parts.append("")

    latency = row.get("latency_seconds")
    if latency is not None:
        parts.append(f"**Latency:** `{latency:.3f}s`")
        parts.append("")

    metrics = row.get("metrics") or []
    if metrics:
        parts.append("**Metrics**")
        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|---|---|")
        for m in metrics:
            name = m.get("name", "?")
            value = m.get("value")
            err = m.get("error")
            if err:
                parts.append(f"| `{name}` | ⚠️ ERROR: {err} |")
            else:
                parts.append(f"| `{name}` | {_fmt_number(value)} |")
        parts.append("")

    err = row.get("error")
    if err:
        parts.append(f"> ❌ **Row error:** {err}")
        parts.append("")

    return "\n".join(parts)


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
    out_path = scenario_dir / "transcript.md"

    header = (
        header_path.read_text(encoding="utf-8")
        if header_path.exists()
        else f"# Scenario: {scenario_dir.name}\n"
    ).rstrip()

    if not results_path.exists():
        out_path.write_text(
            header
            + "\n\n---\n\n"
            + "## Verdict: ⚠️ NO RESULTS\n\n"
            + f"`results.json` not found at `{results_path}`.\n\n"
            + "The evaluation run did not complete successfully. Check the\n"
            + "job logs (Run AgentOps eval step) for the underlying error.\n",
            encoding="utf-8",
        )
        print(f"Wrote {out_path} (no results)")
        return 0

    results = json.loads(results_path.read_text(encoding="utf-8"))
    summary = results.get("summary") or {}
    target = results.get("target") or {}
    metrics_aggregate: Dict[str, float] = results.get("aggregate_metrics") or {}
    threshold_results = results.get("thresholds") or []
    rows = results.get("rows") or []

    lines: List[str] = [header, "", "---", "", "## Target", ""]
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for k, v in target.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines.append("")

    lines.append("## Per-row transcript")
    lines.append("")
    for i, row in enumerate(rows, start=1):
        lines.append(_render_row(i, row))

    lines.append("---")
    lines.append("")
    lines.append("## Aggregate metrics")
    lines.append("")
    if metrics_aggregate:
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for name, value in sorted(metrics_aggregate.items()):
            lines.append(f"| `{name}` | {_fmt_number(value)} |")
    else:
        lines.append("_(none)_")
    lines.append("")

    lines.append("## Thresholds")
    lines.append("")
    if threshold_results:
        lines.append("| Result | Metric | Criteria | Expected | Actual |")
        lines.append("|---|---|---|---|---|")
        for t in threshold_results:
            name = t.get("metric", "?")
            criteria = t.get("criteria", "")
            expected = t.get("expected", "?")
            actual = t.get("actual", "?")
            passed = t.get("passed")
            verdict = "✅ PASS" if passed else "❌ FAIL"
            lines.append(f"| {verdict} | `{name}` | `{criteria}` | `{expected}` | `{actual}` |")
    else:
        lines.append("_(none)_")
    lines.append("")

    overall = summary.get("overall_passed")
    if overall:
        verdict = "✅ PASS"
    elif overall is False:
        verdict = "❌ FAIL"
    else:
        verdict = "⚠️ UNKNOWN"
    lines.append("---")
    lines.append("")
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    if summary:
        items_total = summary.get("items_total", "?")
        items_passed = summary.get("items_passed_all", "?")
        items_rate = summary.get("items_pass_rate", 0)
        thr_total = summary.get("thresholds_total", "?")
        thr_passed = summary.get("thresholds_passed", "?")
        thr_rate = summary.get("threshold_pass_rate", 0)
        lines.append(f"- **Items:** {items_passed}/{items_total} passed ({items_rate:.2%})")
        lines.append(f"- **Thresholds:** {thr_passed}/{thr_total} passed ({thr_rate:.2%})")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
