"""Markdown renderer for watchdog agent findings."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List

from agentops.agent.analyzer import AnalysisResult
from agentops.agent.findings import Category, Finding, Severity, severity_emoji
from agentops.agent.knowledge import find_waf_item
from agentops.agent.maturity import MaturityAssessment, compute_level

_CATEGORY_ORDER: List[Category] = [
    Category.QUALITY,
    Category.PERFORMANCE,
    Category.RELIABILITY,
    Category.OPERATIONAL_EXCELLENCE,
    Category.SECURITY,
    Category.RESPONSIBLE_AI,
]

_CATEGORY_LABEL: Dict[Category, str] = {
    Category.QUALITY: "Quality",
    Category.PERFORMANCE: "Performance Efficiency",
    Category.RELIABILITY: "Reliability",
    Category.OPERATIONAL_EXCELLENCE: "Operational Excellence",
    Category.SECURITY: "Security",
    Category.RESPONSIBLE_AI: "Responsible AI",
}

_CATEGORY_FOOTER: Dict[Category, str] = {
    Category.SECURITY: (
        "_Audit reference: Microsoft Well-Architected Framework for AI "
        "workloads - Security pillar - "
        "https://learn.microsoft.com/azure/well-architected/ai/security_"
    ),
}


def _format_diagnostics_row(name: str, diagnostics: dict) -> str:
    status = diagnostics.get("status", "unknown")
    detail = diagnostics.get("reason") or diagnostics.get("runs_loaded") or ""
    return f"| `{name}` | `{status}` | {detail} |"


def _format_finding_row(finding: Finding) -> str:
    return (
        f"| {severity_emoji(finding.severity)} `{finding.severity.value}` "
        f"| `{finding.id}` | {finding.title} | `{finding.source}` |"
    )


def _verdict_banner(result: AnalysisResult) -> str:
    if not result.findings:
        return "## Verdict: ✅ No issues detected"
    max_sev = result.max_severity
    if max_sev == Severity.CRITICAL:
        return "## Verdict: 🚨 CRITICAL issues found"
    if max_sev == Severity.WARNING:
        return "## Verdict: ⚠️ Warnings found"
    return "## Verdict: ℹ️ Informational findings"


def _group_by_category(findings: List[Finding]) -> Dict[Category, List[Finding]]:
    grouped: Dict[Category, List[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.category, []).append(f)
    return grouped


def _render_finding_detail(lines: List[str], finding: Finding, workspace=None) -> None:
    lines.append(
        f"#### {severity_emoji(finding.severity)} `{finding.id}` - {finding.title}"
    )
    lines.append("")
    lines.append(f"- **Severity:** `{finding.severity.value}`")
    lines.append(f"- **Category:** `{finding.category.value}`")
    lines.append(f"- **Source:** `{finding.source}`")
    waf = find_waf_item(finding.id, workspace=workspace)
    if waf is not None:
        lines.append(
            f"- **WAF:** `{waf.pillar}` / `{waf.area}` - [{waf.item_id}]({waf.reference_url})"
        )
    lines.append("")
    lines.append(finding.summary)
    lines.append("")
    lines.append(f"**Recommendation:** {finding.recommendation}")
    lines.append("")
    if finding.evidence:
        lines.append("**Evidence:**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(finding.evidence, indent=2, default=str))
        lines.append("```")
        lines.append("")


def render_report(result: AnalysisResult) -> str:
    lines: List[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append("# AgentOps Watchdog Report")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")
    lines.append(_verdict_banner(result))
    lines.append("")

    # Summary counts
    sev_counts = {Severity.CRITICAL: 0, Severity.WARNING: 0, Severity.INFO: 0}
    cat_counts: Dict[Category, int] = {c: 0 for c in _CATEGORY_ORDER}
    for f in result.findings:
        sev_counts[f.severity] += 1
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1

    lines.append("## Summary")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    lines.append(f"| 🚨 Critical | {sev_counts[Severity.CRITICAL]} |")
    lines.append(f"| ⚠️  Warning  | {sev_counts[Severity.WARNING]} |")
    lines.append(f"| ℹ️  Info     | {sev_counts[Severity.INFO]} |")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|---|---|")
    for cat in _CATEGORY_ORDER:
        lines.append(f"| {_CATEGORY_LABEL[cat]} | {cat_counts.get(cat, 0)} |")
    lines.append("")

    # Sources
    lines.append("## Sources")
    lines.append("")
    lines.append("| Source | Status | Detail |")
    lines.append("|---|---|---|")
    for name, diag in result.diagnostics.items():
        lines.append(_format_diagnostics_row(name, diag))
    lines.append("")

    # Findings grouped by category
    if result.findings:
        grouped = _group_by_category(result.findings)
        lines.append("## Findings")
        lines.append("")
        for cat in _CATEGORY_ORDER:
            bucket = grouped.get(cat)
            if not bucket:
                continue
            lines.append(f"### {_CATEGORY_LABEL[cat]}")
            lines.append("")
            lines.append("| Severity | ID | Title | Source |")
            lines.append("|---|---|---|---|")
            for f in bucket:
                lines.append(_format_finding_row(f))
            lines.append("")
            for f in bucket:
                _render_finding_detail(lines, f, workspace=result.workspace)
            footer = _CATEGORY_FOOTER.get(cat)
            if footer:
                lines.append(footer)
                lines.append("")
    else:
        lines.append("## Findings")
        lines.append("")
        lines.append("_No findings - all configured checks passed._")
        lines.append("")

    # GenAIOps Maturity Model summary
    maturity = compute_level(result.findings, result.history)
    lines.append("## GenAIOps Maturity")
    lines.append("")
    lines.append(
        f"- **Current level:** L{maturity.level} - {maturity.label}"
    )
    if maturity.next_gap and maturity.next_gap != "no_history":
        lines.append(f"- **Next gap:** `{maturity.next_gap}`")
    if maturity.explanation:
        lines.append(f"- {maturity.explanation}")
    lines.append(
        "- _Reference: Microsoft GenAIOps Maturity Model - "
        "https://techcommunity.microsoft.com/blog/azure-ai-services-blog/genaiops_"
    )
    lines.append("")

    # History appendix
    if result.history and result.history.runs:
        lines.append("## Recent runs")
        lines.append("")
        lines.append("| Run ID | Timestamp | Items pass | Run pass |")
        lines.append("|---|---|---|---|")
        for run in result.history.runs[-10:]:
            ts = run.timestamp.strftime("%Y-%m-%d %H:%M") if run.timestamp else "-"
            items = (
                f"{run.items_passed_all}/{run.items_total}"
                if run.items_total
                else "-"
            )
            run_pass = (
                "✅" if run.run_pass else "❌" if run.run_pass is False else "-"
            )
            lines.append(f"| `{run.run_id}` | {ts} | {items} | {run_pass} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def short_chat_summary(result: AnalysisResult) -> str:
    """Compact one-screen summary used by the Copilot Extension server."""
    if not result.findings:
        return "✅ No issues detected by the AgentOps watchdog."
    counts = {Severity.CRITICAL: 0, Severity.WARNING: 0, Severity.INFO: 0}
    for f in result.findings:
        counts[f.severity] += 1
    parts = [
        f"AgentOps watchdog found {len(result.findings)} finding(s): "
        f"🚨 {counts[Severity.CRITICAL]} critical, "
        f"⚠️ {counts[Severity.WARNING]} warning, "
        f"ℹ️ {counts[Severity.INFO]} info."
    ]
    parts.append("")
    parts.append("Top items:")
    for f in result.findings[:5]:
        parts.append(
            f"- {severity_emoji(f.severity)} **{f.id}** - `{f.category.value}` - {f.title}"
        )
    return "\n".join(parts)
