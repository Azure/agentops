"""Report generation for AgentOps (Markdown and HTML)."""

from __future__ import annotations

from agentops.core.models import ComparisonResult, RunResult


def generate_report_markdown(result: RunResult) -> str:
    overall_status = "PASS" if result.summary.overall_passed else "FAIL"

    lines: list[str] = []
    lines.append("# AgentOps Evaluation Report")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Bundle: {result.bundle.name}")
    lines.append(f"- Dataset: {result.dataset.name}")
    lines.append(f"- Overall status: **{overall_status}**")
    lines.append("")
    lines.append("## Execution Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Backend | {result.execution.backend} |")
    lines.append(f"| Duration (s) | {result.execution.duration_seconds:.3f} |")
    lines.append(f"| Started at | {result.execution.started_at} |")
    lines.append(f"| Finished at | {result.execution.finished_at} |")
    lines.append(f"| Exit code | {result.execution.exit_code} |")
    lines.append("")
    lines.append("## Metrics")

    if result.metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---:|")
        for metric in result.metrics:
            lines.append(f"| {metric.name} | {_fmt(metric.value)} |")
    else:
        lines.append("- No metrics found")

    lines.append("")
    lines.append("## Run Metrics")
    if result.run_metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---:|")
        for metric in result.run_metrics:
            lines.append(f"| {metric.name} | {_fmt(metric.value)} |")
    else:
        lines.append("- No run metrics derived")

    lines.append("")
    lines.append("## Item Verdicts")
    if result.item_evaluations:
        passed_items = sum(1 for item in result.item_evaluations if item.passed_all)
        lines.append(
            f"- Items passed all thresholds: {passed_items}/{len(result.item_evaluations)}"
        )
        lines.append("")
        lines.append("| Row | Passed All | Passed Rules | Total Rules |")
        lines.append("|---:|---|---:|---:|")
        for item in result.item_evaluations:
            passed_rules = sum(1 for threshold in item.thresholds if threshold.passed)
            lines.append(
                f"| {item.row_index} | {'PASS' if item.passed_all else 'FAIL'} | {passed_rules} | {len(item.thresholds)} |"
            )
    else:
        lines.append("- No item-level evaluations found")

    lines.append("")
    lines.append("## Threshold Checks")
    if result.thresholds:
        lines.append("")
        lines.append("| Evaluator | Criteria | Expected | Actual | Status |")
        lines.append("|---|---|---:|---:|---|")
        for threshold in result.thresholds:
            mark = _threshold_label(threshold.passed)
            lines.append(
                f"| {threshold.evaluator} | {threshold.criteria} | {threshold.expected} | {threshold.actual} | {mark} |"
            )
    else:
        lines.append("- No thresholds configured")

    lines.append("")
    lines.append("## Artifacts")
    if result.artifacts is not None:
        if result.artifacts.backend_stdout is not None:
            lines.append(f"- backend_stdout: {result.artifacts.backend_stdout}")
        if result.artifacts.backend_stderr is not None:
            lines.append(f"- backend_stderr: {result.artifacts.backend_stderr}")
        if result.artifacts.foundry_eval_studio_url is not None:
            lines.append(
                f"- foundry_eval_studio_url: {result.artifacts.foundry_eval_studio_url}"
            )
        if result.artifacts.foundry_eval_name is not None:
            lines.append(f"- foundry_eval_name: {result.artifacts.foundry_eval_name}")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Shared formatting helpers
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """Smart number formatting: integers show without decimals, floats show 2 dp."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.2f}"


def _fmt_delta(value: float) -> str:
    """Smart delta formatting with sign prefix."""
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):+d}"
    return f"{value:+.2f}"


def _threshold_label(passed: bool) -> str:
    return "Met" if passed else "Missed"


def _check_threshold(value: float, criteria: str, target: str | None) -> bool:
    """Evaluate whether a metric value meets a threshold criteria+target."""
    if target is None:
        return True
    try:
        t = float(target)
    except (ValueError, TypeError):
        return True
    if criteria == ">=":
        return value >= t
    if criteria == ">":
        return value > t
    if criteria == "<=":
        return value <= t
    if criteria == "<":
        return value < t
    if criteria == "==":
        return value == t
    return True


def _fmt_target(criteria: str, target: str | None) -> str:
    """Format threshold target as 'criteria value' (e.g., '>= 3')."""
    if target is None:
        return criteria
    try:
        val = float(target)
        return f"{criteria} {_fmt(val)}"
    except (ValueError, TypeError):
        return f"{criteria} {target}"


# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

_CSS = """\
:root {
  --bg: #ffffff; --surface: #f6f8fa; --border: #d1d9e0;
  --text: #1f2328; --muted: #656d76; --accent: #0969da;
  --green: #1a7f37; --red: #cf222e; --yellow: #9a6700;
  --green-bg: #dafbe1; --red-bg: #ffebe9; --yellow-bg: #fff8c5;
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font); background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem 2.5rem; max-width: 1020px; margin: 0 auto; }
h1 { font-size: 1.6rem; font-weight: 600; margin-bottom: .4rem; }
h2 { font-size: 1.1rem; font-weight: 600; color: var(--text); margin: 2rem 0 .6rem; padding-bottom: .35rem; border-bottom: 1px solid var(--border); }
.badge { display: inline-block; padding: .15rem .6rem; border-radius: .25rem; font-weight: 600; font-size: .8rem; }
.badge-pass { background: var(--green-bg); color: var(--green); }
.badge-fail { background: var(--red-bg); color: var(--red); }
.badge-improved { background: var(--green-bg); color: var(--green); }
.badge-regressed { background: var(--red-bg); color: var(--red); }
.badge-unchanged { background: var(--yellow-bg); color: var(--yellow); }
.meta { color: var(--muted); font-size: .85rem; margin-bottom: 1.2rem; display: flex; flex-wrap: wrap; gap: .3rem 1.5rem; }
.meta span { white-space: nowrap; }
table { width: 100%; border-collapse: collapse; margin: .4rem 0 1rem; font-size: .875rem; }
th, td { padding: .5rem .7rem; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--surface); font-size: .75rem; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }
td { vertical-align: top; }
tr:hover td { background: #f0f4f8; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .6rem; margin: .4rem 0 1.2rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: .5rem; padding: .8rem 1rem; }
.card .label { font-size: .7rem; font-weight: 600; text-transform: uppercase; color: var(--muted); letter-spacing: .03em; }
.card .value { font-size: 1.3rem; font-weight: 700; margin-top: .15rem; }
footer { margin-top: 2.5rem; padding-top: .8rem; border-top: 1px solid var(--border); color: var(--muted); font-size: .75rem; text-align: center; }
"""


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{_html_escape(label)}</span>'


def _status_badge(passed: bool) -> str:
    return _badge("PASS", "pass") if passed else _badge("FAIL", "fail")


def _threshold_badge(passed: bool) -> str:
    return _badge("Met", "pass") if passed else _badge("Missed", "fail")


def _direction_badge(direction: str) -> str:
    return _badge(direction, direction)


def _wrap_page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_html_escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "<footer>Generated by AgentOps</footer>\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Evaluation run HTML report
# ---------------------------------------------------------------------------


def generate_report_html(result: RunResult) -> str:
    overall = result.summary.overall_passed
    parts: list[str] = []

    parts.append(f"<h1>AgentOps Evaluation Report {_status_badge(overall)}</h1>")
    parts.append(
        '<div class="meta">'
        f"<span><strong>Bundle:</strong> {_html_escape(result.bundle.name)}</span>"
        f"<span><strong>Dataset:</strong> {_html_escape(result.dataset.name)}</span>"
        f"<span><strong>Backend:</strong> {_html_escape(result.execution.backend)}</span>"
        "</div>"
    )

    parts.append("<h2>Execution</h2>")
    parts.append('<div class="card-grid">')
    for label, val in [
        ("Duration", f"{result.execution.duration_seconds:.1f}s"),
        ("Started", result.execution.started_at[:19]),
        ("Exit code", str(result.execution.exit_code)),
    ]:
        parts.append(
            f'<div class="card"><div class="label">{label}</div><div class="value">{_html_escape(val)}</div></div>'
        )
    parts.append("</div>")

    if result.metrics:
        parts.append("<h2>Metrics</h2>")
        parts.append(
            '<table><thead><tr><th>Metric</th><th class="num">Value</th></tr></thead><tbody>'
        )
        for m in result.metrics:
            parts.append(
                f'<tr><td>{_html_escape(m.name)}</td><td class="num">{_fmt(m.value)}</td></tr>'
            )
        parts.append("</tbody></table>")

    if result.run_metrics:
        parts.append("<h2>Run Metrics</h2>")
        parts.append(
            '<table><thead><tr><th>Metric</th><th class="num">Value</th></tr></thead><tbody>'
        )
        for m in result.run_metrics:
            parts.append(
                f'<tr><td>{_html_escape(m.name)}</td><td class="num">{_fmt(m.value)}</td></tr>'
            )
        parts.append("</tbody></table>")

    if result.thresholds:
        parts.append("<h2>Threshold Checks</h2>")
        parts.append(
            '<table><thead><tr><th>Evaluator</th><th>Criteria</th><th class="num">Expected</th><th class="num">Actual</th><th>Status</th></tr></thead><tbody>'
        )
        for t in result.thresholds:
            parts.append(
                f"<tr><td>{_html_escape(t.evaluator)}</td><td>{_html_escape(t.criteria)}</td>"
                f'<td class="num">{_html_escape(t.expected)}</td><td class="num">{_html_escape(t.actual)}</td>'
                f"<td>{_threshold_badge(t.passed)}</td></tr>"
            )
        parts.append("</tbody></table>")

    if result.item_evaluations:
        passed_count = sum(1 for i in result.item_evaluations if i.passed_all)
        total = len(result.item_evaluations)
        parts.append(f"<h2>Item Verdicts ({passed_count}/{total} passed)</h2>")
        parts.append(
            '<table><thead><tr><th class="num">Row</th><th>Status</th><th class="num">Passed Rules</th><th class="num">Total Rules</th></tr></thead><tbody>'
        )
        for item in result.item_evaluations:
            pr = sum(1 for th in item.thresholds if th.passed)
            parts.append(
                f'<tr><td class="num">{item.row_index}</td><td>{_status_badge(item.passed_all)}</td>'
                f'<td class="num">{pr}</td><td class="num">{len(item.thresholds)}</td></tr>'
            )
        parts.append("</tbody></table>")

    if result.artifacts:
        urls = []
        if result.artifacts.foundry_eval_studio_url:
            urls.append(
                f'<a href="{_html_escape(result.artifacts.foundry_eval_studio_url)}" style="color:var(--accent)">View in Foundry</a>'
            )
        if urls:
            parts.append("<h2>Artifacts</h2>")
            parts.append("<p>" + " &middot; ".join(urls) + "</p>")

    return _wrap_page("AgentOps Evaluation Report", "\n".join(parts))


# ---------------------------------------------------------------------------
# Comparison Markdown report (N runs)
# ---------------------------------------------------------------------------


def generate_comparison_markdown(result: ComparisonResult) -> str:
    verdict = (
        "REGRESSIONS DETECTED" if result.summary.any_regressions else "NO REGRESSIONS"
    )
    run_labels = [r.run_id for r in result.runs]

    lines: list[str] = []
    lines.append("# AgentOps Comparison Report")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Runs compared: **{result.summary.run_count}**")
    lines.append(f"- Verdict: **{verdict}**")
    lines.append("")

    # Conditions
    cond = result.conditions
    if cond:
        type_labels = {
            "agent": "Agent Comparison",
            "model": "Model Comparison",
            "dataset": "Dataset Coverage",
            "general": "General Comparison",
        }
        lines.append("## Conditions")
        lines.append("")
        lines.append(
            f"- Comparison type: **{type_labels.get(cond.comparison_type, cond.comparison_type)}**"
        )
        if cond.fixed:
            fixed_items = ", ".join(f"{k}={v}" for k, v in cond.fixed.items())
            lines.append(f"- Fixed: {fixed_items}")
        if cond.varying:
            lines.append(f"- Varying: {', '.join(cond.varying)}")
        if not cond.row_level_valid:
            lines.append(
                "- Note: Row-level comparison is not meaningful because datasets differ across runs."
            )
        lines.append("")

    # Run details table — only show varying fields + always-show fields
    varying_set = set(cond.varying) if cond else set()
    detail_fields = [
        ("Backend", "backend", lambda r: r.backend or "-"),
        ("Target", None, lambda r: r.target or "-"),
        ("Model", None, lambda r: r.model or "-"),
        ("Agent", None, lambda r: r.agent_id or "-"),
        ("Project", "project", lambda r: r.project_endpoint or "-"),
        ("Dataset", "dataset", lambda r: r.dataset_name),
        ("Bundle", "bundle", lambda r: r.bundle_name),
        (
            "Status",
            None,
            lambda r: "PASS"
            if r.overall_passed
            else "FAIL"
            if r.overall_passed is not None
            else "-",
        ),
        ("Started", None, lambda r: r.started_at[:19] if r.started_at else "-"),
    ]
    # Keep fields that are varying or always-show (condition_key is None)
    visible_fields = [
        (label, ckey, getter)
        for label, ckey, getter in detail_fields
        if ckey is None or ckey in varying_set
    ]

    lines.append("## Run Details")
    lines.append("")
    lines.append("| | " + " | ".join(run_labels) + " |")
    lines.append("|---|" + "|".join("---" for _ in run_labels) + "|")
    lines.append(
        "| Role | Baseline | "
        + " | ".join(f"Run {i}" for i in range(1, len(result.runs)))
        + " |"
    )
    for field, _ckey, getter in visible_fields:
        cells = [getter(r) for r in result.runs]
        lines.append(f"| {field} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "*Status is PASS when all thresholds are met, FAIL when any threshold is missed.*"
    )
    lines.append("")

    # Unified Evaluators table (metrics + thresholds merged)
    if result.metric_rows:
        threshold_map = {tr.evaluator: tr for tr in result.threshold_rows}
        lines.append("## Evaluators")
        lines.append("")
        header = "| Evaluator | Target | " + " | ".join(run_labels) + " | Best |"
        sep = "|---|---|" + "|".join("---:" for _ in run_labels) + "|---|"
        lines.append(header)
        lines.append(sep)
        for mr in result.metric_rows:
            tr = threshold_map.get(mr.name)
            target = _fmt_target(tr.criteria, tr.target) if tr else "-"
            cells = []
            for i, v in enumerate(mr.values):
                if not tr:
                    # Informational metric — plain value, no delta/direction/best
                    cells.append(_fmt(v))
                    continue
                parts_cell = [_fmt(v)]
                # Direction vs baseline (skip for baseline itself)
                if i > 0:
                    d = mr.deltas[i]
                    direction = mr.directions[i]
                    if d is not None:
                        parts_cell.append(f"({_fmt_delta(d)}, {direction})")
                # Threshold check vs absolute target
                met = _check_threshold(v, tr.criteria, tr.target)
                parts_cell.append(_threshold_label(met))
                cells.append(" ".join(parts_cell))
            best = (
                run_labels[mr.best_run_index]
                if (mr.best_run_index is not None and tr)
                else "-"
            )
            lines.append(
                f"| {mr.name} | {target} | " + " | ".join(cells) + f" | {best} |"
            )
        lines.append("")

    show_items = result.conditions.row_level_valid if result.conditions else True
    if result.item_rows and show_items:
        threshold_map = {tr.evaluator: tr for tr in result.threshold_rows}
        lines.append("## Item Verdicts")
        lines.append("")
        header = "| Row | " + " | ".join(run_labels) + " |"
        sep = "|---:|" + "|".join("---" for _ in run_labels) + "|"
        lines.append(header)
        lines.append(sep)
        for ir in result.item_rows:
            cells = []
            for run_idx, passed in enumerate(ir.passed_all):
                parts_cell = []
                # Show per-evaluator scores for this row
                for eval_name, scores_list in ir.scores.items():
                    score = scores_list[run_idx] if run_idx < len(scores_list) else None
                    if score is not None:
                        tr = threshold_map.get(eval_name)
                        if tr:
                            met = _check_threshold(score, tr.criteria, tr.target)
                            parts_cell.append(
                                f"{eval_name}: {_fmt(score)} {_threshold_label(met)}"
                            )
                        else:
                            parts_cell.append(f"{eval_name}: {_fmt(score)}")
                if not parts_cell:
                    parts_cell.append("PASS" if passed else "FAIL")
                cells.append("; ".join(parts_cell))
            lines.append(f"| {ir.row_index} | " + " | ".join(cells) + " |")
    elif result.item_rows and not show_items:
        lines.append("## Item Verdicts")
        lines.append("")
        lines.append(
            "*Skipped — datasets differ across runs so row-level comparison is not meaningful.*"
        )

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Comparison HTML report (N runs)
# ---------------------------------------------------------------------------


def generate_comparison_html(result: ComparisonResult) -> str:
    has_reg = result.summary.any_regressions
    verdict_badge = (
        _badge("REGRESSIONS DETECTED", "regressed")
        if has_reg
        else _badge("NO REGRESSIONS", "improved")
    )
    run_labels = [r.run_id for r in result.runs]
    cond = result.conditions
    type_labels = {
        "agent": "Agent Comparison",
        "model": "Model Comparison",
        "dataset": "Dataset Coverage",
        "general": "General Comparison",
    }
    threshold_map = {tr.evaluator: tr for tr in result.threshold_rows}

    # Pre-compute per-run row pass counts (across all threshold evaluators)
    run_row_pass: list[tuple[int, int]] = []  # (passed, total) per run
    for run_idx in range(len(result.runs)):
        total = len(result.item_rows)
        passed = sum(1 for ir in result.item_rows if ir.passed_all[run_idx])
        run_row_pass.append((passed, total))

    # Pre-compute per-evaluator row pass rates
    eval_row_rates: dict[str, list[tuple[int, int]]] = {}
    for tr in result.threshold_rows:
        rates = []
        for run_idx in range(len(result.runs)):
            total = 0
            passed = 0
            for ir in result.item_rows:
                scores_list = ir.scores.get(tr.evaluator, [])
                score = scores_list[run_idx] if run_idx < len(scores_list) else None
                if score is not None:
                    total += 1
                    if _check_threshold(score, tr.criteria, tr.target):
                        passed += 1
            rates.append((passed, total))
        eval_row_rates[tr.evaluator] = rates

    parts: list[str] = []

    # --- Header ---
    parts.append(f"<h1>AgentOps Comparison Report {verdict_badge}</h1>")
    ctype = type_labels.get(cond.comparison_type, "") if cond else ""
    varying_str = ", ".join(cond.varying) if cond and cond.varying else ""
    parts.append(
        f'<div class="meta"><span>{ctype}</span><span>Varying: <strong>{_html_escape(varying_str)}</strong></span><span>{result.summary.run_count} runs</span></div>'
    )

    # --- Run Config ---
    varying_set = set(cond.varying) if cond else set()
    detail_fields = [
        ("Role", None, lambda r: ""),
        ("Target", None, lambda r: r.target or "-"),
        ("Model", None, lambda r: r.model or "-"),
        ("Agent", None, lambda r: r.agent_id or "-"),
        ("Dataset", "dataset", lambda r: r.dataset_name),
        ("Status", None, lambda r: ""),
    ]
    visible_fields = [
        (lbl, c, g) for lbl, c, g in detail_fields if c is None or c in varying_set
    ]

    cols = "".join(f"<th>{_html_escape(lbl)}</th>" for lbl in run_labels)
    parts.append(f"<table><thead><tr><th></th>{cols}</tr></thead><tbody>")
    for field, _ckey, getter in visible_fields:
        cells = ""
        for i, r in enumerate(result.runs):
            if field == "Role":
                cells += (
                    f"<td>{'<strong>Baseline</strong>' if i == 0 else f'Run {i}'}</td>"
                )
            elif field == "Status":
                p, t = run_row_pass[i]
                pct = int(p / t * 100) if t > 0 else 0
                if r.overall_passed:
                    cells += f"<td>{_status_badge(True)} <small>({pct}% · {p}/{t})</small></td>"
                else:
                    cells += f"<td>{_status_badge(False)} <small>({pct}% · {p}/{t})</small></td>"
            else:
                cells += f"<td>{_html_escape(getter(r))}</td>"
        parts.append(f"<tr><td><strong>{field}</strong></td>{cells}</tr>")
    parts.append("</tbody></table>")

    # --- Evaluators ---
    if result.metric_rows:
        parts.append("<h2>Evaluators</h2>")
        cols = "".join(
            f'<th class="num">{_html_escape(lbl)}</th>' for lbl in run_labels
        )
        parts.append(
            f"<table><thead><tr><th>Evaluator</th><th>Target</th>{cols}</tr></thead><tbody>"
        )
        for mr in result.metric_rows:
            tr = threshold_map.get(mr.name)
            target = _fmt_target(tr.criteria, tr.target) if tr else "-"
            cells = ""
            for i, v in enumerate(mr.values):
                if not tr:
                    # Informational metric — plain value only
                    cells += (
                        f'<td class="num" style="color:var(--muted)">{_fmt(v)}</td>'
                    )
                    continue
                is_best = mr.best_run_index == i
                # Dot indicator
                met = _check_threshold(v, tr.criteria, tr.target)
                dot = (
                    '<span style="color:var(--green)">●</span> '
                    if met
                    else '<span style="color:var(--red)">●</span> '
                )
                # Value
                val_str = _fmt(v)
                # Delta + arrow
                delta_str = ""
                if i > 0:
                    d = mr.deltas[i]
                    direction = mr.directions[i]
                    if d is not None:
                        arrow = (
                            "↑"
                            if direction == "improved"
                            else ("↓" if direction == "regressed" else "→")
                        )
                        color = (
                            "var(--green)"
                            if direction == "improved"
                            else (
                                "var(--red)"
                                if direction == "regressed"
                                else "var(--muted)"
                            )
                        )
                        delta_str = f' <small style="color:{color}">{arrow} {_fmt_delta(d)}</small>'
                # Row pass rate
                rate_str = ""
                if mr.name in eval_row_rates:
                    p, t = eval_row_rates[mr.name][i]
                    if t > 0:
                        rate_str = (
                            f' <small style="color:var(--muted)">({p}/{t})</small>'
                        )
                # Best highlight
                best_style = (
                    "background:var(--green-bg);font-weight:600;border-radius:.25rem;padding:.1rem .3rem;"
                    if is_best
                    else ""
                )
                inner = f"{dot}{val_str}{delta_str}{rate_str}"
                if is_best:
                    cells += f'<td class="num"><span style="{best_style}">{inner}</span></td>'
                else:
                    cells += f'<td class="num">{inner}</td>'
            parts.append(
                f"<tr><td>{_html_escape(mr.name)}</td><td>{_html_escape(target)}</td>{cells}</tr>"
            )
        parts.append("</tbody></table>")

    # --- Row Details ---
    show_items = result.conditions.row_level_valid if result.conditions else True
    if result.item_rows and show_items:
        parts.append("<h2>Row Details</h2>")
        cols = "".join(f"<th>{_html_escape(lbl)}</th>" for lbl in run_labels)
        parts.append(
            f'<table><thead><tr><th class="num">Row</th>{cols}</tr></thead><tbody>'
        )
        for ir in result.item_rows:
            cells = ""
            for run_idx in range(len(result.runs)):
                parts_cell = []
                for eval_name, scores_list in ir.scores.items():
                    score = scores_list[run_idx] if run_idx < len(scores_list) else None
                    if score is not None:
                        tr = threshold_map.get(eval_name)
                        if tr:
                            met = _check_threshold(score, tr.criteria, tr.target)
                            dot = (
                                '<span style="color:var(--green)">●</span>'
                                if met
                                else '<span style="color:var(--red)">●</span>'
                            )
                            parts_cell.append(
                                f"{dot} {_html_escape(eval_name)}: {_fmt(score)}"
                            )
                        else:
                            parts_cell.append(
                                f"{_html_escape(eval_name)}: {_fmt(score)}"
                            )
                if not parts_cell:
                    parts_cell.append(_status_badge(ir.passed_all[run_idx]))
                cells += f"<td>{'<br>'.join(parts_cell)}</td>"
            parts.append(f'<tr><td class="num">{ir.row_index}</td>{cells}</tr>')
        parts.append("</tbody></table>")
    elif result.item_rows and not show_items:
        parts.append("<h2>Row Details</h2>")
        parts.append(
            '<p style="color:var(--yellow);font-size:.85rem">Skipped — datasets differ across runs, row-level comparison not meaningful.</p>'
        )

    # --- Fixed Parameters ---
    if cond and cond.fixed:
        parts.append("<h2>Fixed Parameters</h2>")
        parts.append(
            "<table><thead><tr><th>Parameter</th><th>Value</th></tr></thead><tbody>"
        )
        for k, v in cond.fixed.items():
            parts.append(
                f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>"
            )
        parts.append("</tbody></table>")

    return _wrap_page("AgentOps Comparison Report", "\n".join(parts))
