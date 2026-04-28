"""Aggregate per-scenario E2E artifacts into a single Markdown summary.

Reads downloaded GitHub Actions artifacts from ``artifacts/<job-name>/`` and
emits a single Markdown summary table to stdout (or ``--out`` if provided)
covering both the offline smoke scenarios and every live-* scenario, so the
run page shows one consolidated picture instead of just the offline summary.

Inputs it understands:
  * ``artifacts/offline-smoke/SUMMARY.md`` (already rendered by e2e_demo.py)
  * ``artifacts/live-*/.agentops/results/latest/results.json`` (live scenarios)
  * ``artifacts/live-*/HEADER.md`` (optional context line)

The script never raises on missing fields; if a scenario's results are
unparseable it is shown with a ``?`` so the summary is still useful.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ARTIFACT_ROOT = Path("artifacts")


def _read_results(job_dir: Path) -> dict | None:
    candidates = sorted(job_dir.glob("**/.agentops/results/latest/results.json"))
    if not candidates:
        candidates = sorted(job_dir.glob("**/results.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _headline_metric(results: dict) -> tuple[str, str]:
    """Return (metric_name, formatted_value) for the most informative metric."""
    metrics = results.get("metrics") or {}
    # Prefer items_pass_rate, then run_pass, then anything else.
    for preferred in ("items_pass_rate", "run_pass", "threshold_pass_rate"):
        if preferred in metrics:
            v = metrics[preferred]
            try:
                return preferred, f"{float(v):.3f}"
            except (TypeError, ValueError):
                return preferred, str(v)
    if metrics:
        k = next(iter(metrics))
        v = metrics[k]
        try:
            return k, f"{float(v):.3f}"
        except (TypeError, ValueError):
            return k, str(v)
    return "—", "—"


def _row_from_live(job_name: str, job_dir: Path) -> str:
    results = _read_results(job_dir)
    if not results:
        return f"| `{job_name}` | ? | ❓ | — |"
    summary = results.get("summary") or {}
    exit_code = summary.get("exit_code", results.get("exit_code", "?"))
    passed = summary.get("overall_passed")
    if passed is None:
        passed = (results.get("metrics") or {}).get("run_pass")
    icon = "✅" if passed in (True, 1, 1.0) else ("❌" if passed in (False, 0, 0.0) else "❓")
    metric_name, metric_value = _headline_metric(results)
    return f"| `{job_name}` | {exit_code} | {icon} | {metric_name} = {metric_value} |"


def _offline_block(job_dir: Path) -> str:
    summary = next(job_dir.glob("**/SUMMARY.md"), None)
    if not summary:
        return ""
    return summary.read_text(encoding="utf-8").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ARTIFACT_ROOT),
                        help="Directory containing per-job artifact folders.")
    parser.add_argument("--out", default="-",
                        help="Output file (default: stdout).")
    args = parser.parse_args()

    root = Path(args.root)
    lines: list[str] = []
    lines.append("# AgentOps E2E run summary")
    lines.append("")
    lines.append("Aggregated outcome of every job in this workflow run.")
    lines.append("")

    # Live scenarios table.
    live_jobs = sorted(p for p in root.glob("live-*") if p.is_dir())
    if live_jobs:
        lines.append("## Live scenarios")
        lines.append("")
        lines.append("| Job | Exit code | Overall passed | Headline metric |")
        lines.append("|---|---|---|---|")
        for job_dir in live_jobs:
            lines.append(_row_from_live(job_dir.name, job_dir))
        lines.append("")

    # Offline smoke (already a self-contained markdown block).
    offline_dir = root / "offline-smoke"
    if offline_dir.is_dir():
        lines.append("## Offline smoke (`offline-smoke`)")
        lines.append("")
        block = _offline_block(offline_dir)
        if block:
            lines.append(block)
        else:
            lines.append("_No SUMMARY.md found in offline-smoke artifact._")
        lines.append("")

    output = "\n".join(lines).rstrip() + "\n"

    if args.out == "-":
        print(output, end="")
    else:
        Path(args.out).write_text(output, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
