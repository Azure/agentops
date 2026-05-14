"""Local web dashboard for the AgentOps watchdog agent.

``agentops dashboard`` boots a tiny FastAPI server that reads the
analysis history from ``.agentops/agent/history.jsonl`` **and** the
evaluation history from ``.agentops/results/*/results.json``, then
serves a single dashboard page in a dark theme. No external frontend
dependencies (sparklines are inline SVG); no Azure resource required.

The server is intentionally read-only and bound to ``127.0.0.1`` by
default - it is a developer-tool surface, not a production service.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentops.agent.history import AnalysisRecord, load_analysis_history
from agentops.agent.time_range import TimeRange, parse_time_range, preset_keys


# ---------------------------------------------------------------------------
# Data shaping for the dashboard
# ---------------------------------------------------------------------------


_CATEGORY_LABELS = {
    "quality": "Quality",
    "performance": "Performance Efficiency",
    "reliability": "Reliability",
    "operational_excellence": "Operational Excellence",
    "security": "Security",
    "responsible_ai": "Responsible AI",
}

_BADGE_FOR_SEVERITY = {
    None: ("in range", "ok"),
    "info": ("info", "info"),
    "warning": ("warnings", "warn"),
    "critical": ("critical", "crit"),
}

# Quality-metric cards rendered when eval history is available.
# Ordered so the dashboard layout is stable across runs.
_QUALITY_METRICS: List[Tuple[str, str, str]] = [
    ("coherence", "Coherence", "/5"),
    ("fluency", "Fluency", "/5"),
    ("similarity", "Similarity", "/5"),
    ("f1_score", "F1 score", ""),
    ("groundedness", "Groundedness", "/5"),
    ("relevance", "Relevance", "/5"),
    ("avg_latency_seconds", "Latency", "s"),
]


def build_dashboard_payload(
    workspace: Path,
    *,
    history: Optional[List[AnalysisRecord]] = None,
    time_range: Optional[TimeRange] = None,
) -> Dict[str, Any]:
    """Reduce raw history + eval runs into a dashboard-ready dict.

    Note: the production section is **not** fetched here. It is rendered
    as a placeholder in the initial HTML and filled in asynchronously by
    the browser hitting ``/api/production/html``. This keeps the initial
    page load fast (local file reads only) even when App Insights is
    slow to authenticate or query.
    """
    if time_range is None:
        time_range = parse_time_range()
    all_records = history if history is not None else load_analysis_history(workspace)
    records = _filter_records(all_records, time_range)
    eval_runs_all = _load_eval_runs(workspace, limit=200)
    eval_runs = _filter_eval_runs(eval_runs_all, time_range)
    telemetry = _telemetry_status()
    # Production is deferred to /api/production/html; render a placeholder.
    production = {"has_data": False, "deferred": telemetry.get("enabled", False), "cards": []}

    return {
        "workspace": str(workspace.resolve()),
        "foundry_project_url": _resolve_foundry_project_url(workspace),
        "foundry_compliance_url": _resolve_foundry_compliance_url(workspace),
        "foundry_setup_url": _foundry_setup_url(),
        "az_tenant_id": _az_tenant_id(),
        "time_range": {
            "key": time_range.key,
            "label": time_range.label,
            "start": time_range.start.isoformat(),
            "end": time_range.end.isoformat(),
            "hours": time_range.hours,
            "query": time_range.to_query(),
        },
        "telemetry": telemetry,
        "production": production,
        "eval": _build_eval_section(eval_runs),
        "metrics": _build_metrics_cards(eval_runs),
        "watchdog": _build_watchdog_section(records),
        "deployments": _build_deployments_section(workspace, time_range),
        "summary_counts": {
            "eval_runs": len(eval_runs),
            "analyses": len(records),
        },
    }


def _filter_records(records: List[AnalysisRecord], time_range: TimeRange) -> List[AnalysisRecord]:
    out: List[AnalysisRecord] = []
    for r in records:
        ts = _parse_iso(r.timestamp)
        if time_range.contains(ts):
            out.append(r)
    return out


def _filter_eval_runs(runs: List[Dict[str, Any]], time_range: TimeRange) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in runs:
        ts = _parse_iso(r.get("timestamp"))
        if time_range.contains(ts):
            out.append(r)
    return out


def _parse_iso(value: Any) -> Optional[Any]:
    """Coerce a value to a tz-aware UTC datetime, or return ``None``."""
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _build_production_section(
    telemetry: Dict[str, Any],
    *,
    time_range: Optional[TimeRange] = None,
) -> Dict[str, Any]:
    """Pull live App Insights data when telemetry is wired up."""
    if not telemetry.get("enabled"):
        return {"has_data": False, "cards": [], "skip_reason": "telemetry off"}

    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or os.getenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    if not conn and os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"):
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_from_env,
            )
            conn = resolve_appinsights_connection_from_env()
        except Exception:  # noqa: BLE001
            conn = None

    from agentops.agent.production_telemetry import (
        collect_production_metrics,
        extract_application_id,
    )
    app_id = extract_application_id(conn)
    hours = time_range.hours if time_range is not None else 24
    section = collect_production_metrics(app_id, lookback_hours=hours)

    # Attach a portal deep-link to every point so clicking jumps to App
    # Insights. Foundry has no per-bucket view, so portal_url is the most
    # useful destination available today.
    portal_url = telemetry.get("portal_url") if isinstance(telemetry, dict) else None
    if portal_url:
        for card in section.get("cards") or []:
            n = len(card.get("series") or [])
            if n:
                card["links"] = [portal_url] * n
    return section


def _build_eval_section(eval_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not eval_runs:
        return {
            "has_runs": False,
            "cards": [],
        }
    pass_series = [1.0 if r["passed"] else 0.0 for r in eval_runs]
    pass_rate = sum(pass_series) / len(pass_series) if pass_series else 0.0
    latest = eval_runs[-1]
    items_total_series = [float(r.get("items_total") or 0) for r in eval_runs]
    run_links = [r.get("report_link") for r in eval_runs]
    run_alt_links = [r.get("alt_link") for r in eval_runs]
    run_alt_labels = [r.get("alt_label") for r in eval_runs]

    # The section header already shows the "Foundry cloud" pill when the
    # latest run is a cloud run, so the per-card source footers stay short.
    # The detailed "cloud runs are cached locally" explanation lives in the
    # Eval runs help tooltip instead.
    latest_execution = latest.get("execution")

    cards: List[Dict[str, Any]] = [
        {
            "key": "total_runs",
            "label": "Eval runs",
            "value": len(eval_runs),
            "unit": "total",
            "series": [1.0] * len(eval_runs),  # constant - show as filled bar
            "labels": [_label_for_run(r) for r in eval_runs],
            "links": run_links,
            "alt_links": run_alt_links,
            "alt_labels": run_alt_labels,
            "badge": {"label": _badge_runs(len(eval_runs)), "tone": "info"},
            "help": (
                "Total agentops eval run invocations recorded under "
                ".agentops/results/. Cloud runs (execution: cloud) are "
                "executed by Foundry server-side, then downloaded and "
                "cached here - that is why even cloud runs show up under "
                "a local path. The trend line marks each run, oldest on "
                "the left."
                "\n\nBadge tiers:"
                "\n• under 3 runs - low sample"
                "\n• 3 to 9 - moderate sample"
                "\n• 10 or more - well sampled"
            ),
        },
        {
            "key": "pass_rate",
            "label": "Pass rate",
            "value": f"{int(pass_rate * 100)}%",
            "unit": "",
            "series": pass_series,
            "labels": [
                f"{_label_for_run(r)} · {'PASS' if r['passed'] else 'FAIL'}"
                f" · {r.get('execution') or 'local'}"
                for r in eval_runs
            ],
            "links": run_links,
            "alt_links": run_alt_links,
            "alt_labels": run_alt_labels,
            "badge": _badge_pass_rate(pass_rate),
            "help": (
                "Share of recorded runs whose summary.overall_passed is "
                "true. Hover the sparkline to see each run."
                "\n\nBadge tiers:"
                "\n• 90% or above - healthy"
                "\n• 70 to 89% - mixed"
                "\n• below 70% - unhealthy"
            ),
            "source": "Share of recorded runs that passed every configured threshold.",
        },
        {
            "key": "items",
            "label": "Dataset rows",
            "value": int(items_total_series[-1]) if items_total_series else 0,
            "unit": "evaluated",
            "series": items_total_series,
            "labels": [
                f"{_label_for_run(r)} · {int(r.get('items_total') or 0)} row(s)"
                f" · {r.get('execution') or 'local'}"
                for r in eval_runs
            ],
            "links": run_links,
            "alt_links": run_alt_links,
            "alt_labels": run_alt_labels,
            "badge": {"label": "in latest run", "tone": "muted"},
            "help": (
                "Number of dataset rows that AgentOps actually evaluated "
                "in the latest run. The dataset on disk may contain more "
                "rows that were skipped due to filters or errors."
            ),
            "source": "Number of dataset rows evaluated in the most recent run.",
        },
        {
            "key": "latest_run",
            "label": "Latest target",
            "value": latest["target"] or " - ",
            "unit": "",
            "value_kind": "text",
            "series": pass_series[-6:],
            "labels": [
                f"{_label_for_run(r)} · {r.get('target') or ' - '}"
                f" · {r.get('execution') or 'local'}"
                for r in eval_runs[-6:]
            ],
            "links": run_links[-6:],
            "alt_links": run_alt_links[-6:],
            "alt_labels": run_alt_labels[-6:],
            "badge": {
                "label": "passed" if latest["passed"] else "failed",
                "tone": "ok" if latest["passed"] else "crit",
            },
            "help": (
                "Agent or model identifier from the most recent run. The "
                "badge shows whether that run met every configured "
                "threshold (passed or failed)."
            ),
            "meta": [
                _format_iso_timestamp(latest["timestamp"]),
                f"duration: {latest['duration']:.1f}s" if latest["duration"] else "duration:  - ",
                f"execution: {latest['execution']}" if latest["execution"] else "execution:  - ",
            ],
            "source": "Agent or model identifier from the most recent run.",
        },
    ]
    return {
        "has_runs": True,
        "cards": cards,
        "latest_execution": latest_execution,
    }


def _label_for_run(run: Dict[str, Any]) -> str:
    """Build a human label for a sparkline point on the eval cards."""
    ts = run.get("timestamp") or ""
    # Trim to minute precision for hover-tip readability.
    ts = ts[:16].replace("T", " ") if isinstance(ts, str) else str(ts)
    return ts or " - "


def _build_metrics_cards(eval_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not eval_runs:
        return []
    cards: List[Dict[str, Any]] = []
    for key, label, unit in _QUALITY_METRICS:
        # Build aligned series + labels: keep only runs that reported this metric.
        paired = [
            (run, run["metrics"].get(key))
            for run in eval_runs
            if run["metrics"].get(key) is not None
        ]
        if not paired:
            continue
        series = [float(v) for _r, v in paired]
        labels = [
            f"{_label_for_run(r)} · {float(v):.2f}"
            for r, v in paired
        ]
        links = [r.get("report_link") for r, _v in paired]
        alt_links = [r.get("alt_link") for r, _v in paired]
        alt_labels = [r.get("alt_label") for r, _v in paired]
        latest = series[-1]
        is_latency = key == "avg_latency_seconds"
        badge = _metric_trend_badge(series, is_latency=is_latency)
        cards.append({
            "key": key,
            "label": label,
            "value": f"{latest:.2f}",
            "unit": unit,
            "series": series,
            "labels": labels,
            "links": links,
            "alt_links": alt_links,
            "alt_labels": alt_labels,
            "badge": badge,
            "help": (
                f"Average {label.lower()} across rows in the most recent "
                "run. The trend line shows the metric across recorded "
                "runs. Badge compares the latest run to the previous one: "
                "improved, regressed, stable, or baseline."
            ),
            "source": f"Average {label.lower()} score across runs in this window.",
        })
    return cards


def _build_watchdog_section(records: List[AnalysisRecord]) -> Dict[str, Any]:
    latest = records[-1] if records else None

    def _series(extractor) -> List[float]:
        return [float(extractor(r) or 0) for r in records]

    findings_series = _series(lambda r: r.findings_total)
    critical_series = _series(lambda r: r.findings_by_severity.get("critical", 0))
    record_labels = [_label_for_record(r) for r in records]

    latest_label, latest_badge = _latest_run_badge(latest)

    # Latest findings list. We project the dict directly from the
    # AnalysisRecord (which stores the same Finding.to_dict() payload
    # the watchdog produced) and sort by severity desc → category →
    # title so the most urgent items render first.
    latest_findings: List[Dict[str, Any]] = []
    if latest and latest.findings:
        latest_findings = sorted(
            latest.findings,
            key=lambda f: (
                -_SEVERITY_SORT_RANK.get(str(f.get("severity") or "").lower(), -1),
                str(f.get("category") or ""),
                str(f.get("title") or ""),
            ),
        )

    return {
        "has_history": bool(records),
        "history_count": len(records),
        "headline_cards": [
            {
                "key": "findings_total",
                "label": "Findings",
                "value": int(findings_series[-1]) if findings_series else 0,
                "unit": "total",
                "series": findings_series,
                "labels": [
                    f"{_label_for_record(r)} · {r.findings_total} finding(s)"
                    for r in records
                ],
                "badge": _headline_badge_total(findings_series),
                "help": (
                    "Total findings produced by the AgentOps doctor across "
                    "all recorded analyses. The badge compares the latest "
                    "run to the previous one."
                ),
                "source": "All findings produced by the AgentOps doctor across recorded analyses.",
            },
            {
                "key": "critical",
                "label": "Critical",
                "value": int(critical_series[-1]) if critical_series else 0,
                "unit": "open",
                "series": critical_series,
                "labels": [
                    f"{_label_for_record(r)} · {r.findings_by_severity.get('critical', 0)} critical"
                    for r in records
                ],
                "badge": _headline_badge_critical(critical_series),
                "help": (
                    "Findings tagged as critical severity in the latest "
                    "analysis. Treat any non-zero value as a fail-the-CI "
                    "candidate."
                ),
                "source": "Findings tagged as critical severity in the latest analysis.",
            },
            {
                "key": "last_analysis",
                "label": "Last analysis",
                "value": latest_label,
                "unit": "",
                "value_kind": "text",
                "series": findings_series[-6:],
                "labels": record_labels[-6:],
                "badge": latest_badge,
                "help": (
                    "When the most recent watchdog run finished. The "
                    "badge reflects how stale that analysis is relative "
                    "to now."
                ),
                "meta": _latest_run_meta(latest),
                "source": "When the most recent watchdog analysis finished.",
            },
        ],
        "latest_findings": latest_findings,
        "maturity": _maturity_for_dashboard(latest),
    }


def _maturity_for_dashboard(
    latest: Optional[AnalysisRecord],
) -> Optional[Dict[str, Any]]:
    """Project the GenAIOps maturity assessment onto the dashboard payload."""
    from agentops.agent.maturity import compute_level_from_ids

    if latest is None:
        return None
    finding_ids = [
        str(f.get("id") or "") for f in (latest.findings or []) if isinstance(f, dict)
    ]
    assessment = compute_level_from_ids(
        finding_ids=[fid for fid in finding_ids if fid],
        has_history=True,
    )
    return {
        "level": assessment.level,
        "label": assessment.label,
        "next_gap": assessment.next_gap,
        "explanation": assessment.explanation,
    }


# Severity rank used to sort the findings list (highest severity first).
_SEVERITY_SORT_RANK = {
    "critical": 2,
    "warning": 1,
    "info": 0,
}


def _label_for_record(record: AnalysisRecord) -> str:
    """Short timestamp label for a watchdog sparkline point."""
    ts = record.timestamp or ""
    return ts[:16].replace("T", " ") if isinstance(ts, str) else " - "


# ---------------------------------------------------------------------------
# Deployments (GitHub Actions workflow runs)
# ---------------------------------------------------------------------------


# Cached `gh run list` payload, keyed by workspace. Keeps the dashboard
# snappy on refresh while still picking up new runs within the TTL.
_DEPLOYMENTS_CACHE_TTL_SECONDS = 60.0
_deployments_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _build_deployments_section(
    workspace: Path,
    time_range: TimeRange,
) -> Dict[str, Any]:
    """Project recent GitHub Actions runs into the dashboard card shape.

    Uses the local ``gh`` CLI to list workflow runs for the repo that
    contains the workspace. We diagnose each failure mode separately so
    the empty-state can tell the user exactly what to fix instead of a
    generic "could not list workflow runs".
    """
    diag = _diagnose_gh_state(workspace)
    state = diag.get("state")

    if state == "gh-missing":
        return _deployments_empty(
            state,
            "GitHub CLI is not installed in this environment. "
            "Install it from <a href=\"https://cli.github.com\" target=\"_blank\" "
            "rel=\"noopener noreferrer\">cli.github.com</a> and run "
            "<code>gh auth login</code> to surface workflow runs here.",
        )
    if state == "not-git-repo":
        return _deployments_empty(
            state,
            "This workspace is not inside a Git repository, so there are no "
            "GitHub Actions runs to fetch. Open the dashboard from a clone "
            "of your repo to see this section populated.",
        )
    if state == "no-github-remote":
        return _deployments_empty(
            state,
            "This Git repository has no GitHub remote, so GitHub Actions does "
            "not apply. Push the repo to GitHub (or run this dashboard from a "
            "clone that already has an <code>origin</code> on GitHub) to use "
            "this section.",
        )
    if state == "gh-unauthenticated":
        return _deployments_empty(
            state,
            "<code>gh</code> is installed but not authenticated. "
            "Run <code>gh auth login</code> and then refresh this page.",
        )
    if state == "gh-failed":
        detail = diag.get("detail") or ""
        suffix = f" Error: <code>{_html_escape(detail)}</code>" if detail else ""
        return _deployments_empty(
            state,
            "Could not list workflow runs from GitHub even though "
            "<code>gh</code> is authenticated. Confirm the repo has GitHub "
            "Actions enabled and that your token has the <code>repo</code> "
            f"scope (<code>gh auth refresh -s repo</code>).{suffix}",
        )

    # state == "ok"
    runs = diag.get("runs") or []
    if not runs:
        return _deployments_empty(
            "no-runs-total",
            "No GitHub Actions runs exist on this repository yet. Run "
            "<code>agentops workflow generate</code> to scaffold a workflow, "
            "commit it under <code>.github/workflows/</code>, and trigger it "
            "(open a PR or push to a branch).",
        )

    windowed = _filter_workflow_runs(runs, time_range)
    if not windowed:
        return _deployments_empty(
            "no-runs",
            "No workflow runs fell inside the selected window. Widen the "
            "time range above (try 30D) or trigger a new run.",
        )

    windowed = list(reversed(windowed))  # oldest → newest for sparkline left-to-right

    total = len(windowed)
    successes = sum(1 for r in windowed if (r.get("conclusion") or "").lower() == "success")
    success_rate = successes / total if total else 0.0
    pass_series = [1.0 if (r.get("conclusion") or "").lower() == "success" else 0.0 for r in windowed]
    run_labels = [_label_for_workflow_run(r) for r in windowed]
    run_links = [r.get("url") for r in windowed]

    latest = windowed[-1]
    latest_conclusion = (latest.get("conclusion") or latest.get("status") or " - ").lower()
    latest_label = latest.get("workflowName") or latest.get("name") or " - "

    cards: List[Dict[str, Any]] = [
        {
            "key": "workflow_runs",
            "label": "Workflow runs",
            "value": total,
            "unit": "total",
            "series": [1.0] * total,
            "labels": run_labels,
            "links": run_links,
            "badge": {"label": _badge_runs_label(total), "tone": "info"},
            "help": (
                "Recent GitHub Actions runs in this repo, fetched via "
                "<code>gh run list</code>. Hover the sparkline to see the "
                "workflow name and conclusion; click a dot to open the "
                "run in GitHub."
            ),
            "source": "Recent GitHub Actions runs in this repository.",
        },
        {
            "key": "success_rate",
            "label": "Success rate",
            "value": f"{int(success_rate * 100)}%",
            "unit": "",
            "series": pass_series,
            "labels": [
                f"{_label_for_workflow_run(r)} · {(r.get('conclusion') or r.get('status') or ' - ')}"
                for r in windowed
            ],
            "links": run_links,
            "badge": _success_rate_badge(success_rate),
            "help": (
                "Share of recent workflow runs that finished with "
                "conclusion <code>success</code>. Failed, cancelled, and "
                "skipped runs all count against this rate."
                "\n\nBadge tiers:"
                "\n• 90% or above - healthy"
                "\n• 70 to 89% - mixed"
                "\n• below 70% - unhealthy"
            ),
            "source": "Share of recent workflow runs that finished successfully.",
        },
        {
            "key": "latest_workflow",
            "label": "Latest run",
            "value": latest_label,
            "unit": "",
            "value_kind": "text",
            "series": pass_series[-6:],
            "labels": run_labels[-6:],
            "links": run_links[-6:],
            "badge": _workflow_conclusion_badge(latest_conclusion),
            "help": (
                "The most recent GitHub Actions run for this repo. "
                "Click the card's sparkline dots to open the corresponding "
                "run in GitHub."
            ),
            "meta": [
                _format_iso_timestamp(latest.get("createdAt") or ""),
                f"branch: {latest.get('headBranch') or ' - '}",
                f"event: {latest.get('event') or ' - '}",
            ],
            "source": "Most recent GitHub Actions run for this repository.",
            "alt_link": latest.get("url"),
            "alt_label": "Open in GitHub",
        },
    ]

    return {"has_data": True, "cards": cards}


def _deployments_empty(reason: str, hint_html: str) -> Dict[str, Any]:
    """Standard shape for an empty Deployments section."""
    return {"has_data": False, "reason": reason, "hint": hint_html, "cards": []}


def _diagnose_gh_state(workspace: Path) -> Dict[str, Any]:
    """Diagnose why ``gh run list`` would (or would not) work and, if it
    does, return the list of recent workflow runs.

    Returns a dict with a ``state`` key plus extras:

    - ``{"state": "gh-missing"}``
    - ``{"state": "not-git-repo"}``
    - ``{"state": "no-github-remote"}``
    - ``{"state": "gh-unauthenticated"}``
    - ``{"state": "gh-failed", "detail": "..."}``
    - ``{"state": "ok", "runs": [...]}``

    Cached for ``_DEPLOYMENTS_CACHE_TTL_SECONDS`` per workspace.
    """
    import time
    key = str(workspace.resolve())
    now = time.time()
    cached = _deployments_cache.get(key)
    if cached and now - cached[0] < _DEPLOYMENTS_CACHE_TTL_SECONDS:
        return cached[1]

    result = _diagnose_gh_state_uncached(workspace)
    _deployments_cache[key] = (now, result)
    return result


def _diagnose_gh_state_uncached(workspace: Path) -> Dict[str, Any]:
    if shutil.which("gh") is None:
        return {"state": "gh-missing"}

    # Is the workspace inside a git checkout? `git rev-parse` answers
    # without raising on subdirectories of a repo root.
    git_check = _run_quick(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace)
    if git_check is None or git_check.returncode != 0:
        return {"state": "not-git-repo"}

    # Does the repo have any remotes at all? `gh repo view` reports this
    # as "no git remotes found" but its wording is unstable, so we look
    # at git directly first.
    remotes = _run_quick(["git", "remote"], cwd=workspace)
    if remotes is not None and remotes.returncode == 0:
        remote_names = [
            line.strip() for line in (remotes.stdout or "").splitlines() if line.strip()
        ]
        if not remote_names:
            return {"state": "no-github-remote", "detail": "no git remotes"}

    # We have at least one remote. Confirm gh can resolve it as a GitHub
    # repo (the user might be on a self-hosted-only or non-GitHub remote)
    # and that the user is authenticated.
    repo_check = _run_quick(
        ["gh", "repo", "view", "--json", "nameWithOwner"], cwd=workspace,
    )
    if repo_check is None:
        return {"state": "gh-failed", "detail": "gh repo view did not run"}
    if repo_check.returncode != 0:
        stderr = (repo_check.stderr or "").lower()
        # Order matters: gh's "remote is not GitHub" message includes
        # "please use `gh auth login`", which would otherwise be matched
        # by the auth check below.
        if (
            "github host" in stderr
            or "known github" in stderr
            or "no git remote" in stderr
            or "no git remotes" in stderr
            or "not a github" in stderr
            or "no github" in stderr
            or "could not determine" in stderr
        ):
            return {"state": "no-github-remote", "detail": (repo_check.stderr or "").strip()[:200]}
        if "not logged" in stderr or "authentication required" in stderr:
            return {"state": "gh-unauthenticated"}
        return {
            "state": "gh-failed",
            "detail": (repo_check.stderr or "").strip().splitlines()[-1][:200]
            if repo_check.stderr else "",
        }

    # Repo confirmed - fetch runs.
    runs_proc = _run_quick(
        [
            "gh", "run", "list",
            "--limit", "50",
            "--json", "conclusion,status,createdAt,name,displayTitle,url,headBranch,event,workflowName",
        ],
        cwd=workspace,
        timeout=15,
    )
    if runs_proc is None or runs_proc.returncode != 0:
        detail = ""
        if runs_proc is not None and runs_proc.stderr:
            detail = runs_proc.stderr.strip().splitlines()[-1][:200]
        return {"state": "gh-failed", "detail": detail}
    try:
        runs = json.loads(runs_proc.stdout or "[]")
    except (ValueError, json.JSONDecodeError):
        return {"state": "gh-failed", "detail": "unparseable gh run list output"}
    if not isinstance(runs, list):
        return {"state": "gh-failed", "detail": "unexpected gh run list shape"}

    return {"state": "ok", "runs": runs}


def _run_quick(
    cmd: List[str], *, cwd: Path, timeout: int = 10,
) -> Optional[subprocess.CompletedProcess]:
    """Best-effort subprocess wrapper: returns ``None`` if the command
    cannot run at all (binary missing, OS error, timeout). Otherwise
    returns the ``CompletedProcess`` so callers can inspect returncode
    and stderr without try/except boilerplate.
    """
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _filter_workflow_runs(
    runs: List[Dict[str, Any]],
    time_range: TimeRange,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in runs:
        ts = _parse_iso(r.get("createdAt"))
        if time_range.contains(ts):
            out.append(r)
    return out


def _label_for_workflow_run(run: Dict[str, Any]) -> str:
    name = run.get("workflowName") or run.get("name") or "workflow"
    ts = run.get("createdAt") or ""
    short_ts = ts[:16].replace("T", " ") if isinstance(ts, str) else ""
    return f"{name} · {short_ts}".strip(" ·")


def _badge_runs_label(count: int) -> str:
    if count == 0:
        return "no runs"
    if count < 5:
        return "few runs"
    if count < 20:
        return "active"
    return "very active"


def _success_rate_badge(rate: float) -> Dict[str, str]:
    if rate >= 0.9:
        return {"label": "healthy", "tone": "ok"}
    if rate >= 0.7:
        return {"label": "mixed", "tone": "warn"}
    return {"label": "unhealthy", "tone": "crit"}


def _workflow_conclusion_badge(conclusion: str) -> Dict[str, str]:
    c = (conclusion or "").lower()
    if c == "success":
        return {"label": "passed", "tone": "ok"}
    if c in ("failure", "timed_out", "startup_failure"):
        return {"label": c.replace("_", " "), "tone": "crit"}
    if c == "cancelled":
        return {"label": "cancelled", "tone": "warn"}
    if c in ("in_progress", "queued", "waiting", "requested", "pending"):
        return {"label": c.replace("_", " "), "tone": "info"}
    return {"label": c or " - ", "tone": "muted"}


# ---------------------------------------------------------------------------
# Eval run loading
# ---------------------------------------------------------------------------


def _load_eval_runs(workspace: Path, *, limit: int = 24) -> List[Dict[str, Any]]:
    """Scan ``.agentops/results/<timestamp>/results.json`` and project the
    fields the dashboard cares about. ``latest/`` is skipped because it is
    a mirror of the most recent timestamped run.
    """
    results_root = workspace / ".agentops" / "results"
    if not results_root.exists():
        return []

    candidates: List[Tuple[str, Path]] = []
    for entry in results_root.iterdir():
        if not entry.is_dir() or entry.name == "latest":
            continue
        results_file = entry / "results.json"
        if results_file.exists():
            candidates.append((entry.name, results_file))

    # Sort by directory name (timestamp prefix is sortable).
    candidates.sort(key=lambda kv: kv[0])
    candidates = candidates[-limit:]

    runs: List[Dict[str, Any]] = []
    for run_id, path in candidates:
        run = _project_run(path, run_id=run_id)
        if run is not None:
            runs.append(run)
    return runs


def _project_run(path: Path, *, run_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    summary = data.get("summary") or {}
    target = data.get("target") or {}
    cfg = data.get("config") or {}

    # Pick the deepest available view of the run. Cloud runs publish a
    # Foundry portal URL via cloud_evaluation.json; fall back to a local
    # dashboard endpoint that renders the run's report.md.
    cloud_report_url: Optional[str] = None
    cloud_meta = path.parent / "cloud_evaluation.json"
    if cloud_meta.exists():
        try:
            meta = json.loads(cloud_meta.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                url = meta.get("report_url")
                if isinstance(url, str) and url:
                    cloud_report_url = _with_tenant(url)
        except (OSError, ValueError):
            pass
    # Clicking a sparkline dot opens the most useful destination for that
    # run: the Foundry cloud evaluation page when the run was published,
    # otherwise the local report. The other URL is exposed as an
    # alternative the dashboard surfaces on hover so the user can pick
    # either side.
    local_report_url = f"/api/runs/{run_id}/report"
    report_link = cloud_report_url or local_report_url
    alt_link = local_report_url if cloud_report_url else None
    alt_label = "Local report" if cloud_report_url else None

    return {
        "run_id": run_id,
        "timestamp": data.get("started_at") or data.get("finished_at"),
        "duration": _safe_float(data.get("duration_seconds")),
        "target": target.get("raw") if isinstance(target, dict) else None,
        "passed": bool(summary.get("overall_passed")) if isinstance(summary, dict) else False,
        "items_total": summary.get("items_total") if isinstance(summary, dict) else None,
        "items_passed_all": summary.get("items_passed_all") if isinstance(summary, dict) else None,
        "metrics": data.get("aggregate_metrics") if isinstance(data.get("aggregate_metrics"), dict) else {},
        "execution": cfg.get("execution") if isinstance(cfg, dict) else None,
        "cloud_report_url": cloud_report_url,
        "local_report_url": local_report_url,
        "report_link": report_link,
        "alt_link": alt_link,
        "alt_label": alt_label,
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_iso_timestamp(value: Any) -> str:
    """Render an ISO-8601 timestamp as a compact, human-friendly string.

    Examples:
      ``2026-05-12T22:19:29.306816+00:00`` -> ``2026-05-12 22:19 UTC``
      ``2026-05-12T22:19:29``               -> ``2026-05-12 22:19``

    Falls back to the raw string when it can't be parsed, so we never
    drop information silently.
    """
    if not value:
        return ""
    s = str(value)
    try:
        from datetime import datetime, timezone
        normalized = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None and dt.utcoffset() is not None:
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return s[:16].replace("T", " ") if "T" in s else s


def _foundry_setup_url() -> Optional[str]:
    """One-time Foundry tenant primer.

    Opening this URL in a fresh Foundry session (or signing out first)
    establishes the directory matching your az login tenant. After the
    user opens it once and confirms the directory in Foundry, every
    deep-link from the dashboard lands in the right tenant without a
    further switch.
    """
    tenant = _az_tenant_id()
    if not tenant:
        return None
    return f"https://ai.azure.com/?tid={tenant}"


def _resolve_foundry_project_url(workspace: Path) -> Optional[str]:
    """Return a stable Foundry URL for the powered-by badge.

    Prefers stripping the ``/build/evaluations/...`` suffix off the most
    recent run's cloud report URL so the badge lands on the project
    root. Appends ``?tid=<tenant>`` (from ``az account show``) so the
    Foundry portal silently switches directory and doesn't strand the
    user on a "wrong tenant" page when their browser's session is in a
    different directory.
    """
    base = _resolve_foundry_project_root(workspace)
    if base is None:
        return _with_tenant("https://ai.azure.com")
    return _with_tenant(base + "/build/agents")


def _resolve_foundry_compliance_url(workspace: Path) -> Optional[str]:
    """Return the Foundry > Operate > Compliance deep-link for this project.

    Resolves the same way as the project URL but lands on the Operate >
    Compliance surface so users can click straight from the watchdog
    section header into the page that owns runtime guardrails,
    security posture, and data governance.
    """
    base = _resolve_foundry_project_root(workspace)
    if base is None:
        return None
    return _with_tenant(base + "/operate/compliance")


def _resolve_foundry_project_root(workspace: Path) -> Optional[str]:
    """Pull the project-root prefix (everything before ``/build/...``) out
    of the most recent cloud_evaluation.json. Returns ``None`` when there
    is no cloud run yet - callers fall back to defaults or hide the link.
    """
    results_root = workspace / ".agentops" / "results"
    if not results_root.is_dir():
        return None
    candidates: List[Tuple[str, Path]] = []
    for entry in results_root.iterdir():
        if not entry.is_dir() or entry.name == "latest":
            continue
        meta = entry / "cloud_evaluation.json"
        if meta.exists():
            candidates.append((entry.name, meta))
    candidates.sort(key=lambda kv: kv[0], reverse=True)
    for _, meta in candidates:
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url = data.get("report_url") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url:
            continue
        for marker in ("/build/evaluations/", "/build/evaluation/"):
            idx = url.find(marker)
            if idx >= 0:
                return url[:idx]
        # No /build/ segment - assume the full URL is already the root.
        return url.rstrip("/")
    return None


def _with_tenant(url: str) -> str:
    """Append ``?tid=<az-tenant>`` (or ``&tid=``) to a Foundry URL when
    an az-login tenant is available, so the portal opens with the right
    directory pre-selected. No-ops when no tenant can be resolved.
    """
    tenant = _az_tenant_id()
    if not tenant:
        return url
    if "tid=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tid={tenant}"


_TENANT_CACHE: Dict[str, Optional[str]] = {}


def _az_tenant_id() -> Optional[str]:
    """Return the active Azure tenant id from ``az account show``.

    Cached for the process so we don't shell out per request. Returns
    ``None`` when az CLI is missing, not logged in, or the call fails
    for any reason - the surrounding URL still works without ``?tid=``.
    """
    if "value" in _TENANT_CACHE:
        return _TENANT_CACHE["value"]
    tenant: Optional[str] = None
    try:
        az = shutil.which("az") or shutil.which("az.cmd")
        if az:
            result = subprocess.run(
                [az, "account", "show", "--query", "tenantId", "-o", "tsv"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                if value and len(value) >= 36:
                    tenant = value
    except Exception:  # noqa: BLE001
        tenant = None
    _TENANT_CACHE["value"] = tenant
    return tenant


def _render_run_report_html(workspace: Path, run_id: str) -> str:
    """Serve an eval run's local report.md (or fall back to results.json)
    as a simple, dashboard-styled HTML page.

    Path traversal is guarded by requiring the resolved directory to be a
    direct child of ``.agentops/results/`` and rejecting any name that
    contains separators.
    """
    if not run_id or any(sep in run_id for sep in ("/", "\\", "..")):
        return _run_report_error_html(
            "Invalid run id", f"Run id {run_id!r} is not a valid name."
        )

    results_root = workspace / ".agentops" / "results"
    run_dir = results_root / run_id
    if not run_dir.is_dir():
        return _run_report_error_html(
            "Run not found",
            f"No run directory at .agentops/results/{run_id}.",
        )

    report_md = run_dir / "report.md"
    results_json = run_dir / "results.json"

    body_html = ""
    if report_md.exists():
        try:
            md = report_md.read_text(encoding="utf-8")
            body_html = (
                '<article class="markdown-body">'
                + _render_markdown(md)
                + '</article>'
            )
        except OSError:
            body_html = '<p class="empty">Could not read report.md.</p>'
    elif results_json.exists():
        try:
            data = json.loads(results_json.read_text(encoding="utf-8"))
            body_html = (
                '<p class="empty">No report.md found. Showing raw results.json.</p>'
                '<pre class="report-md">' + _html_escape(json.dumps(data, indent=2)) + '</pre>'
            )
        except (OSError, ValueError):
            body_html = '<p class="empty">Could not read results.json.</p>'
    else:
        body_html = '<p class="empty">No artifacts found for this run.</p>'

    return _RUN_REPORT_TEMPLATE.format(
        title=_html_escape(f"Run · {run_id}"),
        run_id=_html_escape(run_id),
        body=body_html,
        icon_uri=_icon_data_uri(),
    )


def _render_markdown(text: str) -> str:
    """Render markdown to HTML using the ``markdown`` package when
    available; fall back to an escaped <pre> block otherwise.

    Enabled extensions cover the syntax the AgentOps reporter actually
    emits: GitHub-style tables and fenced code blocks.
    """
    text = _normalize_lists_for_markdown(text)
    try:
        import markdown as md_lib  # type: ignore[import-not-found]
    except ImportError:
        return '<pre class="report-md">' + _html_escape(text) + '</pre>'
    try:
        return md_lib.markdown(
            text,
            extensions=["tables", "fenced_code", "sane_lists"],
            output_format="html5",
        )
    except Exception:  # noqa: BLE001
        return '<pre class="report-md">' + _html_escape(text) + '</pre>'


def _normalize_lists_for_markdown(text: str) -> str:
    """Ensure a blank line separates a list from the preceding paragraph.

    Older AgentOps reports emit:

        **Result:** PASS
        - **Target:** ...
        - **Dataset:** ...

    Python-Markdown won't recognise the list without a blank line above
    it and renders the whole thing as one paragraph. We insert a blank
    line whenever a list item directly follows a non-empty, non-list
    line so we don't have to regenerate every saved report.
    """
    lines = text.splitlines()
    out: List[str] = []
    for line in lines:
        stripped = line.lstrip()
        is_list_item = (
            stripped.startswith(("- ", "* ", "+ "))
            or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] in (". ", ") "))
        )
        if is_list_item and out:
            prev = out[-1].rstrip()
            prev_stripped = prev.lstrip()
            prev_is_list = prev_stripped.startswith(("- ", "* ", "+ "))
            if prev and not prev_is_list:
                out.append("")
        out.append(line)
    return "\n".join(out)


def _run_report_error_html(title: str, detail: str) -> str:
    return _RUN_REPORT_TEMPLATE.format(
        title=_html_escape(title),
        run_id="",
        body=f'<p class="empty">{_html_escape(detail)}</p>',
        icon_uri=_icon_data_uri(),
    )


_RUN_REPORT_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title} · AgentOps</title>
<link rel="icon" type="image/png" href="{icon_uri}" />
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; padding: 32px 40px;
    background: #08090b; color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
      "Inter", system-ui, sans-serif;
    font-size: 14px; line-height: 1.55;
  }}
  header {{
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 24px;
    padding-bottom: 16px; border-bottom: 1px solid #1f2228;
  }}
  header img {{ width: 36px; height: 36px; border-radius: 10px; }}
  h1 {{ font-size: 18px; margin: 0; font-weight: 700; }}
  h1 small {{
    color: #94a3b8; font-weight: 500; font-size: 12px;
    margin-left: 8px; font-family: monospace;
  }}
  a.back {{ color: #38bdf8; text-decoration: none; font-size: 12px; }}
  a.back:hover {{ text-decoration: underline; }}
  pre.report-md {{
    background: #161618; border: 1px solid #1f2228; border-radius: 12px;
    padding: 18px 22px; overflow-x: auto;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    font-size: 12.5px; line-height: 1.55;
    white-space: pre-wrap; word-break: break-word;
  }}
  p.empty {{ color: #94a3b8; font-style: italic; }}
  .markdown-body {{
    max-width: 980px; margin: 0 auto;
  }}
  .markdown-body h1, .markdown-body h2, .markdown-body h3,
  .markdown-body h4 {{
    color: #f8fafc; font-weight: 700; letter-spacing: -0.01em;
    margin: 28px 0 12px;
  }}
  .markdown-body h1 {{
    font-size: 22px; padding-bottom: 10px;
    border-bottom: 1px solid #1f2228;
  }}
  .markdown-body h2 {{ font-size: 17px; }}
  .markdown-body h3 {{ font-size: 14px; color: #94a3b8;
    text-transform: uppercase; letter-spacing: 0.06em; }}
  .markdown-body h4 {{ font-size: 13px; color: #cbd5e1; }}
  .markdown-body p {{ margin: 8px 0; }}
  .markdown-body ul, .markdown-body ol {{
    margin: 8px 0; padding-left: 24px;
  }}
  .markdown-body li {{ margin: 4px 0; }}
  .markdown-body strong {{ color: #f8fafc; font-weight: 700; }}
  .markdown-body em {{ color: #cbd5e1; }}
  .markdown-body a {{ color: #38bdf8; text-decoration: none; }}
  .markdown-body a:hover {{ text-decoration: underline; }}
  .markdown-body code {{
    background: rgba(255, 255, 255, 0.06);
    padding: 1px 6px; border-radius: 4px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    font-size: 12.5px; color: #f1f5f9;
  }}
  .markdown-body pre {{
    background: #161618; border: 1px solid #1f2228; border-radius: 10px;
    padding: 14px 18px; overflow-x: auto;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    font-size: 12.5px; line-height: 1.55;
    margin: 12px 0;
  }}
  .markdown-body pre code {{
    background: transparent; padding: 0; border-radius: 0;
    font-size: inherit; color: inherit;
  }}
  .markdown-body table {{
    border-collapse: collapse; margin: 14px 0;
    width: 100%; font-size: 13px;
  }}
  .markdown-body th, .markdown-body td {{
    border: 1px solid #1f2228;
    padding: 8px 12px; text-align: left; vertical-align: top;
  }}
  .markdown-body th {{
    background: #1c1c1f; color: #cbd5e1;
    font-weight: 700; font-size: 11px;
    letter-spacing: 0.04em; text-transform: uppercase;
  }}
  .markdown-body tr:nth-child(even) td {{
    background: rgba(255, 255, 255, 0.02);
  }}
  .markdown-body blockquote {{
    margin: 12px 0; padding: 6px 16px;
    border-left: 3px solid #38bdf8;
    color: #cbd5e1; background: rgba(56, 189, 248, 0.05);
    border-radius: 0 6px 6px 0;
  }}
  .markdown-body hr {{
    border: 0; border-top: 1px solid #1f2228; margin: 24px 0;
  }}
</style>
</head>
<body>
<header>
  <img src="{icon_uri}" alt="AgentOps" />
  <h1>Eval run<small>{run_id}</small></h1>
  <span style="flex:1"></span>
  <a class="back" href="/" onclick="if (window.history.length > 1) {{ window.history.back(); return false; }}">← back to dashboard</a>
</header>
{body}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Telemetry status
# ---------------------------------------------------------------------------


def _telemetry_status() -> Dict[str, Any]:
    """Inspect env + Foundry discovery to tell the user whether eval/watchdog
    traces will reach an App Insights workspace. Pure read; no side effects.
    """
    explicit_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or os.getenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    otlp = os.getenv("AGENTOPS_OTLP_ENDPOINT")
    project = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")

    if explicit_conn:
        return {
            "enabled": True,
            "source": "env",
            "label": "App Insights",
            "detail": "Connected via <code>APPLICATIONINSIGHTS_CONNECTION_STRING</code>.",
            "portal_url": _appinsights_portal_url(explicit_conn),
            "tone": "ok",
        }
    if otlp:
        return {
            "enabled": True,
            "source": "otlp",
            "label": "OTLP exporter",
            "detail": f"<code>AGENTOPS_OTLP_ENDPOINT</code> = {_html_escape(otlp)}",
            "portal_url": None,
            "tone": "ok",
        }
    if project:
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_from_env,
            )
            conn = resolve_appinsights_connection_from_env()
        except Exception:  # noqa: BLE001
            conn = None
        if conn:
            return {
                "enabled": True,
                "source": "discovery",
                "label": "App Insights",
                "detail": "Auto-discovered from the Foundry project endpoint.",
                "portal_url": _appinsights_portal_url(conn),
                "tone": "ok",
            }
        return {
            "enabled": False,
            "source": "discovery_failed",
            "label": "Telemetry off",
            "detail": (
                'In Foundry: <strong>Project details &rarr; Connected '
                "resources &rarr; Add connection &rarr; Application "
                "Insights</strong>. "
                '<a href="https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-setup" '
                'target="_blank" rel="noopener noreferrer">Docs &#x2197;</a>'
            ),
            "portal_url": None,
            "tone": "warn",
        }
    return {
        "enabled": False,
        "source": "off",
        "label": "Telemetry off",
        "detail": (
            'Set <code>AZURE_AI_FOUNDRY_PROJECT_ENDPOINT</code> and '
            "wire App Insights in Foundry (Project details &rarr; "
            "Connected resources). "
            '<a href="https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-setup" '
            'target="_blank" rel="noopener noreferrer">Docs &#x2197;</a>'
        ),
        "portal_url": None,
        "tone": "muted",
    }


def _appinsights_portal_url(connection_string: Optional[str]) -> Optional[str]:
    """Build a deep link to the Logs blade for the App Insights resource
    identified by the connection string. Returns ``None`` when the
    connection string lacks the ``ApplicationId`` segment (older format).
    """
    if not connection_string:
        return None
    m = re.search(r"ApplicationId=([0-9a-fA-F-]+)", connection_string)
    if not m:
        return None
    app_id = m.group(1)
    # ApplicationInsights-Extension landing query inspecting recent AgentOps spans.
    query = (
        "union dependencies, requests, traces"
        "\n| where timestamp > ago(1h)"
        "\n| where name has 'ANALYZE' or name has 'RUN ' or name has 'eval_item' "
        "or name has 'invoke_agent' or name has 'evaluator' or name has 'chat'"
        "\n| order by timestamp desc"
        "\n| take 100"
    )
    # The portal accepts an `appId` query param shortcut.
    return (
        "https://portal.azure.com/#blade/Microsoft_OperationsManagementSuite_Workspace/"
        f"AnalyticsBlade/initiator/AnalyticsShareLinkToQuery/isQueryEditorVisible/true/"
        f"sourceId/%2Fapps%2F{app_id}/scope/%7B%22resources%22%3A%5B%7B%22resourceId%22"
        f"%3A%22%2Fapps%2F{app_id}%22%7D%5D%7D/query/"
        + _url_quote(query)
    )


def _url_quote(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


def _headline_badge_total(series: List[float]) -> Dict[str, str]:
    if not series:
        return {"label": "no data", "tone": "muted"}
    last = series[-1]
    if last == 0:
        return {"label": "all clear", "tone": "ok"}
    if len(series) >= 2 and last > series[-2]:
        return {"label": "trending up", "tone": "warn"}
    return {"label": "open", "tone": "info"}


def _headline_badge_critical(series: List[float]) -> Dict[str, str]:
    if not series:
        return {"label": "no data", "tone": "muted"}
    last = series[-1]
    if last == 0:
        return {"label": "none", "tone": "ok"}
    return {"label": "above zero", "tone": "crit"}


def _latest_run_badge(record: Optional[AnalysisRecord]) -> tuple:
    if record is None:
        return ("never", {"label": "no data", "tone": "muted"})
    label, tone = _BADGE_FOR_SEVERITY[record.max_severity]
    return (
        f"{record.findings_total} finding(s)",
        {"label": label, "tone": tone},
    )


def _latest_run_meta(record: Optional[AnalysisRecord]) -> List[str]:
    if record is None:
        return []
    meta = [record.timestamp]
    if record.duration_seconds is not None:
        meta.append(f"duration: {record.duration_seconds:.1f}s")
    if record.sources_enabled:
        meta.append(f"sources: {', '.join(record.sources_enabled)}")
    return meta


def _badge_runs(count: int) -> str:
    if count >= 10:
        return "well sampled"
    if count >= 3:
        return "moderate sample"
    return "low sample"


def _badge_pass_rate(rate: float) -> Dict[str, str]:
    if rate >= 0.9:
        return {"label": "healthy", "tone": "ok"}
    if rate >= 0.7:
        return {"label": "mixed", "tone": "warn"}
    return {"label": "unhealthy", "tone": "crit"}


def _metric_trend_badge(series: List[float], *, is_latency: bool) -> Dict[str, str]:
    if len(series) < 2:
        return {"label": "baseline", "tone": "info"}
    last, prev = series[-1], series[-2]
    delta = last - prev
    if abs(delta) < 1e-3:
        return {"label": "stable", "tone": "muted"}
    improved = (delta < 0) if is_latency else (delta > 0)
    if improved:
        return {"label": "improved", "tone": "ok"}
    return {"label": "regressed", "tone": "warn"}


# ---------------------------------------------------------------------------
# HTML rendering - inline, zero JS deps
# ---------------------------------------------------------------------------


def _render_card(card: Dict[str, Any], *, hero: bool = False) -> str:
    series = card.get("series", [])
    labels = card.get("labels") or []
    spark = _sparkline_svg(
        series, labels=labels,
        links=card.get("links"),
        alt_links=card.get("alt_links"),
        alt_labels=card.get("alt_labels"),
    )
    badge = card["badge"]
    css_class = "card hero" if hero else "card"
    value = card.get("value", 0)
    unit = card.get("unit", "")
    unit_html = f'<span class="card-unit"> {unit}</span>' if unit else ""

    # Textual values (e.g. "agent-smoke:3") wrap awkwardly when rendered at
    # 36px. Detect and switch to a compact text style.
    value_kind = card.get("value_kind", "numeric")
    if value_kind == "numeric" and isinstance(value, str):
        if any(c.isalpha() and c != "." for c in value):
            value_kind = "text"
    value_css = "card-value card-value-text" if value_kind == "text" else "card-value"

    # value-num span is updated by JS on sparkline hover; data-orig holds the
    # original so leaving the card restores it.
    value_inner = (
        f'<span class="value-num" data-orig="{_html_escape(str(value))}">'
        f"{_html_escape(str(value))}</span>"
    )

    # Cards used to render a visible `card-meta` block under the sparkline
    # (timestamp / duration / execution mode for the "Latest target" and
    # "Latest run" cards). That block grew tall enough to push every card
    # in the same row to match its height. Fold the meta lines into the
    # help tooltip instead so the on-card layout stays uniform.
    meta_lines = [m for m in (card.get("meta") or []) if m]

    # Hover detail shows the sparkline point's timestamp/label when present.
    hover_html = '<div class="hover-detail" data-default="">&nbsp;</div>'

    footer_html = ""
    if card.get("source"):
        footer_html = (
            f'<div class="card-source" title="Data source">'
            f'<span class="source-icon">⌖</span>{_html_escape(card["source"])}</div>'
        )

    help_html = ""
    help_text = card.get("help") or ""
    if meta_lines:
        # Bullet the meta lines so they read as "more facts about this
        # card" rather than running on with the help prose.
        bullets = "\n".join(f"• {m}" for m in meta_lines)
        help_text = f"{help_text}\n\n{bullets}" if help_text else bullets
    if help_text:
        help_html = (
            '<span class="card-help" tabindex="0" aria-label="About this card">'
            '<span class="card-help-icon" aria-hidden="true">i</span>'
            f'<span class="card-help-tooltip" role="tooltip">{_html_escape(help_text)}</span>'
            '</span>'
        )

    return (
        f'<div class="{css_class}">'
        f'{help_html}'
        f'<div class="card-label">{_html_escape(card["label"])}</div>'
        f'<div class="{value_css}">{value_inner}{unit_html}</div>'
        f"{spark}"
        f"{hover_html}"
        f'<div class="badge-row">'
        f'<div class="badge tone-{badge["tone"]}">{_html_escape(badge["label"])}</div>'
        f'</div>'
        f"{footer_html}"
        f"</div>"
    )


def _render_exec_section_tag(execution: Optional[str]) -> str:
    """Render a small inline tag next to a section title indicating the
    execution mode of the latest run (cloud vs local).

    Kept understated - one indicator per section, not per card - so it
    informs without dominating the visual hierarchy.
    """
    if not execution:
        return ""
    if execution == "cloud":
        return (
            '<span class="section-exec-tag tag-cloud" '
            'title="Latest run executed in Foundry cloud">'
            '<span class="section-exec-dot"></span>'
            'Foundry cloud</span>'
        )
    return (
        '<span class="section-exec-tag tag-local" '
        'title="Latest run executed locally">'
        '<span class="section-exec-dot"></span>'
        'Local</span>'
    )


def _html_escape(text: Any) -> str:
    """Minimal HTML attribute/text escaping."""
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_telemetry_card(telemetry: Dict[str, Any]) -> str:
    tone = telemetry["tone"]
    dot = '<span class="dot dot-on"></span>' if telemetry["enabled"] else '<span class="dot dot-off"></span>'

    link_html = ""
    if telemetry.get("portal_url"):
        link_html = (
            f'<a class="card-link" href="{telemetry["portal_url"]}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'View in App Insights →</a>'
        )

    source_html = ""
    src = telemetry.get("source")
    if src:
        src_label = {
            "env": "APPLICATIONINSIGHTS_CONNECTION_STRING",
            "otlp": "AGENTOPS_OTLP_ENDPOINT",
            "discovery": "Foundry project endpoint (auto)",
            "discovery_failed": "discovery failed",
            "off": "no env var set",
        }.get(src, src)
        source_html = (
            f'<div class="card-source"><span class="source-icon">⌖</span>{src_label}</div>'
        )

    return (
        f'<div class="card telemetry">'
        f'<div class="card-label">Telemetry</div>'
        f'<div class="card-value card-value-text tone-{tone}-text">{dot}{telemetry["label"]}</div>'
        f'<div class="telemetry-detail">{telemetry["detail"]}</div>'
        f'<div class="badge tone-{tone}">{"on" if telemetry["enabled"] else "off"}</div>'
        f"{link_html}"
        f"{source_html}"
        f"</div>"
    )


def render_production_grid_html(production: Dict[str, Any]) -> str:
    """Return the inner HTML of the production-telemetry grid only.

    Used by the ``/api/production/html`` endpoint that the dashboard's
    deferred-load JS calls after the page is on screen. Keeps the slow
    App Insights round-trip off the initial render.
    """
    if not production.get("has_data") or not production.get("cards"):
        diagnostics = production.get("diagnostics") or {}
        reason = ""
        if isinstance(diagnostics, dict):
            reason = str(diagnostics.get("reason") or "").strip()
        # When App Insights specifically returned zero invocations, the
        # "no data in window" label is the accurate headline. For any
        # other reason (auth, network, KQL error), the failure word is
        # used so the user knows the empty state is a problem, not a
        # legitimate "nothing to show" result.
        is_zero_invocations = "0 invocations" in reason
        label = (
            "No invocations in the selected window"
            if is_zero_invocations
            else "Production telemetry unavailable"
        )
        if not reason:
            reason = (
                "No invocations found in the selected window. The Foundry "
                "project may not have produced any traces yet."
            )
        return (
            '<div class="card hero loading-card">'
            f'<div class="card-label">{label}</div>'
            '<div class="card-value card-value-text"> - </div>'
            f'<div class="telemetry-detail">{_html_escape(reason)}</div>'
            '</div>'
        )
    return "".join(_render_card(c, hero=True) for c in production["cards"])


def _sparkline_svg(
    series: List[float],
    *,
    labels: Optional[List[str]] = None,
    links: Optional[List[str]] = None,
    alt_links: Optional[List[Optional[str]]] = None,
    alt_labels: Optional[List[Optional[str]]] = None,
) -> str:
    if not series:
        return ""
    window = series[-12:]
    label_window = (labels or [])[-12:]
    link_window = (links or [])[-12:]
    alt_link_window = (alt_links or [])[-12:]
    alt_label_window = (alt_labels or [])[-12:]
    # Align label/link count with the window.
    if len(label_window) < len(window):
        label_window = label_window + [""] * (len(window) - len(label_window))
    if len(link_window) < len(window):
        link_window = link_window + [None] * (len(window) - len(link_window))
    if len(alt_link_window) < len(window):
        alt_link_window = alt_link_window + [None] * (len(window) - len(alt_link_window))
    if len(alt_label_window) < len(window):
        alt_label_window = alt_label_window + [None] * (len(window) - len(alt_label_window))
    if len(window) == 1:
        window = [window[0], window[0]]
        label_window = [label_window[0] if label_window else "", label_window[0] if label_window else ""]
        link_window = [link_window[0] if link_window else None, link_window[0] if link_window else None]
        alt_link_window = [alt_link_window[0] if alt_link_window else None, alt_link_window[0] if alt_link_window else None]
        alt_label_window = [alt_label_window[0] if alt_label_window else None, alt_label_window[0] if alt_label_window else None]
    width = 240
    height = 56
    pad = 4
    max_v = max(window)
    min_v = min(window)
    span = max(max_v - min_v, 1.0)
    step = (width - 2 * pad) / (len(window) - 1) if len(window) > 1 else 0
    points: List[Tuple[float, float]] = []
    for i, v in enumerate(window):
        x = pad + i * step
        y = height - pad - ((v - min_v) / span) * (height - 2 * pad)
        points.append((x, y))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    last_x, last_y = points[-1]
    area_points = (
        " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        + f" {last_x:.1f},{height - pad} {pad:.1f},{height - pad}"
    )

    dots: List[str] = []
    for i, ((x, y), value) in enumerate(zip(points, window)):
        label = _html_escape(label_window[i] if i < len(label_window) else "")
        href = link_window[i] if i < len(link_window) else None
        alt_href = alt_link_window[i] if i < len(alt_link_window) else None
        alt_label = alt_label_window[i] if i < len(alt_label_window) else None
        is_last = "is-last" if i == len(points) - 1 else ""
        is_clickable = "is-clickable" if href else ""
        formatted_value = (
            f"{value:.2f}" if isinstance(value, float) and not value.is_integer()
            else f"{int(value)}"
        )
        alt_attrs = ""
        if alt_href and alt_label:
            alt_attrs = (
                f' data-alt-href="{_html_escape(alt_href)}"'
                f' data-alt-label="{_html_escape(alt_label)}"'
            )
        circle = (
            f'<circle class="dot {is_last} {is_clickable}" cx="{x:.1f}" cy="{y:.1f}" r="3.5" '
            f'fill="currentColor" data-v="{formatted_value}" data-l="{label}"{alt_attrs}>'
            f'<title>{label}{" - " + formatted_value if label else formatted_value}'
            f'{" · click to open" if href else ""}</title>'
            f'</circle>'
        )
        if href:
            new_tab = not href.startswith("/")
            target_attr = ' target="_blank" rel="noopener noreferrer"' if new_tab else ""
            dots.append(
                f'<a class="dot-link" href="{_html_escape(href)}"{target_attr}>{circle}</a>'
            )
        else:
            dots.append(circle)
    dots_svg = "".join(dots)

    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polygon fill="currentColor" fill-opacity="0.08" points="{area_points}"/>'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/>'
        f"{dots_svg}"
        f"</svg>"
    )


_PILLAR_ORDER: List[str] = [
    "quality",
    "performance",
    "reliability",
    "operational_excellence",
    "security",
    "responsible_ai",
]


def _render_findings_list(findings: List[Dict[str, Any]]) -> str:
    """Render findings grouped by WAF-AI pillar, one row per pillar.

    Each pillar renders even when empty (an explicit "clean" indicator
    is a useful status signal). Inside ``operational_excellence`` the
    rows are split into "Workspace & CI Hygiene" and "Spec Conformance"
    sub-sections.
    """

    by_pillar: Dict[str, List[Dict[str, Any]]] = {p: [] for p in _PILLAR_ORDER}
    for f in findings:
        cat = str(f.get("category") or "").strip().lower()
        if cat in by_pillar:
            by_pillar[cat].append(f)

    pillar_rows: List[str] = []
    for pillar in _PILLAR_ORDER:
        bucket = by_pillar[pillar]
        label = _CATEGORY_LABELS.get(pillar, pillar.title())
        sev_counts = {"critical": 0, "warning": 0, "info": 0}
        for f in bucket:
            sev = str(f.get("severity") or "info").lower()
            if sev in sev_counts:
                sev_counts[sev] += 1
        chips = (
            f'<span class="pillar-chip chip-crit">{sev_counts["critical"]} critical</span>'
            f'<span class="pillar-chip chip-warn">{sev_counts["warning"]} warning</span>'
            f'<span class="pillar-chip chip-info">{sev_counts["info"]} info</span>'
        )

        if not bucket:
            body = (
                '<div class="pillar-empty">'
                '<span class="pillar-empty-icon">&#x2713;</span>'
                f'<span>No {label} findings.</span>'
                '</div>'
            )
        elif pillar == "operational_excellence":
            spec = [
                f for f in bucket
                if str(f.get("id") or "").startswith("opex.spec_conformance.")
            ]
            hygiene = [f for f in bucket if f not in spec]
            body = (
                _render_pillar_subgroup("Workspace & CI Hygiene", hygiene)
                + _render_pillar_subgroup("Spec Conformance", spec)
            )
        else:
            body = "".join(_render_finding_card(f) for f in bucket)

        pillar_rows.append(
            '<details class="pillar-row" open>'
            f'<summary class="pillar-summary">'
            f'<span class="pillar-name">{_html_escape(label)}</span>'
            f'<span class="pillar-chips">{chips}</span>'
            '</summary>'
            f'<div class="pillar-body">{body}</div>'
            '</details>'
        )

    return (
        '<div class="section-title sub">Findings by WAF-AI pillar</div>'
        f'<div class="findings-pillars">{"".join(pillar_rows)}</div>'
    )


def _render_pillar_subgroup(label: str, bucket: List[Dict[str, Any]]) -> str:
    """Render a labeled sub-section within a pillar row."""
    if not bucket:
        return (
            f'<div class="pillar-subgroup">'
            f'<div class="pillar-subgroup-title">{_html_escape(label)}</div>'
            '<div class="pillar-empty">'
            '<span class="pillar-empty-icon">&#x2713;</span>'
            f'<span>No {label} findings.</span>'
            '</div>'
            '</div>'
        )
    cards = "".join(_render_finding_card(f) for f in bucket)
    return (
        '<div class="pillar-subgroup">'
        f'<div class="pillar-subgroup-title">{_html_escape(label)}</div>'
        f'{cards}'
        '</div>'
    )


def _render_finding_card(f: Dict[str, Any]) -> str:
    """Render a single finding card (extracted from the old list renderer)."""
    sev = str(f.get("severity") or "info").lower()
    cat = str(f.get("category") or "").strip()
    title = str(f.get("title") or " - ")
    summary = str(f.get("summary") or "").strip()
    rec = str(f.get("recommendation") or "").strip()
    source = str(f.get("source") or "").strip()
    evidence = f.get("evidence") or {}
    sev_tone = {"critical": "crit", "warning": "warn", "info": "info"}.get(sev, "muted")
    cat_label = _CATEGORY_LABELS.get(cat, cat.title() or " - ")

    is_llm = source == "llm_judge"
    ai_badge = (
        '<span class="ai-badge" title="LLM-judged signal (advisory)">AI</span>'
        if is_llm else ""
    )

    rec_html = (
        f'<div class="finding-recommendation"><strong>Fix:</strong> '
        f'{_html_escape(rec)}</div>' if rec else ""
    )
    source_html = (
        f'<div class="finding-source">Source: {_html_escape(source)}</div>'
        if source else ""
    )

    fix_panel = _render_suggested_fix_panel(
        finding_id=str(f.get("id") or ""),
        title=title,
        evidence=evidence if isinstance(evidence, dict) else {},
    )

    return (
        '<div class="finding">'
        f'<div class="finding-row1">'
        f'<span class="badge tone-{sev_tone}">{_html_escape(sev)}</span>'
        f'<span class="finding-cat">{_html_escape(cat_label)}</span>'
        f'{ai_badge}'
        f'<span class="finding-title">{_html_escape(title)}</span>'
        '</div>'
        + (f'<div class="finding-summary">{_html_escape(summary)}</div>' if summary else "")
        + rec_html
        + fix_panel
        + source_html
        + '</div>'
    )


def _render_suggested_fix_panel(
    *, finding_id: str, title: str, evidence: Dict[str, Any]
) -> str:
    """Render a collapsible 'suggested fix' panel for fixable findings.

    The panel is read-only by design - it shows the suggestions the
    judge model (or the deterministic check) proposed. Applying them
    is a separate concern handled outside the dashboard render.
    """
    suggestions = evidence.get("suggestions") if isinstance(evidence, dict) else None
    if not suggestions or not isinstance(suggestions, list):
        return ""
    cleaned = [str(s).strip() for s in suggestions if str(s).strip()]
    if not cleaned:
        return ""

    items = "".join(
        f'<li>{_html_escape(text)}</li>' for text in cleaned[:6]
    )

    return (
        '<details class="finding-fix">'
        '<summary>'
        '<span class="fix-icon">&#x1F4A1;</span> '
        'Suggested fixes ('
        f'{len(cleaned)})'
        '</summary>'
        '<div class="fix-body">'
        f'<ol class="fix-list">{items}</ol>'
        '</div>'
        '</details>'
    )


def _icon_data_uri() -> str:
    """Read the bundled icon.png and return a base64 data URI.

    Falls back to a tiny inline SVG glyph when the asset is missing
    (older installs) so the dashboard still renders.
    """
    try:
        data = _pkg_files("agentops.templates").joinpath("icon.png").read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:  # noqa: BLE001
        # Fallback SVG dot.
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#38bdf8">'
            '<circle cx="12" cy="12" r="10"/></svg>'
        )
        return "data:image/svg+xml;utf8," + svg


def _foundry_logo_data_uri() -> Optional[str]:
    """Read the bundled foundry.svg and return a base64 data URI.

    Returns ``None`` when the asset is missing (older installs) so the
    powered-by badge can be skipped gracefully.
    """
    try:
        data = _pkg_files("agentops.templates").joinpath("foundry.svg").read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:  # noqa: BLE001
        return None


def _collapsible_section(title_inner_html: str, body_html: str) -> str:
    """Wrap a dashboard section in a collapsible ``<details>`` block.

    All sections are expanded by default so the dashboard reads the same
    on first load; the chevron in the summary lets users hide noisy
    sections (e.g. production telemetry on a stale workspace).
    """
    return (
        '<details class="section-block" open>'
        '<summary class="section-summary">'
        '<span class="section-chevron" aria-hidden="true">&#x25BE;</span>'
        f'<span class="section-title-text">{title_inner_html}</span>'
        '</summary>'
        f'<div class="section-body">{body_html}</div>'
        '</details>'
    )


def _render_loading_shell() -> str:
    """Tiny self-contained HTML shell shown while the full dashboard
    is being built server-side.

    The shell renders **instantly** (no file IO, no subprocesses) so
    the user sees a branded loading state immediately instead of a
    black page. A small inline script preserves the query string and
    fetches ``/?_partial=1`` to hydrate the real dashboard. The page
    falls back to a plain link for clients with JavaScript disabled.
    """
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentOps Dashboard - Loading...</title>
<style>
  :root {
    --bg: #0b0e14;
    --card: #11151c;
    --border: #1f2630;
    --text: #e6ebf2;
    --text-dim: #9aa3b2;
    --accent: #38bdf8;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", sans-serif; }
  .shell {
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }
  .loader-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 36px 44px;
    text-align: center;
    max-width: 420px;
  }
  .loader-brand {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--accent);
    font-weight: 700;
    margin-bottom: 18px;
  }
  .loader-title {
    font-size: 22px;
    font-weight: 600;
    margin: 0 0 10px 0;
  }
  .loader-subtitle {
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.55;
    margin: 0 0 24px 0;
  }
  .loader-spinner {
    width: 56px; height: 56px;
    margin: 0 auto;
    border-radius: 50%;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loader-dots {
    margin-top: 22px;
    display: flex; justify-content: center; gap: 6px;
  }
  .loader-dots span {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--text-dim);
    opacity: 0.4;
    animation: pulse 1.4s ease-in-out infinite;
  }
  .loader-dots span:nth-child(2) { animation-delay: 0.18s; }
  .loader-dots span:nth-child(3) { animation-delay: 0.36s; }
  @keyframes pulse {
    0%, 80%, 100% { opacity: 0.25; transform: scale(0.85); }
    40% { opacity: 1; transform: scale(1.0); }
  }
  .loader-fallback {
    margin-top: 22px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .loader-fallback a { color: var(--accent); }
</style>
</head>
<body>
<div class="shell">
  <div class="loader-card" role="status" aria-live="polite">
    <div class="loader-brand">AgentOps</div>
    <h1 class="loader-title">Loading dashboard</h1>
    <p class="loader-subtitle">
      Reading eval history, scanning CI/CD runs, and waking up the
      doctor agent. This takes a few seconds on first load.
    </p>
    <div class="loader-spinner" aria-hidden="true"></div>
    <div class="loader-dots" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>
    <noscript>
      <p class="loader-fallback">
        JavaScript is disabled.
        <a id="noscript-link" href="?_partial=1">Open the dashboard manually</a>.
      </p>
    </noscript>
  </div>
</div>
<script>
(function() {
  // Preserve the query string (range/from/to) and add _partial=1 so
  // the server returns the real dashboard. We replace document.open()
  // / write() / close() rather than redirecting so the user does not
  // see a flash of empty page.
  var params = new URLSearchParams(window.location.search || '');
  params.set('_partial', '1');
  var url = window.location.pathname + '?' + params.toString();
  // Update the <noscript> fallback link too, just in case.
  var fallback = document.getElementById('noscript-link');
  if (fallback) fallback.setAttribute('href', url);
  fetch(url, {credentials: 'same-origin'})
    .then(function(r) { return r.ok ? r.text() : Promise.reject(r.status); })
    .then(function(html) {
      document.open();
      document.write(html);
      document.close();
    })
    .catch(function(err) {
      var card = document.querySelector('.loader-card');
      if (!card) return;
      card.innerHTML =
        '<div class="loader-brand">AgentOps</div>' +
        '<h1 class="loader-title">Dashboard failed to load</h1>' +
        '<p class="loader-subtitle">Server returned an error (' +
        String(err) + '). Try reloading the page or run ' +
        '<code>agentops dashboard</code> again.</p>';
    });
})();
</script>
</body>
</html>
"""


def render_dashboard_html(payload: Dict[str, Any]) -> str:
    """Render the dashboard from a payload built by
    :func:`build_dashboard_payload`. Returns a complete HTML document.
    """
    telemetry = payload["telemetry"]
    # Show the telemetry card only when telemetry is OFF - it then acts as
    # the "why is the production section empty" hint. When telemetry is
    # active, the dedicated Production telemetry section communicates the
    # connection state already.
    show_telemetry_card = not telemetry.get("enabled", False)
    telemetry_card = _render_telemetry_card(telemetry) if show_telemetry_card else ""

    eval_section = ""
    if payload["eval"]["has_runs"]:
        cards_html = "".join(_render_card(c) for c in payload["eval"]["cards"])
        exec_tag = _render_exec_section_tag(
            payload["eval"].get("latest_execution"),
        )
        eval_body = f'<div class="grid">{cards_html}{telemetry_card}</div>'
        eval_section = _collapsible_section(
            f"Evaluation runs{exec_tag}", eval_body,
        )
    else:
        eval_body = (
            '<div class="empty-state">'
            "No eval runs yet under <code>.agentops/results/</code>. "
            "Run <code>agentops eval run</code> to populate this section."
            "</div>"
            + (f'<div class="grid">{telemetry_card}</div>' if telemetry_card else "")
        )
        eval_section = _collapsible_section("Evaluation runs", eval_body)

    deployments = payload.get("deployments") or {}
    if deployments.get("has_data") and deployments.get("cards"):
        deploy_cards = "".join(_render_card(c) for c in deployments["cards"])
        deployments_body = f'<div class="grid">{deploy_cards}</div>'
    else:
        hint = deployments.get("hint") or (
            "Install the GitHub CLI and run <code>gh auth login</code> "
            "to surface workflow runs here."
        )
        deployments_body = f'<div class="empty-state">{hint}</div>'
    deployments_section = _collapsible_section("CI/CD Pipelines", deployments_body)

    metrics_section = ""
    if payload["metrics"]:
        metrics_html = "".join(_render_card(c) for c in payload["metrics"])
        exec_tag = _render_exec_section_tag(
            payload["eval"].get("latest_execution") if payload["eval"].get("has_runs") else None,
        )
        metrics_body = f'<div class="grid">{metrics_html}</div>'
        metrics_section = _collapsible_section(
            f"Quality metrics{exec_tag}", metrics_body,
        )

    watchdog = payload["watchdog"]
    compliance_url = payload.get("foundry_compliance_url")
    compliance_link = ""
    if compliance_url:
        # The link opens Foundry's *native* Operate → Compliance view,
        # which surfaces runtime guardrails (content filters, RAI,
        # network posture) — NOT the doctor's findings. Make the label
        # explicit so users do not expect to see the same findings
        # echoed in Foundry.
        compliance_link = (
            f' <a class="section-link" href="{_html_escape(compliance_url)}" '
            'target="_blank" rel="noopener noreferrer" '
            'title="Opens Foundry\u2019s Operate \u2192 Compliance page '
            '(runtime guardrails). The doctor findings shown here are '
            'AgentOps-only and are not echoed in Foundry.">'
            'Open Foundry runtime compliance &#x2197;</a>'
        )
    watchdog_title = f"Doctor findings{compliance_link}"

    maturity_block = ""
    maturity = watchdog.get("maturity") if isinstance(watchdog, dict) else None
    if maturity:
        next_gap = maturity.get("next_gap")
        gap_html = (
            f' &mdash; next gap: <code>{_html_escape(str(next_gap))}</code>'
            if next_gap and next_gap != "no_history"
            else ""
        )
        maturity_block = (
            '<div class="maturity-badge" '
            'title="GenAIOps Maturity Model (Microsoft)">'
            f'<strong>Maturity: L{int(maturity.get("level", 0))} '
            f'&mdash; {_html_escape(str(maturity.get("label", "")))}</strong>'
            f'{gap_html}'
            '</div>'
        )

    if watchdog["has_history"]:
        watchdog_headline = "".join(
            _render_card(c, hero=True) for c in watchdog["headline_cards"]
        )
        findings_list = _render_findings_list(watchdog.get("latest_findings") or [])
        watchdog_body = (
            f'{maturity_block}'
            f'<div class="grid">{watchdog_headline}</div>'
            f'{findings_list}'
        )
    else:
        watchdog_body = (
            '<div class="empty-state">'
            "No analysis history yet. Run "
            "<code>agentops doctor</code> to populate this section."
            "</div>"
        )
    watchdog_section = _collapsible_section(watchdog_title, watchdog_body)

    production = payload.get("production") or {}
    production_section = ""
    portal_link = ""
    portal_url = telemetry.get("portal_url") if isinstance(telemetry, dict) else None
    if portal_url:
        portal_link = (
            f' <a class="section-link" href="{_html_escape(portal_url)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'View in App Insights →</a>'
        )

    prod_title = (
        'Production telemetry'
        ' <span class="live-pill">live · App Insights</span>'
        f'{portal_link}'
    )
    if production.get("has_data") and production.get("cards"):
        # Server-side render (rare - happens when /api/production/html is
        # invoked directly without a deferred placeholder).
        prod_html = "".join(_render_card(c, hero=True) for c in production["cards"])
        prod_body = f'<div class="grid" id="production-grid">{prod_html}</div>'
        production_section = _collapsible_section(prod_title, prod_body)
    elif production.get("deferred"):
        # Telemetry is wired up; the cards will arrive async from
        # /api/production/html so the page can render immediately.
        # Render 4 skeleton cards matching the eventual grid layout
        # (Invocations / Error rate / P95 latency / Tokens) so the
        # page does not visually re-flow when the fetch lands.
        skeleton_labels = ("Invocations", "Error rate", "P95 latency", "Tokens")
        skeleton_cards = "".join(
            (
                '<div class="card hero loading-card skeleton-card">'
                f'<div class="card-label">{label}</div>'
                '<div class="card-value skeleton-bar skeleton-bar-value"></div>'
                '<div class="skeleton-bar skeleton-bar-spark"></div>'
                '<div class="skeleton-bar skeleton-bar-detail"></div>'
                '</div>'
            )
            for label in skeleton_labels
        )
        prod_body = f'<div class="grid" id="production-grid">{skeleton_cards}</div>'
        production_section = _collapsible_section(prod_title, prod_body)

    counts = payload["summary_counts"]
    workspace_display = _shorten_workspace(payload["workspace"])
    range_info = payload.get("time_range") or {}
    range_bar = _render_range_bar(range_info)

    foundry_uri = _foundry_logo_data_uri()
    foundry_url = payload.get("foundry_project_url") or "https://ai.azure.com"
    if foundry_uri:
        powered_by_html = (
            f'<a class="powered-by" href="{_html_escape(foundry_url)}" '
            'target="_blank" rel="noopener noreferrer" '
            'title="Open this project in Microsoft Foundry">'
            f'<img src="{foundry_uri}" alt="Foundry" />'
            '<span>Your Foundry project &#x2197;</span>'
            '</a>'
        )
    else:
        powered_by_html = ""

    # Banner removed; the primer click is now folded into the project
    # action button above, which carries the ?tid= hint.
    setup_banner_html = ""

    return _DASHBOARD_TEMPLATE.format(
        eval_section=eval_section,
        deployments_section=deployments_section,
        metrics_section=metrics_section,
        production_section=production_section,
        watchdog_section=watchdog_section,
        eval_runs=counts["eval_runs"],
        analyses=counts["analyses"],
        workspace_display=workspace_display,
        workspace=payload["workspace"],
        icon_uri=_icon_data_uri(),
        powered_by=powered_by_html,
        setup_banner=setup_banner_html,
        range_bar=range_bar,
        range_label=_html_escape(range_info.get("label", "")),
    )


def _render_range_bar(range_info: Dict[str, Any]) -> str:
    """Render the 1D / 7D / 30D / Custom selector."""
    active_key = range_info.get("key", "7d")
    pills: List[str] = []
    labels = {"1d": "1D", "7d": "7D", "30d": "30D"}
    for key in preset_keys():
        cls = "range-pill active" if key == active_key else "range-pill"
        pills.append(f'<a class="{cls}" href="?range={key}">{labels[key]}</a>')
    custom_cls = "range-pill active" if active_key == "custom" else "range-pill"
    pills.append(
        f'<a class="{custom_cls}" href="#" onclick="document.getElementById(\'rangeCustomForm\').classList.toggle(\'open\'); return false;">Custom</a>'
    )

    today = _today_iso()
    week_ago = _days_ago_iso(7)
    custom_from = range_info.get("start", "")[:10] if active_key == "custom" else week_ago
    custom_to = range_info.get("end", "")[:10] if active_key == "custom" else today
    form_class = "range-custom-form open" if active_key == "custom" else "range-custom-form"

    custom_form = (
        f'<form id="rangeCustomForm" class="{form_class}" method="get">'
        f'<input type="hidden" name="range" value="custom" />'
        f'<label>From <input type="date" name="from" value="{custom_from}" max="{today}" /></label>'
        f'<label>To <input type="date" name="to" value="{custom_to}" max="{today}" /></label>'
        f'<button type="submit">Apply</button>'
        f'</form>'
    )

    refresh_control = (
        '<div class="refresh-control" title="How often the dashboard reloads">'
        '<svg class="refresh-icon" viewBox="0 0 16 16" width="12" height="12" '
        'fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M13.5 8a5.5 5.5 0 1 1-1.61-3.89" />'
        '<polyline points="13.5,2 13.5,5 10.5,5" />'
        '</svg>'
        '<span class="refresh-label">Refresh</span>'
        '<select id="refreshSelect" aria-label="Refresh period">'
        '<option value="0">Off</option>'
        '<option value="60000">1 min</option>'
        '<option value="300000" selected>5 min</option>'
        '<option value="900000">15 min</option>'
        '<option value="1800000">30 min</option>'
        '<option value="3600000">1 hour</option>'
        '</select>'
        '</div>'
    )

    return (
        '<div class="range-bar">'
        + '<div class="range-pills">' + "".join(pills) + '</div>'
        + custom_form
        + refresh_control
        + '</div>'
    )


def _today_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _shorten_workspace(path: str) -> str:
    """Show only the current folder name for compact heading display.

    Using the last two segments was risky because the parent folder is
    not always meaningful (e.g. ``Desktop\\agent-x``, ``tmp\\agent-x``).
    The full path is still kept in the title attribute for context.
    """
    name = Path(path).name
    return name or path


_DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AgentOps Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="icon" type="image/png" href="{icon_uri}" />
<style>
  :root {{
    --bg: #08090b;
    --bg-grad: radial-gradient(1200px 600px at 80% -10%, rgba(56, 189, 248, 0.06), transparent 60%);
    --card: #161618;
    --card-hi: #1c1c1f;
    --border: rgba(255, 255, 255, 0.06);
    --border-strong: rgba(255, 255, 255, 0.12);
    --text: #fafafa;
    --text-dim: #a1a1aa;
    --text-faint: #71717a;
    --ok: #4ade80;
    --info: #38bdf8;
    --warn: #fbbf24;
    --crit: #f87171;
    --muted: #71717a;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); }}
  body {{
    padding: 28px 32px 48px;
    background: var(--bg) var(--bg-grad) no-repeat;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
    max-width: 1400px; margin: 0 auto;
  }}
  header {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 28px; padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }}
  header .brand {{ display: flex; align-items: center; gap: 14px; }}
  header .brand img {{
    width: 40px; height: 40px; border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }}
  header h1 {{
    margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
  }}
  header .subtitle {{
    color: var(--text-dim); font-size: 12px; font-weight: 500;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 2px;
  }}
  header .stats {{
    display: flex; flex-direction: column; align-items: flex-end; gap: 10px;
    color: var(--text-dim); font-size: 12px; font-weight: 500;
  }}
  header .stats-counts {{
    display: flex; align-items: center; gap: 18px;
  }}
  header .stat-num {{ color: var(--text); font-size: 18px; font-weight: 600; }}
  header .powered-by {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 5px 12px 5px 9px; border-radius: 999px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 12px; font-weight: 600;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease,
                color 0.15s ease;
  }}
  header .powered-by:hover {{
    background: rgba(56, 189, 248, 0.10);
    color: var(--text);
    border-color: rgba(56, 189, 248, 0.45);
  }}
  header .powered-by img {{
    height: 14px; width: auto; display: block;
  }}
  .section-title {{
    margin: 32px 0 14px; font-size: 11px; font-weight: 700;
    color: var(--text-faint); letter-spacing: 0.12em;
    text-transform: uppercase;
  }}
  .section-title.sub {{
    margin-top: 18px; font-size: 11px;
  }}
  /* Collapsible section wrapper: <details><summary>title</summary>body</details>. */
  .section-block {{
    margin: 32px 0 0;
  }}
  .section-block + .section-block {{
    margin-top: 24px;
  }}
  .section-block > summary.section-summary {{
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    user-select: none;
    color: var(--text-faint);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    margin-bottom: 14px;
    transition: color 120ms ease;
  }}
  .section-block > summary.section-summary::-webkit-details-marker {{
    display: none;
  }}
  .section-block > summary.section-summary:hover {{
    color: var(--text-dim);
  }}
  .section-chevron {{
    display: inline-block;
    color: var(--text-faint);
    font-size: 14px;
    line-height: 1;
    transition: transform 150ms ease;
  }}
  .section-block:not([open]) > summary.section-summary .section-chevron {{
    transform: rotate(-90deg);
  }}
  .section-title-text {{
    /* Reuse existing inline elements (exec tag, live pill, section link)
       inside the title without forcing them to inherit summary casing. */
    text-transform: uppercase;
  }}
  .section-title-text .section-exec-tag,
  .section-title-text .live-pill,
  .section-title-text .section-link {{
    text-transform: none;
    letter-spacing: 0;
  }}
  .section-body {{
    /* Body is just the cards grid + any sub-titles; no extra padding. */
  }}
  .live-pill {{
    display: inline-block; margin-left: 8px;
    padding: 2px 8px; border-radius: 999px;
    background: rgba(74, 222, 128, 0.12); color: var(--ok);
    font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
    text-transform: uppercase; vertical-align: middle;
    animation: live-pulse 2s ease-in-out infinite;
  }}
  .maturity-badge {{
    margin: 0 0 14px;
    padding: 8px 12px;
    border-radius: 8px;
    background: rgba(96, 165, 250, 0.10);
    border: 1px solid rgba(96, 165, 250, 0.35);
    color: var(--fg);
    font-size: 13px;
  }}
  .maturity-badge strong {{
    margin-right: 6px;
  }}
  .maturity-badge code {{
    background: rgba(0, 0, 0, 0.25);
    padding: 1px 6px; border-radius: 4px;
    font-size: 12px;
  }}
  .ai-badge {{
    display: inline-block;
    margin-right: 6px;
    padding: 1px 7px;
    border-radius: 4px;
    background: rgba(168, 85, 247, 0.18);
    color: #c4b5fd;
    border: 1px solid rgba(168, 85, 247, 0.45);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    vertical-align: middle;
  }}
  .finding-fix {{
    margin-top: 8px;
    border: 1px solid rgba(168, 85, 247, 0.30);
    background: rgba(168, 85, 247, 0.08);
    border-radius: 6px;
    padding: 6px 10px;
  }}
  .finding-fix > summary {{
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
    list-style: none;
  }}
  .finding-fix > summary::-webkit-details-marker {{ display: none; }}
  .finding-fix .fix-icon {{ margin-right: 4px; }}
  .finding-fix .fix-body {{
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px dashed rgba(168, 85, 247, 0.30);
  }}
  .finding-fix .fix-list {{
    margin: 0 0 8px 18px;
    padding: 0;
    font-size: 13px;
  }}
  .finding-fix .fix-list li {{ margin: 4px 0; }}
  .finding-fix .fix-copilot-link {{
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    background: rgba(168, 85, 247, 0.22);
    color: #c4b5fd;
    text-decoration: none;
    font-size: 12px;
    font-weight: 600;
  }}
  .finding-fix .fix-copilot-link:hover {{
    background: rgba(168, 85, 247, 0.35);
  }}
  .loading-card {{
    opacity: 0.85; border-style: dashed;
  }}
  .loading-card .card-value {{
    font-size: 28px;
  }}
  .skeleton-bar {{
    display: block; border-radius: 6px;
    background: linear-gradient(
      90deg,
      rgba(255, 255, 255, 0.04) 0%,
      rgba(255, 255, 255, 0.10) 50%,
      rgba(255, 255, 255, 0.04) 100%
    );
    background-size: 200% 100%;
    animation: shimmer 1.4s ease-in-out infinite;
  }}
  .skeleton-bar-value {{ height: 30px; width: 60%; margin: 8px 0 12px; }}
  .skeleton-bar-spark  {{ height: 36px; width: 100%; margin-bottom: 10px; }}
  .skeleton-bar-detail {{ height: 12px; width: 80%; }}
  @keyframes shimmer {{
    0%   {{ background-position: 200% 0; }}
    100% {{ background-position: -200% 0; }}
  }}
  .section-link {{
    margin-left: 12px; color: var(--info); text-decoration: none;
    font-size: 12px; font-weight: 600; vertical-align: middle;
    text-transform: none; letter-spacing: 0;
  }}
  .section-link:hover {{ text-decoration: underline; }}
  @keyframes live-pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.6; }}
  }}
  .range-bar {{
    display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
    margin-bottom: 8px;
  }}
  .range-pills {{ display: flex; gap: 4px; }}
  .range-pill {{
    padding: 6px 14px; border-radius: 999px;
    color: var(--text-dim); text-decoration: none;
    font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
    transition: background 0.15s ease, color 0.15s ease;
  }}
  .range-pill:hover {{
    background: rgba(255, 255, 255, 0.06); color: var(--text);
  }}
  .range-pill.active {{
    background: rgba(56, 189, 248, 0.14); color: var(--info);
    border-color: rgba(56, 189, 248, 0.35);
  }}
  .range-custom-form {{
    display: none; gap: 10px; align-items: center;
    background: var(--card); padding: 10px 14px;
    border-radius: 999px; border: 1px solid var(--border);
    font-size: 12px; color: var(--text-dim);
  }}
  .range-custom-form.open {{ display: flex; }}
  .range-custom-form input[type="date"] {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border); color: var(--text);
    padding: 4px 8px; border-radius: 8px; font-size: 12px;
    color-scheme: dark;
  }}
  .range-custom-form button {{
    background: var(--info); color: #08090b; border: 0;
    padding: 5px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 700; cursor: pointer;
  }}
  .range-current {{
    color: var(--text-faint); font-size: 11px; font-weight: 500;
    margin-left: auto;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .refresh-control {{
    margin-left: auto;
    display: inline-flex; align-items: center; gap: 8px;
    padding: 5px 10px 5px 12px; border-radius: 999px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
    transition: background 0.15s ease, color 0.15s ease,
                border-color 0.15s ease;
  }}
  .refresh-control:hover {{
    background: rgba(255, 255, 255, 0.06); color: var(--text);
    border-color: rgba(56, 189, 248, 0.35);
  }}
  .refresh-control .refresh-icon {{
    color: var(--info); opacity: 0.9;
  }}
  .refresh-control.spinning .refresh-icon {{
    animation: refresh-spin 0.8s linear;
  }}
  .refresh-label {{
    color: var(--text-faint); text-transform: uppercase;
    font-size: 10px; letter-spacing: 0.06em;
  }}
  #refreshSelect {{
    appearance: none; -webkit-appearance: none;
    background: transparent; color: var(--text);
    border: 0; outline: none;
    font: inherit; font-weight: 600;
    padding: 0 18px 0 2px; cursor: pointer;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10' fill='none' stroke='%2394a3b8' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='2,4 5,7 8,4'/></svg>");
    background-repeat: no-repeat;
    background-position: right 2px center;
  }}
  #refreshSelect option {{
    background: var(--card); color: var(--text);
  }}
  @keyframes refresh-spin {{
    from {{ transform: rotate(0deg); }}
    to   {{ transform: rotate(360deg); }}
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px 20px;
    display: flex; flex-direction: column; gap: 6px;
    color: var(--text);
    transition: border-color 0.15s ease, transform 0.15s ease;
    position: relative; overflow: hidden;
  }}
  .card:hover {{ border-color: var(--border-strong); }}
  .card-help {{
    position: absolute; top: 12px; right: 14px;
    width: 16px; height: 16px;
    display: inline-flex; align-items: center; justify-content: center;
    cursor: help; z-index: 3;
  }}
  .card-help-icon {{
    width: 16px; height: 16px; border-radius: 50%;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid var(--border);
    color: var(--text-faint);
    font-size: 10px; font-weight: 700; font-style: italic;
    font-family: Georgia, "Times New Roman", serif;
    display: inline-flex; align-items: center; justify-content: center;
    line-height: 1; user-select: none;
    transition: background 0.15s ease, color 0.15s ease,
                border-color 0.15s ease;
  }}
  .card-help:hover .card-help-icon,
  .card-help:focus-within .card-help-icon {{
    background: rgba(56, 189, 248, 0.18);
    color: var(--info);
    border-color: rgba(56, 189, 248, 0.45);
  }}
  .card-help-tooltip {{
    position: absolute; top: 26px; right: -2px;
    width: max-content; max-width: 260px;
    padding: 10px 12px;
    background: #0d0e10; color: var(--text);
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    font-size: 11.5px; line-height: 1.55; font-weight: 500;
    letter-spacing: 0.01em;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.55);
    opacity: 0; visibility: hidden;
    transform: translateY(-4px);
    transition: opacity 0.12s ease, transform 0.12s ease,
                visibility 0s linear 0.12s;
    pointer-events: none;
    text-transform: none;
    white-space: pre-line;
    text-align: left;
  }}
  .card-help-tooltip::before {{
    content: "";
    position: absolute; top: -5px; right: 6px;
    width: 8px; height: 8px;
    background: #0d0e10;
    border-left: 1px solid var(--border-strong);
    border-top: 1px solid var(--border-strong);
    transform: rotate(45deg);
  }}
  .card-help:hover .card-help-tooltip,
  .card-help:focus-within .card-help-tooltip {{
    opacity: 1; visibility: visible; transform: translateY(0);
    transition: opacity 0.12s ease, transform 0.12s ease,
                visibility 0s linear 0s;
  }}
  .card.hero {{
    background: linear-gradient(165deg, var(--card-hi) 0%, var(--card) 100%);
  }}
  .card.telemetry {{
    border-style: dashed; border-color: var(--border-strong);
    background: linear-gradient(165deg, #181a1f 0%, var(--card) 100%);
  }}
  .card-label {{
    color: var(--text-dim); font-size: 12px; font-weight: 600;
    letter-spacing: 0.02em;
  }}
  .card-value {{
    font-size: 36px; font-weight: 700; line-height: 1.05;
    margin: 2px 0 0; letter-spacing: -0.02em;
  }}
  .card-value-text {{
    font-size: 20px; line-height: 1.25; font-weight: 600;
    word-break: break-word;
  }}
  .card-unit {{
    color: var(--text-dim); font-size: 13px; font-weight: 500;
    margin-left: 5px;
  }}
  .card-meta {{
    display: flex; flex-direction: column; gap: 2px;
    color: var(--text-faint); font-size: 11px; margin-top: 4px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .telemetry-detail {{
    color: var(--text-dim); font-size: 12px; line-height: 1.45;
    overflow-wrap: anywhere; word-break: break-word;
  }}
  .telemetry-detail code {{
    font-size: 11px; padding: 1px 4px; border-radius: 4px;
    background: rgba(255, 255, 255, 0.05);
    color: var(--text); font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .telemetry-detail a {{
    color: var(--info); text-decoration: none; font-weight: 600;
  }}
  .telemetry-detail a:hover {{ text-decoration: underline; }}
  .telemetry-detail strong {{ color: var(--text); font-weight: 600; }}
  .card-link {{
    color: var(--info); text-decoration: none; font-size: 12px;
    font-weight: 600; margin-top: 4px;
  }}
  .card-link:hover {{ text-decoration: underline; }}
  .card-source {{
    color: var(--text-faint); font-size: 10.5px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 6px; padding-top: 10px;
    border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 6px;
  }}
  .source-icon {{ opacity: 0.6; }}
  .sparkline {{
    width: 100%; height: 56px; color: var(--info); margin-top: 6px;
    cursor: crosshair;
  }}
  .sparkline .dot {{
    opacity: 0; transition: opacity 0.12s ease, r 0.12s ease;
  }}
  .sparkline:hover .dot {{ opacity: 0.55; }}
  .sparkline .dot.is-last {{ opacity: 1; }}
  .sparkline .dot:hover {{ opacity: 1; r: 5; }}
  .sparkline .dot.is-clickable {{ cursor: pointer; }}
  .sparkline a.dot-link {{ cursor: pointer; }}
  .sparkline a.dot-link:hover .dot {{
    opacity: 1; r: 5.5; filter: drop-shadow(0 0 4px currentColor);
  }}
  .card.hero .sparkline {{ color: var(--info); }}
  .hover-detail {{
    color: var(--text-dim); font-size: 11px; font-weight: 500;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 6px; min-height: 14px;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  }}
  .hover-detail.active {{ color: var(--text); }}
  .hover-detail .hover-label {{ color: inherit; }}
  .hover-detail .hover-alt-pill {{
    display: inline-flex; align-items: center;
    padding: 1px 8px; border-radius: 999px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
      "Inter", system-ui, sans-serif;
    font-size: 10.5px; font-weight: 600; letter-spacing: 0.02em;
    color: var(--info); text-decoration: none;
    background: rgba(56, 189, 248, 0.10);
    border: 1px solid rgba(56, 189, 248, 0.28);
    pointer-events: auto;
    transition: background 0.12s ease, color 0.12s ease,
                border-color 0.12s ease;
  }}
  .hover-detail .hover-alt-pill:hover {{
    background: rgba(56, 189, 248, 0.22);
    color: var(--text);
    border-color: rgba(56, 189, 248, 0.55);
  }}
  .dot {{
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    margin-right: 8px; vertical-align: middle;
  }}
  .dot-on  {{ background: var(--ok); box-shadow: 0 0 8px rgba(74, 222, 128, 0.6); }}
  .dot-off {{ background: var(--muted); }}
  .badge-row {{
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin-top: 4px;
  }}
  .badge {{
    display: inline-flex; align-self: flex-start; align-items: center;
    padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
    text-transform: lowercase; letter-spacing: 0.01em;
  }}
  .section-exec-tag {{
    display: inline-flex; align-items: center; gap: 6px;
    margin-left: 10px; padding: 2px 9px 2px 8px;
    border-radius: 999px;
    font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
    text-transform: none;
    vertical-align: middle;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
      "Inter", system-ui, sans-serif;
  }}
  .section-exec-tag.tag-cloud {{
    color: var(--info);
    background: rgba(56, 189, 248, 0.10);
    border: 1px solid rgba(56, 189, 248, 0.25);
  }}
  .section-exec-tag.tag-local {{
    color: var(--text-faint);
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
  }}
  .section-exec-dot {{
    width: 5px; height: 5px; border-radius: 50%;
    background: currentColor;
  }}
  }}
  .tone-ok    {{ background: rgba(74, 222, 128, 0.12); color: var(--ok); }}
  .tone-info  {{ background: rgba(56, 189, 248, 0.12); color: var(--info); }}
  .tone-warn  {{ background: rgba(251, 191, 36, 0.13); color: var(--warn); }}
  .tone-crit  {{ background: rgba(248, 113, 113, 0.13); color: var(--crit); }}
  .tone-muted {{ background: rgba(113, 113, 122, 0.18); color: var(--text-dim); }}
  .tone-ok-text    {{ color: var(--ok); }}
  .tone-warn-text  {{ color: var(--warn); }}
  .tone-crit-text  {{ color: var(--crit); }}
  .tone-info-text  {{ color: var(--info); }}
  .tone-muted-text {{ color: var(--text-dim); }}
  .empty-state {{
    background: var(--card); border: 1px dashed var(--border-strong);
    border-radius: 16px; padding: 22px;
    color: var(--text-dim); margin-bottom: 14px; font-size: 13px;
  }}
  .empty-state code {{
    background: rgba(255, 255, 255, 0.04); padding: 2px 7px; border-radius: 6px;
    color: var(--text); font-size: 12px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  /* Findings list (watchdog section). One stacked card per finding,
     sorted by severity. Replaces the per-category trend mini-charts. */
  .findings-list {{
    display: flex; flex-direction: column; gap: 10px;
    margin-top: 8px;
  }}
  .finding {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    font-size: 13px;
    color: var(--text-dim);
  }}
  .finding-row1 {{
    display: flex; align-items: center; gap: 10px;
    flex-wrap: wrap;
  }}
  .finding-cat {{
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--text-faint);
    font-weight: 600;
  }}
  .finding-title {{
    color: var(--text);
    font-weight: 600;
    font-size: 13px;
    flex: 1 1 auto;
    min-width: 0;
  }}
  .finding-summary {{
    margin-top: 6px;
    line-height: 1.5;
  }}
  .finding-recommendation {{
    margin-top: 6px;
    line-height: 1.5;
    color: var(--text);
  }}
  .finding-recommendation strong {{ color: var(--info); }}
  .finding-source {{
    margin-top: 6px;
    font-size: 11px;
    color: var(--text-faint);
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .findings-empty {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    display: flex; align-items: center; gap: 12px;
    color: var(--text-dim);
    margin-top: 8px;
  }}
  .findings-empty-icon {{
    font-size: 18px;
    color: var(--ok);
    font-weight: 700;
  }}
  /* Pillar rows (one row per WAF-AI pillar). */
  .findings-pillars {{
    display: flex; flex-direction: column; gap: 10px;
    margin-top: 8px;
  }}
  .pillar-row {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }}
  .pillar-row > .pillar-summary {{
    list-style: none;
    cursor: pointer;
    padding: 12px 16px;
    display: flex; align-items: center; gap: 12px;
    flex-wrap: wrap;
  }}
  .pillar-row > .pillar-summary::-webkit-details-marker {{ display: none; }}
  .pillar-name {{
    color: var(--text);
    font-weight: 700;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    flex: 1 1 auto;
    min-width: 0;
  }}
  .pillar-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .pillar-chip {{
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text-dim);
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .chip-crit {{ color: var(--crit); border-color: var(--crit); }}
  .chip-warn {{ color: var(--warn); border-color: var(--warn); }}
  .chip-info {{ color: var(--info); border-color: var(--info); }}
  .pillar-body {{
    padding: 0 16px 14px;
    display: flex; flex-direction: column; gap: 10px;
    border-top: 1px solid var(--border);
  }}
  .pillar-body > .finding {{ margin-top: 10px; }}
  .pillar-empty {{
    display: flex; align-items: center; gap: 10px;
    color: var(--text-faint);
    font-size: 12px;
    padding: 10px 0 0;
  }}
  .pillar-empty-icon {{
    color: var(--ok);
    font-weight: 700;
  }}
  .pillar-subgroup {{ display: flex; flex-direction: column; gap: 8px; }}
  .pillar-subgroup-title {{
    margin-top: 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-faint);
    font-weight: 600;
  }}
  footer {{
    margin-top: 40px; font-size: 11px; color: var(--text-faint);
    text-align: center; font-family: "SF Mono", monospace;
  }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <img src="{icon_uri}" alt="AgentOps" />
    <div>
      <h1>AgentOps Dashboard</h1>
      <div class="subtitle" title="{workspace}">{workspace_display}</div>
    </div>
  </div>
  <div class="stats">
    <div class="stats-counts">
      <div><span class="stat-num">{eval_runs}</span> eval(s)</div>
      <div><span class="stat-num">{analyses}</span> analysis run(s)</div>
    </div>
    {powered_by}
  </div>
</header>

{setup_banner}

{range_bar}
<div class="range-current">window: {range_label}</div>

{eval_section}
{deployments_section}
{metrics_section}
{watchdog_section}
{production_section}

<footer>Auto-refresh: <span id="refreshFooter">every 5 min</span> · <code>agentops dashboard</code></footer>

<script>
// Wire interactive sparkline hover on every card. Exposed as a function
// so we can re-run it after the production grid is replaced via fetch.
function wireSparklineHover(root) {{
  (root || document).querySelectorAll('.card').forEach(function(card) {{
    if (card.dataset.hoverWired === '1') return;
    card.dataset.hoverWired = '1';
    const valueNum = card.querySelector('.value-num');
    const hoverDetail = card.querySelector('.hover-detail');
    if (!valueNum) return;
    const origValue = valueNum.dataset.orig || valueNum.textContent;
    card.querySelectorAll('svg.sparkline .dot').forEach(function(dot) {{
      dot.addEventListener('mouseenter', function() {{
        const v = dot.getAttribute('data-v');
        const l = dot.getAttribute('data-l');
        const altHref = dot.getAttribute('data-alt-href');
        const altLabel = dot.getAttribute('data-alt-label');
        if (v) valueNum.textContent = v;
        if (hoverDetail) {{
          hoverDetail.innerHTML = '';
          if (l) {{
            const labelSpan = document.createElement('span');
            labelSpan.className = 'hover-label';
            labelSpan.textContent = l;
            hoverDetail.appendChild(labelSpan);
          }}
          if (altHref && altLabel) {{
            const a = document.createElement('a');
            a.className = 'hover-alt-pill';
            a.href = altHref;
            if (!altHref.startsWith('/')) {{
              a.target = '_blank';
              a.rel = 'noopener noreferrer';
            }}
            a.textContent = altLabel + ' \u2197';
            hoverDetail.appendChild(a);
          }}
          hoverDetail.classList.add('active');
        }}
      }});
    }});
    card.addEventListener('mouseleave', function() {{
      valueNum.textContent = origValue;
      if (hoverDetail) {{
        hoverDetail.innerHTML = '';
        hoverDetail.classList.remove('active');
      }}
    }});
  }});
}}
wireSparklineHover();

// Deferred load of the Production telemetry grid. The initial page render
// skips the App Insights round-trip so the dashboard opens immediately;
// this fetch fills in the slow part asynchronously without blocking.
(function() {{
  const grid = document.getElementById('production-grid');
  if (!grid || !grid.querySelector('.loading-card')) return;
  const params = window.location.search || '';
  fetch('/api/production/html' + params)
    .then(function(r) {{ return r.ok ? r.text() : null; }})
    .then(function(html) {{
      if (html === null) return;
      grid.innerHTML = html;
      wireSparklineHover(grid);
    }})
    .catch(function() {{ /* best-effort; leave the placeholder up */ }});
}})();

// Configurable auto-refresh. Replaces the old <meta http-equiv="refresh">
// so the user can pick the cadence (Off / 1m / 5m / 15m / 30m / 1h) and
// the choice persists across reloads via localStorage.
(function() {{
  const STORAGE_KEY = 'agentops.dashboard.refreshMs';
  const select = document.getElementById('refreshSelect');
  const footer = document.getElementById('refreshFooter');
  const control = select ? select.closest('.refresh-control') : null;
  if (!select) return;

  const LABELS = {{
    '0':       'off',
    '60000':   'every 1 min',
    '300000':  'every 5 min',
    '900000':  'every 15 min',
    '1800000': 'every 30 min',
    '3600000': 'every 1 hour'
  }};

  let timerId = null;
  function applyPeriod(ms) {{
    if (timerId) {{ clearTimeout(timerId); timerId = null; }}
    if (footer) footer.textContent = LABELS[String(ms)] || 'off';
    if (ms > 0) {{
      timerId = setTimeout(function() {{
        if (control) control.classList.add('spinning');
        window.location.reload();
      }}, ms);
    }}
  }}

  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored !== null && LABELS[stored] !== undefined) {{
    select.value = stored;
  }}
  applyPeriod(parseInt(select.value, 10) || 0);

  select.addEventListener('change', function() {{
    const ms = parseInt(select.value, 10) || 0;
    try {{ window.localStorage.setItem(STORAGE_KEY, String(ms)); }} catch (e) {{}}
    applyPeriod(ms);
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app(workspace: Path):
    """Return a FastAPI app rooted at *workspace*."""
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "agentops dashboard requires the [agent] extra. "
            "Install with: pip install 'agentops-toolkit[agent]'"
        ) from exc

    app = FastAPI(title="AgentOps Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def _index(
        range_: Optional[str] = Query(None, alias="range"),
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
        partial: Optional[str] = Query(None, alias="_partial"),
    ) -> HTMLResponse:
        # The full render does file IO, history aggregation, and a
        # `gh run list` subprocess for CI/CD, which together can take
        # several seconds. To avoid a "black page" while the browser
        # waits, ``/`` returns a tiny branded shell with an animated
        # loader, and the shell fetches ``/?_partial=1`` to hydrate
        # the real dashboard once the heavy work completes.
        if not partial:
            return HTMLResponse(_render_loading_shell())
        time_range = parse_time_range(range_, from_, to)
        payload = build_dashboard_payload(workspace, time_range=time_range)
        return HTMLResponse(render_dashboard_html(payload))

    @app.get("/favicon.ico")
    def _favicon():
        from fastapi.responses import Response
        try:
            data = _pkg_files("agentops.templates").joinpath("icon.png").read_bytes()
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        return Response(content=data, media_type="image/png")

    @app.get("/api/history")
    def _api_history(limit: Optional[int] = None) -> JSONResponse:
        records = load_analysis_history(workspace, limit=limit)
        return JSONResponse([r.to_dict() for r in records])

    @app.get("/api/eval-runs")
    def _api_eval_runs(limit: int = 24) -> JSONResponse:
        return JSONResponse(_load_eval_runs(workspace, limit=limit))

    @app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
    def _api_run_report(run_id: str) -> HTMLResponse:
        return HTMLResponse(_render_run_report_html(workspace, run_id))

    @app.get("/api/telemetry")
    def _api_telemetry() -> JSONResponse:
        return JSONResponse(_telemetry_status())

    @app.get("/api/production")
    def _api_production(
        range_: Optional[str] = Query(None, alias="range"),
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
    ) -> JSONResponse:
        time_range = parse_time_range(range_, from_, to)
        return JSONResponse(_build_production_section(_telemetry_status(), time_range=time_range))

    @app.get("/api/production/html", response_class=HTMLResponse)
    def _api_production_html(
        range_: Optional[str] = Query(None, alias="range"),
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
    ) -> HTMLResponse:
        time_range = parse_time_range(range_, from_, to)
        production = _build_production_section(_telemetry_status(), time_range=time_range)
        return HTMLResponse(render_production_grid_html(production))

    @app.get("/healthz")
    def _healthz() -> Dict[str, str]:
        return {"status": "ok"}

    return app