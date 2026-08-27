"""Tests for :mod:`agentops.agent.cockpit`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from agentops.agent.cockpit import (
    build_cockpit_payload,
    render_cockpit_html,
)
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.history import append_analysis, build_record
from agentops.agent.time_range import TimeRange
from fixtures.observe import make_attribution_config_payload


# Tests run against a wide time range so the cockpit filter does not
# accidentally exclude fixture runs based on the test wall clock.
_WIDE = TimeRange(
    key="custom",
    label="test-window",
    start=datetime(2000, 1, 1, tzinfo=timezone.utc),
    end=datetime(2100, 1, 1, tzinfo=timezone.utc),
    hours=24 * 365 * 100,
)


def _make_alert_coverage(
    *,
    state: str,
    reason: str | None = None,
    rules: tuple = (),
    by_category: dict | None = None,
    iac_provenance: tuple = (),
):
    """Build a deterministic AlertCoverage for cockpit card tests."""
    from agentops.utils.alert_discovery import AlertCoverage

    categories = by_category or {
        "quality": "gap",
        "safety": "gap",
        "errors": "gap",
        "latency": "gap",
    }
    return AlertCoverage(
        state=state,
        reason=reason,
        rules=rules,
        by_category=categories,
        iac_provenance=iac_provenance,
    )


@pytest.fixture(autouse=True)
def _stub_alert_coverage(monkeypatch):
    """Keep cockpit alert cards deterministic and offline by default.

    Individual tests override this by patching
    ``agentops.utils.alert_discovery.discover_alert_coverage`` again. The
    default mirrors the real ``not_applicable`` path (no endpoint / nothing to
    verify) while faithfully echoing IaC provenance, so no Azure call is ever
    made from cockpit tests regardless of the developer's environment.
    """
    from agentops.utils import alert_discovery

    def _stub(project_endpoint, *, iac_provenance=(), **_kwargs):
        return _make_alert_coverage(
            state=alert_discovery.STATE_NOT_APPLICABLE,
            reason="No Foundry project endpoint is configured.",
            iac_provenance=tuple(iac_provenance),
        )

    monkeypatch.setattr(alert_discovery, "discover_alert_coverage", _stub)


def _set_appinsights_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "ApplicationId=11111111-1111-1111-1111-111111111111",
    )


def _dir_to_iso(timestamp_dir: str) -> str:
    """Convert a filesystem-safe timestamp directory (`T20-00-00Z`) into a
    proper ISO-8601 string for the results.json `started_at` field."""
    # ``2026-05-11T20-00-00Z`` → ``2026-05-11T20:00:00+00:00``
    if "T" in timestamp_dir:
        date_part, time_part = timestamp_dir.split("T", 1)
        time_part = time_part.replace("Z", "")
        time_part = time_part.replace("-", ":")
        return f"{date_part}T{time_part}+00:00"
    return timestamp_dir


def _make_history(workspace: Path, *severities_and_categories):
    """Append one record per (severity, category) tuple given."""
    for idx, (sev, cat) in enumerate(severities_and_categories):
        finding = Finding(
            id=f"f-{idx}",
            severity=sev,
            title="t",
            summary="s",
            recommendation="r",
            source="test",
            category=cat,
        )
        record = build_record(
            [finding],
            sources_enabled=["results_history"],
            lookback_days=7,
            duration_seconds=0.5,
        )
        append_analysis(workspace, record)


def _write_eval_run(
    workspace: Path,
    *,
    timestamp_dir: str,
    passed: bool,
    metrics: dict,
    target: str = "agent-smoke:2",
    items_total: int = 3,
    execution: str = "cloud",
    duration: float = 12.3,
    started_at: str | None = None,
    cloud_evaluation: dict | None = None,
) -> None:
    out = workspace / ".agentops" / "results" / timestamp_dir
    out.mkdir(parents=True, exist_ok=True)
    # Real AgentOps writes a proper ISO timestamp into results.json; the
    # directory name is filesystem-safe (no colons) and not parsed.
    iso_ts = started_at or _dir_to_iso(timestamp_dir)
    payload = {
        "version": 1,
        "started_at": iso_ts,
        "finished_at": iso_ts,
        "duration_seconds": duration,
        "target": {"kind": "foundry_prompt", "raw": target},
        "summary": {
            "items_total": items_total,
            "items_passed_all": items_total if passed else 0,
            "overall_passed": passed,
            "items_pass_rate": 1.0 if passed else 0.0,
            "thresholds_total": 4,
            "thresholds_passed": 4 if passed else 2,
            "threshold_pass_rate": 1.0 if passed else 0.5,
        },
        "aggregate_metrics": metrics,
        "config": {"execution": execution},
    }
    (out / "results.json").write_text(json.dumps(payload), encoding="utf-8")
    if cloud_evaluation is not None:
        # Cloud runs persist this sidecar so the cockpit can resolve
        # the Foundry project root for deep-links.
        (out / "cloud_evaluation.json").write_text(
            json.dumps(cloud_evaluation), encoding="utf-8",
        )


def test_empty_workspace_yields_empty_state(tmp_path: Path):
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    assert payload["watchdog"]["has_history"] is False
    assert len(payload["readiness"]["checks"]) == 8
    html = render_cockpit_html(payload)
    assert "No analysis history yet" in html
    assert "NO-GO" in html


def test_telemetry_status_reflects_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    assert payload["telemetry"]["enabled"] is False
    assert payload["telemetry"]["source"] == "off"

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=abc")
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    assert payload["telemetry"]["enabled"] is True
    assert payload["telemetry"]["source"] == "env"


def test_telemetry_status_accepts_foundry_project_managed_identity(monkeypatch):
    from agentops.agent.cockpit import _telemetry_status
    from agentops.utils import foundry_discovery

    resource_id = (
        "/subscriptions/000/resourceGroups/rg/providers/"
        "Microsoft.Insights/components/appi-pmi"
    )
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/pmi",
    )
    monkeypatch.setattr(
        foundry_discovery,
        "resolve_appinsights_connection_from_env_with_reason",
        lambda: (
            None,
            "Foundry Application Insights connection uses "
            "ProjectManagedIdentity; API Key credentials are not required.",
        ),
    )
    monkeypatch.setattr(
        foundry_discovery,
        "resolve_appinsights_resource_id_from_env_with_reason",
        lambda: (resource_id, None),
    )

    status = _telemetry_status()

    assert status["enabled"] is True
    assert status["source"] == "foundry_project_connection"
    assert status["resource_id"] == resource_id
    assert status["portal_url"].endswith(f"#resource{resource_id}/overview")


def test_watchdog_section_surfaces_latest_findings(tmp_path: Path):
    """The watchdog section exposes the latest run's findings (sorted by
    severity desc) instead of per-category trend charts."""
    # One record with two findings — the section surfaces findings from
    # the most-recent record, not historical ones.
    findings = [
        Finding(
            id="f-warn",
            severity=Severity.WARNING,
            title="quality warning",
            summary="summary 1",
            recommendation="rec 1",
            source="test",
            category=Category.QUALITY,
        ),
        Finding(
            id="f-crit",
            severity=Severity.CRITICAL,
            title="reliability outage",
            summary="summary 2",
            recommendation="rec 2",
            source="test",
            category=Category.RELIABILITY,
        ),
    ]
    record = build_record(
        findings, sources_enabled=["results_history"], lookback_days=7, duration_seconds=0.5,
    )
    append_analysis(tmp_path, record)

    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    assert payload["watchdog"]["has_history"] is True
    surfaced = payload["watchdog"]["latest_findings"]
    assert len(surfaced) == 2
    # Critical should sort above warning.
    assert surfaced[0]["severity"] == "critical"
    assert surfaced[1]["severity"] == "warning"
    # Old per-category trend cards are gone — replaced by the list.
    assert "category_cards" not in payload["watchdog"]


def test_finding_recommendation_renders_safe_markdown(tmp_path: Path):
    recommendation = (
        "Diversify the dataset along the flagged axes. "
        "**Concrete fixes the judge model suggested for this specific case:** "
        "- Include examples from various geographical locations. "
        "- Add scenarios that cover different domains or subjects. "
        "- Escape <script>alert(1)</script> safely."
    )
    finding = Finding(
        id="rai.dataset_distribution_skew",
        severity=Severity.WARNING,
        title="Evaluation dataset shows distribution skew",
        summary="The judge model identified distribution skew.",
        recommendation=recommendation,
        source="llm_judge",
        category=Category.RESPONSIBLE_AI,
    )
    record = build_record(
        [finding],
        sources_enabled=["llm_judge"],
        lookback_days=7,
        duration_seconds=0.5,
    )
    append_analysis(tmp_path, record)

    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    assert "**Concrete fixes" not in html
    assert " - Include examples" not in html
    assert '<strong class="recommendation-mark">Concrete fixes' in html
    assert '<ul class="recommendation-list">' in html
    assert "<li>Include examples from various geographical locations.</li>" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_html_contains_exactly_five_sections_in_required_order(
    tmp_path: Path, monkeypatch,
):
    _set_appinsights_env(monkeypatch)
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0, "fluency": 4.0},
        cloud_evaluation={
            "report_url": (
                "https://ai.azure.com/nextgen/r/sub,rg,,account,project/"
                "build/evaluations/eval-1/run/run-1"
            ),
        },
    )
    _make_history(tmp_path, (Severity.INFO, Category.QUALITY))
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    status_cards_pos = html.find('id="section-status-cards"')
    connections_pos = html.find('<span class="section-title-text">Connections')
    readiness_pos = html.find('<span class="section-title-text">Observability readiness')
    doctor_pos = html.find('<span class="section-title-text">AgentOps Doctor')
    actions_pos = html.find('<span class="section-title-text">Next actions')
    assert -1 not in (
        status_cards_pos,
        connections_pos,
        readiness_pos,
        doctor_pos,
        actions_pos,
    )
    assert (
        status_cards_pos
        < connections_pos
        < readiness_pos
        < doctor_pos
        < actions_pos
    )
    assert html.count('id="section-status-cards"') == 1
    assert html.count('id="section-connections"') == 1
    assert html.count('id="section-readiness"') == 1
    assert html.count('id="section-agentops-doctor"') == 1
    assert html.count('id="section-next-actions"') == 1
    assert html.count('<a class="card status-card') == 2
    assert "Readiness" in html
    assert "Doctor" in html

    for removed in (
        '<span class="section-title-text">Eval gates',
        '<span class="section-title-text">Production signal',
        '<span class="section-title-text">CI/CD Pipelines',
        '<span class="section-title-text">Foundry launchpad',
        "range-pills",
        "refreshSelect",
        "Auto-refresh",
        "window:",
    ):
        assert removed not in html


def test_connections_only_contains_foundry_and_github(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://account.services.ai.azure.com/api/projects/project",
    )
    monkeypatch.setattr(
        "agentops.agent.cockpit._resolve_github_repository",
        lambda _workspace: {
            "name": "owner/repo",
            "url": "https://github.com/owner/repo",
        },
    )
    payload = build_cockpit_payload(tmp_path)
    items = payload["connections"]["items"]
    assert [item["title"] for item in items] == [
        "Foundry project",
        "GitHub repository",
    ]
    html = render_cockpit_html(payload)
    assert "Open in Foundry" in html
    assert "Open in GitHub" in html
    assert "Azure tenant" not in html
    assert "Application Insights</div>" not in html


def test_readiness_splits_connection_and_instrumentation(tmp_path: Path):
    """Readiness separates App Insights linkage from agent instrumentation."""
    from agentops.agent.cockpit import (
        _build_readiness_checklist,
        _render_readiness_section,
    )

    telemetry = {"enabled": True, "detail": "ok", "portal_url": "https://x"}
    deployments = {"has_data": False}

    # No Doctor history → continuous-eval row is muted, not silently
    # green. The cockpit must not pretend a feature is configured just
    # because Doctor was never run.
    readiness = _build_readiness_checklist(
        tmp_path, telemetry, deployments, watchdog=None,
    )
    titles = [c["title"] for c in readiness["checks"]]
    assert "App Insights connection" in titles
    assert "Agent tracing instrumentation" in titles
    cont_row = next(
        c for c in readiness["checks"]
        if "Continuous evaluation rules" in c["title"]
    )
    assert cont_row["status"] == "muted"
    assert "agentops doctor" in cont_row["detail"]

    html = _render_readiness_section(readiness)
    assert "&amp;rarr;" not in html
    assert "App Insights connection" in html


def test_readiness_detects_multiturn_and_threshold_bound_rubric(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:3\n"
        "dataset: .agentops/data/travel-conversations.jsonl\n"
        "dataset_kind: multi-turn\n"
        "execution: azd\n"
        "thresholds:\n"
        "  task_success: \">=0.8\"\n"
        "rubrics:\n"
        "  - name: travel-concierge-quality\n"
        "    evaluator: travel-concierge-quality\n"
        "    dimensions:\n"
        "      - name: task_success\n"
        "        description: Completes the requested trip plan.\n",
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "travel-conversations.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Plan a trip to Rome."},
                    {"role": "assistant", "content": "Here is a 3-day plan."},
                ],
                "expected": "A multi-day Rome itinerary.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    by_title = {check["title"]: check for check in readiness["checks"]}

    # Multi-turn coverage is applicable (declared + real conversation rows).
    assert by_title["Multi-turn eval coverage"]["status"] == "ok"
    # Rubric gates readiness only because a threshold binds one of its metrics.
    assert by_title["Optional rubric evaluator gate"]["status"] == "ok"
    # Trace sampling / replay cards were removed entirely.
    assert "Trace sampling for live quality" not in by_title
    assert "Trace replay linked to evidence" not in by_title


def test_readiness_hides_multiturn_for_single_turn_dataset(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: support-agent:4\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "dataset_kind: single-turn\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    titles = [c["title"] for c in readiness["checks"]]
    # Single-turn agents are not deficient for being single-turn.
    assert "Multi-turn eval coverage" not in titles


def test_readiness_rubric_declared_without_threshold_is_not_a_gate(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: travel-agent:3\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "rubrics:\n"
        "  - name: travel-concierge-quality\n"
        "    evaluator: travel-concierge-quality\n"
        "    dimensions:\n"
        "      - name: task_success\n"
        "        description: Completes the requested trip plan.\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    by_title = {check["title"]: check for check in readiness["checks"]}
    # Declared but not threshold-bound -> informational, never a missing gate.
    assert by_title["Optional rubric evaluator gate"]["status"] == "muted"


def test_readiness_detects_hosted_otel_eval_rubric_and_unknown_alerts(
    tmp_path: Path,
):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "azure.yaml").write_text(
        "services:\n"
        "  helpdeskbot:\n"
        "    host: azure.ai.agent\n"
        "    kind: hosted\n"
        "    project: ./src/helpdeskbot\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "helpdeskbot"
    source.mkdir(parents=True)
    (source / "acs_middleware.py").write_text(
        "from opentelemetry import trace\n"
        "tracer = trace.get_tracer(__name__)\n"
        "with tracer.start_as_current_span('acs'):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (source / "eval.yaml").write_text(
        "evaluators:\n"
        "  - name: helpdeskbot-safe-eval\n"
        "    local_uri: evaluators/helpdeskbot-safe-eval\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {
            "enabled": True,
            "detail": "Linked through Project Managed Identity.",
            "portal_url": "https://portal.azure.com/#resource/appi",
        },
        {"has_data": False},
        watchdog=None,
    )
    by_title = {check["title"]: check for check in readiness["checks"]}

    assert by_title["App Insights connection"]["status"] == "ok"
    tracing = by_title["Agent tracing instrumentation"]
    assert tracing["status"] == "ok"
    assert "native tracing" in tracing["detail"]
    assert "no application-side OpenTelemetry setup is required" in tracing["detail"]
    assert "acs_middleware.py" in tracing["detail"]
    # The azd eval recipe declares a rubric evaluator, but no threshold binds
    # its metrics, so it is informational (muted), not a missing gate.
    assert by_title["Optional rubric evaluator gate"]["status"] == "muted"
    assert "src/helpdeskbot/eval.yaml" in by_title[
        "Optional rubric evaluator gate"
    ]["detail"]
    assert by_title["Alerts wired"]["status"] == "info"
    assert "Not verified" in by_title["Alerts wired"]["detail"]
    assert "does not claim" in by_title["Alerts wired"]["detail"]


def test_readiness_recognizes_prompt_agent_native_tracing(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: support-agent:4\n"
        "dataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    tracing = next(
        check
        for check in readiness["checks"]
        if check["title"] == "Agent tracing instrumentation"
    )

    assert tracing["status"] == "ok"
    assert "prompt agent runtime" in tracing["detail"]
    assert "Custom spans remain optional" in tracing["detail"]


def test_readiness_shows_iac_alerts_as_provenance_not_proof(tmp_path: Path):
    """IaC markers are provenance only and must never yield a ready card."""
    from agentops.agent.cockpit import _build_readiness_checklist

    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "alerts.bicep").write_text(
        "resource failedRequests 'Microsoft.Insights/metricAlerts@2018-03-01' = {\n"
        "  name: 'failed-requests'\n"
        "}\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    alerts = next(
        check for check in readiness["checks"] if check["title"] == "Alerts wired"
    )
    # A string in a template is not proof a rule is deployed and enabled.
    assert alerts["status"] != "ok"
    # ...but the file is still surfaced as deployment provenance.
    assert "infra/alerts.bicep" in alerts["detail"]
    assert "provenance only" in alerts["detail"]


def test_alerts_wired_card_ready_when_coverage_ready(tmp_path: Path, monkeypatch):
    from agentops.agent.cockpit import _build_readiness_checklist
    from agentops.utils import alert_discovery

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: my-agent:1\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "project_endpoint: https://foundry.example.com/api/projects/proj\n",
        encoding="utf-8",
    )

    def _ready(project_endpoint, *, iac_provenance=(), **_kwargs):
        return _make_alert_coverage(
            state=alert_discovery.STATE_READY,
            rules=(SimpleNamespace(),),
            by_category={
                "quality": "gap",
                "safety": "gap",
                "errors": "covered",
                "latency": "gap",
            },
            iac_provenance=tuple(iac_provenance),
        )

    monkeypatch.setattr(alert_discovery, "discover_alert_coverage", _ready)

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    alerts = next(
        check for check in readiness["checks"] if check["title"] == "Alerts wired"
    )
    assert alerts["status"] == "ok"
    assert "Verified 1 enabled Azure Monitor alert rule" in alerts["detail"]
    assert "covered: errors" in alerts["detail"]


def test_alerts_wired_card_cannot_verify_is_not_absence(
    tmp_path: Path, monkeypatch
):
    from agentops.agent.cockpit import _build_readiness_checklist
    from agentops.utils import alert_discovery

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:1\ndataset: d.jsonl\n"
        "project_endpoint: https://foundry.example.com/api/projects/proj\n",
        encoding="utf-8",
    )

    def _cannot(project_endpoint, *, iac_provenance=(), **_kwargs):
        return _make_alert_coverage(
            state=alert_discovery.STATE_CANNOT_VERIFY,
            reason="insufficient RBAC to list alert rules",
            iac_provenance=tuple(iac_provenance),
        )

    monkeypatch.setattr(alert_discovery, "discover_alert_coverage", _cannot)

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    alerts = next(
        check for check in readiness["checks"] if check["title"] == "Alerts wired"
    )
    assert alerts["status"] == "cannot_verify"
    assert "does not claim that alerting is absent" in alerts["detail"]
    assert "Monitoring Reader" in alerts["detail"]


def test_alerts_wired_card_not_configured_is_warn(tmp_path: Path, monkeypatch):
    from agentops.agent.cockpit import _build_readiness_checklist
    from agentops.utils import alert_discovery

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:1\ndataset: d.jsonl\n"
        "project_endpoint: https://foundry.example.com/api/projects/proj\n",
        encoding="utf-8",
    )

    def _missing(project_endpoint, *, iac_provenance=(), **_kwargs):
        return _make_alert_coverage(
            state=alert_discovery.STATE_NOT_CONFIGURED,
            reason="no rule scoped to the resource",
            iac_provenance=tuple(iac_provenance),
        )

    monkeypatch.setattr(alert_discovery, "discover_alert_coverage", _missing)

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )
    alerts = next(
        check for check in readiness["checks"] if check["title"] == "Alerts wired"
    )
    assert alerts["status"] == "warn"
    assert "How to complete:" in alerts["detail"]


def test_readiness_non_ready_items_include_remediation(tmp_path: Path, monkeypatch):
    from agentops.agent.cockpit import _build_readiness_checklist

    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": False, "detail": "", "portal_url": None},
        {"has_data": False},
        watchdog=None,
    )

    non_ready = [
        check for check in readiness["checks"]
        if check["status"] != "ok"
    ]
    assert non_ready
    for check in non_ready:
        detail = check["detail"]
        if check["title"] == "Alerts wired":
            assert "Not verified" in detail
            continue
        assert "How to complete:" in detail
        assert ("<a " in detail) or ("<code>" in detail) or ("Foundry" in detail)
    by_title = {check["title"]: check["detail"] for check in readiness["checks"]}
    assert "OpenTelemetry" in by_title["Agent tracing instrumentation"]
    # Scheduled eval is optional drift-watch context and is hidden entirely
    # when no cron-scheduled workflow exists, so it must not appear here.
    assert "Scheduled eval (drift watch)" not in by_title
    # Red-team readiness is hidden before workspace init (no agentops.yaml),
    # so the card must not appear on an uninitialized workspace.
    assert "Red team scans" not in by_title
    assert "does not claim" in by_title["Alerts wired"]


def test_readiness_dots_are_binary_ready_or_not(tmp_path: Path):
    """Readiness dots must match the X/Y ready label: green only for ready,
    gray for every non-ready state."""
    from agentops.agent.cockpit import _render_readiness_section

    readiness = {
        "label": "1/4 ready",
        "checks": [
            {"title": "Ready", "status": "ok", "detail": "done"},
            {"title": "Info", "status": "info", "detail": "not counted"},
            {"title": "Warn", "status": "warn", "detail": "not counted"},
            {"title": "Muted", "status": "muted", "detail": "not counted"},
        ],
    }

    html = _render_readiness_section(readiness)

    assert html.count("background:#22c55e") == 1
    assert html.count("background:#64748b") == 3
    assert "background:#38bdf8" not in html
    assert "background:#f59e0b" not in html


def test_readiness_continuous_eval_warns_when_doctor_flags_missing_rules(
    tmp_path: Path,
):
    """When the latest Doctor analysis emitted
    ``safety.config.continuous_eval_missing`` the readiness row must
    surface a "warn" status with a Foundry-Operate next step."""
    from agentops.agent.cockpit import _build_readiness_checklist

    telemetry = {"enabled": True, "detail": "ok", "portal_url": "https://x"}
    watchdog = {
        "has_history": True,
        "latest_findings": [
            {
                "id": "safety.config.continuous_eval_missing",
                "title": "No continuous evaluation rules configured",
                "severity": "warning",
                "category": "responsible_ai",
            }
        ],
    }

    readiness = _build_readiness_checklist(
        tmp_path, telemetry, {}, watchdog=watchdog,
    )
    cont_row = next(
        c for c in readiness["checks"]
        if "Continuous evaluation rules" in c["title"]
    )
    assert cont_row["status"] == "warn"
    assert "Operate" in cont_row["detail"]
    assert "create a continuous evaluation rule" in cont_row["detail"]
    assert "Foundry monitor docs" in cont_row["detail"]


def test_next_actions_prioritize_doctor_then_incomplete_readiness():
    from agentops.agent.cockpit import _build_next_actions

    actions = _build_next_actions(
        watchdog={
            "latest_findings": [
                {
                    "id": "quality.answer",
                    "severity": "critical",
                    "title": "Answer quality is blocked",
                    "summary": "The response is incomplete.",
                    "recommendation": "Fix the response policy.",
                },
                {
                    "id": "reliability.trace",
                    "severity": "warning",
                    "title": "Trace coverage is incomplete",
                    "summary": "A trace is missing.",
                    "recommendation": "Enable trace capture.",
                },
            ],
        },
        readiness={
            "checks": [
                {
                    "title": "Server-side tracing",
                    "status": "warn",
                    "detail": "How to complete: enable tracing.",
                },
                {
                    "title": "Alerts wired",
                    "status": "ok",
                    "detail": "Ready.",
                },
            ],
        },
    )

    assert [action["title"] for action in actions["actions"]] == [
        "Fix Doctor: Answer quality is blocked",
        "Fix Doctor: Trace coverage is incomplete",
        "Complete readiness: Server-side tracing",
    ]


def test_next_actions_skip_non_actionable_statuses():
    """Hidden, not-applicable, informational, muted, and ok statuses must not
    manufacture any action."""
    from agentops.agent.cockpit import _build_next_actions

    actions = _build_next_actions(
        watchdog={"latest_findings": []},
        readiness={
            "checks": [
                {"title": "Ready", "status": "ok", "detail": "done"},
                {"title": "Info", "status": "info", "detail": "context"},
                {"title": "Muted", "status": "muted", "detail": "optional"},
                {"title": "NotApplicable", "status": "na", "detail": "n/a"},
                {"title": "Hidden", "status": "hidden", "detail": "hidden"},
            ],
        },
    )

    titles = [action["title"] for action in actions["actions"]]
    assert titles == ["All caught up"]


def test_next_actions_cannot_verify_uses_softer_wording():
    """``cannot_verify`` is not a failure, so it yields an "Enable
    verification" action, never a "Complete readiness" one."""
    from agentops.agent.cockpit import _build_next_actions

    actions = _build_next_actions(
        watchdog={"latest_findings": []},
        readiness={
            "checks": [
                {
                    "title": "Multi-turn coverage",
                    "status": "cannot_verify",
                    "detail": "Dataset not readable yet.",
                },
            ],
        },
    )

    titles = [action["title"] for action in actions["actions"]]
    assert titles == ["Enable verification: Multi-turn coverage"]
    assert not any("Complete readiness" in title for title in titles)


def test_next_actions_uninitialized_emits_single_onboarding_action():
    """Before init, emit exactly one onboarding action regardless of how many
    readiness checks would otherwise be non-ok."""
    from agentops.agent.cockpit import _build_next_actions

    actions = _build_next_actions(
        watchdog={"latest_findings": []},
        readiness={
            "checks": [
                {"title": "A", "status": "warn", "detail": "x"},
                {"title": "B", "status": "warn", "detail": "y"},
                {"title": "C", "status": "cannot_verify", "detail": "z"},
            ],
        },
        initialized=False,
    )

    assert len(actions["actions"]) == 1
    assert actions["actions"][0]["title"] == "Get started: initialize AgentOps"


def test_readiness_detects_official_eval_workflow_and_evidence(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "agentops-pr.yml").write_text(
        "\n".join(
            [
                "name: AgentOps PR",
                "on:",
                "  schedule:",
                "    - cron: '0 3 * * *'",
                "jobs:",
                "  eval:",
                "    steps:",
                "      - uses: microsoft/ai-agent-evals@v3-beta",
                "      - run: python -m agentops.pipeline.official_eval prepare",
            ]
        ),
        encoding="utf-8",
    )
    evidence_dir = tmp_path / ".agentops" / "release" / "latest"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "evidence.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "generated_at": "2025-01-01T00:00:00Z",
                "ready": ["Latest eval gate"],
                "warnings": [],
                "blockers": [],
                "latest_eval": {"runner": "official-ai-agent-evaluation"},
                "official_eval": {"machine_readable_thresholds": False},
            }
        ),
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog={"has_history": True, "latest_findings": []},
    )

    by_title = {check["title"]: check for check in readiness["checks"]}
    assert by_title["CI eval gate (workflow on PRs)"]["status"] == "ok"
    assert "official Microsoft Foundry AI Agent Evaluation" in by_title[
        "CI eval gate (workflow on PRs)"
    ]["detail"]
    assert by_title["Scheduled eval (drift watch)"]["status"] == "info"
    assert "official Microsoft Foundry AI Agent Evaluation" in by_title[
        "Scheduled eval (drift watch)"
    ]["detail"]
    assert by_title["Release evidence pack"]["status"] == "ok"
    assert "official Microsoft Foundry AI Agent Evaluation" in by_title[
        "Release evidence pack"
    ]["detail"]


def test_readiness_detects_agentops_cloud_eval_workflow_and_evidence(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "agentops-pr.yml").write_text(
        "\n".join(
            [
                "name: AgentOps PR",
                "on:",
                "  schedule:",
                "    - cron: '0 3 * * *'",
                "jobs:",
                "  eval:",
                "    steps:",
                "      - name: Prepare AgentOps cloud eval config",
                "        run: data[\"execution\"] = \"cloud\"",
                "      - name: Run AgentOps Foundry cloud eval",
                "        run: agentops eval run --config \"$AGENTOPS_CI_CONFIG\"",
            ]
        ),
        encoding="utf-8",
    )
    evidence_dir = tmp_path / ".agentops" / "release" / "latest"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "evidence.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "generated_at": "2025-01-01T00:00:00Z",
                "ready": ["Latest eval gate"],
                "warnings": [],
                "blockers": [],
                "latest_eval": {"runner": "agentops-cloud"},
            }
        ),
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog={"has_history": True, "latest_findings": []},
    )

    by_title = {check["title"]: check for check in readiness["checks"]}
    assert by_title["CI eval gate (workflow on PRs)"]["status"] == "ok"
    assert "AgentOps cloud eval" in by_title["CI eval gate (workflow on PRs)"][
        "detail"
    ]
    assert by_title["Scheduled eval (drift watch)"]["status"] == "info"
    assert "AgentOps cloud eval" in by_title["Scheduled eval (drift watch)"][
        "detail"
    ]
    assert by_title["Release evidence pack"]["status"] == "ok"
    assert "AgentOps cloud eval in Foundry" in by_title["Release evidence pack"][
        "detail"
    ]


def test_readiness_details_include_azd_eval_and_governance_evidence(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    evidence_dir = tmp_path / ".agentops" / "release" / "latest"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "evidence.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "generated_at": "2025-01-01T00:00:00Z",
                "ready": ["Latest eval gate"],
                "warnings": [],
                "blockers": [],
                "latest_eval": {"runner": "azd-ai-agent-eval"},
                "governance": {
                    "assert": {"status": "present"},
                    "acs": {"status": "present"},
                    "redteam": {"status": "not_configured"},
                },
            }
        ),
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog={"has_history": True, "latest_findings": []},
    )

    detail = {check["title"]: check for check in readiness["checks"]}[
        "Release evidence pack"
    ]["detail"]
    assert "azd ai agent eval" in detail
    assert "Governance evidence: assert: present, acs: present." in detail


def test_readiness_detects_prompt_agent_deploy_workflow(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "agentops-deploy-dev.yml").write_text(
        "\n".join(
            [
                "# agentops:deploy-mode=prompt-agent",
                "steps:",
                "  - run: agentops eval run --config .agentops/deployments/agentops.candidate.yaml",
            ]
        ),
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "Linked", "portal_url": "https://x"},
        {"has_data": False},
        watchdog={"has_history": True, "latest_findings": []},
    )

    deploy_row = next(c for c in readiness["checks"] if c["title"] == "CI/CD deploy stage")
    assert deploy_row["status"] == "ok"
    assert "prompt-agent deploy workflow" in deploy_row["detail"]
    assert "evaluates that exact version" in deploy_row["detail"]


def test_readiness_continuous_eval_ok_when_doctor_finds_no_problem(
    tmp_path: Path,
):
    """A Doctor run that did not emit the continuous-eval findings is
    treated as confirmation that rules are configured."""
    from agentops.agent.cockpit import _build_readiness_checklist

    telemetry = {"enabled": True, "detail": "ok", "portal_url": "https://x"}
    watchdog = {"has_history": True, "latest_findings": []}

    readiness = _build_readiness_checklist(
        tmp_path, telemetry, {}, watchdog=watchdog,
    )
    cont_row = next(
        c for c in readiness["checks"]
        if "Continuous evaluation rules" in c["title"]
    )
    assert cont_row["status"] == "ok"


def test_deployments_diagnostic_not_a_git_repo(tmp_path: Path):
    """Empty tempdir → deployments section explains it is not a git repo."""
    from agentops.agent.cockpit import (
        _build_deployments_section,
        _deployments_cache,
    )
    _deployments_cache.clear()
    out = _build_deployments_section(tmp_path, _WIDE)
    assert out["has_data"] is False
    assert out["reason"] == "not-git-repo"
    assert "not inside a Git repository" in out["hint"]


def test_deployments_diagnostic_no_github_remote(tmp_path: Path):
    """Git repo without any remote → deployments tells the user precisely."""
    import subprocess
    from agentops.agent.cockpit import (
        _build_deployments_section,
        _deployments_cache,
        _diagnose_gh_state,
    )
    import shutil as _shutil
    if _shutil.which("git") is None or _shutil.which("gh") is None:
        import pytest
        pytest.skip("git or gh CLI not available")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _deployments_cache.clear()
    diag = _diagnose_gh_state(tmp_path)
    assert diag["state"] == "no-github-remote"

    _deployments_cache.clear()
    out = _build_deployments_section(tmp_path, _WIDE)
    assert out["has_data"] is False
    assert out["reason"] == "no-github-remote"
    assert "no GitHub remote" in out["hint"]


def test_create_app_serves_cockpit(tmp_path: Path):
    """FastAPI integration smoke test (skipped if FastAPI not installed)."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        import pytest
        pytest.skip("fastapi extras not installed")

    from agentops.agent.cockpit import create_app

    _make_history(tmp_path, (Severity.INFO, Category.QUALITY))
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0},
    )
    client = TestClient(create_app(tmp_path))

    # ``/`` returns the instant loading shell (no full render).
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "AgentOps Cockpit - Loading" in r.text
    assert "loader-spinner" in r.text
    assert "_partial=1" in r.text  # JS hydrates from the partial endpoint

    # ``/?_partial=1`` returns the full cockpit HTML.
    r = client.get("/?_partial=1")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "AgentOps Cockpit" in r.text
    assert "range-pills" not in r.text
    assert "refreshSelect" not in r.text

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    r = client.get("/api/history")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get("/api/eval-runs")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get("/api/telemetry")
    assert r.status_code == 200
    payload = r.json()
    assert "enabled" in payload
    assert "source" in payload



def test_pillar_rows_rendered_in_canonical_order(tmp_path: Path):
    """All six WAF-AI pillars render as rows, in fixed order, even when
    most pillars are empty."""
    _make_history(
        tmp_path,
        (Severity.CRITICAL, Category.QUALITY),
        (Severity.WARNING, Category.OPERATIONAL_EXCELLENCE),
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    # Six pillar rows present.
    expected_labels = [
        "Quality",
        "Performance Efficiency",
        "Reliability",
        "Operational Excellence",
        "Security",
        "Responsible AI",
    ]
    positions = [html.find(f'>{label}</span>') for label in expected_labels]
    assert all(p > 0 for p in positions), positions
    assert positions == sorted(positions), (
        "pillar rows must render in canonical WAF-AI order"
    )


def test_empty_pillars_render_clean_indicator(tmp_path: Path):
    """Pillars with no findings still render with an explicit 'clean'
    indicator — the absence is a signal too."""
    _make_history(tmp_path, (Severity.WARNING, Category.QUALITY))
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    # Reliability has no findings; it should still render with the
    # pillar-empty class.
    assert "pillar-empty" in html


def test_spec_conformance_subsection_inside_opex_row(tmp_path: Path):
    """opex.spec_conformance.* findings render in their own sub-section
    inside the Operational Excellence row."""
    finding = Finding(
        id="opex.spec_conformance.spec_missing",
        severity=Severity.WARNING,
        title="Spec missing",
        summary="Spec scaffolding present but no content.",
        recommendation="Author the spec.",
        source="spec_workspace",
        category=Category.OPERATIONAL_EXCELLENCE,
    )
    record = build_record(
        [finding],
        sources_enabled=["spec_workspace"],
        lookback_days=7,
        duration_seconds=0.1,
    )
    append_analysis(tmp_path, record)
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    assert "Spec Conformance" in html
    assert "Workspace &amp; CI Hygiene" in html or "Workspace & CI Hygiene" in html


def test_normalize_workflow_name_rewrites_legacy_watchdog():
    """Existing repos generated before the rename have
    ``name: AgentOps watchdog`` baked into their workflow YAML.
    The cockpit must rewrite that to the current product name
    when displaying it, so users do not see the old label in the
    Latest run card."""
    from agentops.agent.cockpit import _normalize_workflow_name

    assert _normalize_workflow_name("AgentOps watchdog") == "AgentOps doctor"
    assert _normalize_workflow_name("AgentOps Watchdog") == "AgentOps Doctor"
    # Names without the legacy token pass through unchanged.
    assert _normalize_workflow_name("AgentOps PR") == "AgentOps PR"
    assert _normalize_workflow_name("") == ""


def test_cockpit_short_chat_summary_does_not_say_watchdog():
    """The Copilot Extension's short summary used to say
    "AgentOps watchdog" — make sure the rename stuck."""
    from agentops.agent.report import short_chat_summary
    from agentops.agent.analyzer import AnalysisResult
    text = short_chat_summary(AnalysisResult(findings=[]))
    assert "watchdog" not in text.lower()
    assert "doctor" in text.lower()


# ---------------------------------------------------------------------------
# Foundry connection + deep-link fixes
# ---------------------------------------------------------------------------


def test_resolve_agent_identity_reads_flat_agentops_yaml(tmp_path):
    """AgentOps 1.0 flat schema places ``agent:`` at the root of
    ``agentops.yaml``. The cockpit must pick this up; otherwise it
    incorrectly renders "No agent pinned" even when the CLI banner is
    showing the agent."""
    from agentops.agent.cockpit import _resolve_agent_identity

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\n", encoding="utf-8"
    )
    agent_id, source = _resolve_agent_identity(tmp_path)
    assert agent_id == "quickstart-agent:2"
    assert source == "agentops.yaml"


def test_resolve_agent_identity_flat_wins_over_legacy(tmp_path):
    """When both files exist, the flat 1.0 schema wins so the cockpit
    matches the CLI's behavior."""
    from agentops.agent.cockpit import _resolve_agent_identity

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: flat-agent:1\n", encoding="utf-8"
    )
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / "run.yaml").write_text(
        "target:\n  endpoint:\n    agent_id: legacy-agent:9\n",
        encoding="utf-8",
    )
    agent_id, source = _resolve_agent_identity(tmp_path)
    assert agent_id == "flat-agent:1"
    assert source == "agentops.yaml"


def test_resolve_agent_identity_falls_back_to_legacy_run_yaml(tmp_path):
    """Legacy projects still expose ``target.endpoint.agent_id`` —
    keep supporting them."""
    from agentops.agent.cockpit import _resolve_agent_identity

    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / "run.yaml").write_text(
        "target:\n  endpoint:\n    agent_id: legacy-agent:9\n",
        encoding="utf-8",
    )
    agent_id, source = _resolve_agent_identity(tmp_path)
    assert agent_id == "legacy-agent:9"
    assert source == "run.yaml"


def test_resolve_agent_identity_returns_none_when_unset(tmp_path):
    """Empty workspace: cockpit renders the muted "No agent pinned"
    state. Helper must return ``(None, "")`` so the renderer hits the
    fallback branch."""
    from agentops.agent.cockpit import _resolve_agent_identity

    agent_id, source = _resolve_agent_identity(tmp_path)
    assert agent_id is None
    assert source == ""


def test_foundry_deeplinks_use_only_build_routes(tmp_path):
    """Deep-links use the new Foundry routes for agent and project surfaces."""
    from agentops.agent.cockpit import _foundry_deeplinks

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\n", encoding="utf-8"
    )
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-12T22-19-24Z",
        passed=True,
        metrics={"similarity": 0.9},
        cloud_evaluation={
            "report_url": (
                "https://ai.azure.com/nextgen/r/"
                "abc123,rg-x,,acct-y,proj-z/build/evaluations/"
                "eval_001/run/run_001"
            ),
        },
    )

    links = _foundry_deeplinks(tmp_path)
    # No links may reference the legacy /observability or /operate
    # portal paths — those 404 in the new Foundry portal.
    for value in links.values():
        assert value is not None
        assert "/observability/" not in value
    assert links["agent"].split("?")[0].endswith("/build/agents/quickstart-agent/build")
    assert links["monitor"].split("?")[0].endswith("/build/agents/quickstart-agent/monitor")
    assert links["traces"].split("?")[0].endswith("/build/agents/quickstart-agent/traces")
    assert links["evaluations"].split("?")[0].endswith("/build/evaluations")
    assert links["red_teaming"].split("?")[0].endswith("/build/evaluations/redteam")
    assert links["datasets"].split("?")[0].endswith("/build/data/datasets")
    assert links["operate"].split("?")[0].endswith("/operate/overview")


def test_readiness_detail_links_use_info_color(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    html = render_cockpit_html(build_cockpit_payload(tmp_path, time_range=_WIDE))

    assert "Docs &#x2197;" in html
    assert ".readiness-detail a" in html
    assert "color: var(--info)" in html


def test_doctor_section_has_no_foundry_control_plane_link(tmp_path):
    """The AgentOps Doctor surfaces *local* findings only — there is no
    "Foundry control plane" equivalent that mirrors them. The section
    header must not advertise an external link that would 404."""
    _make_history(tmp_path, (Severity.WARNING, Category.OPERATIONAL_EXCELLENCE))
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    assert "Open Foundry control plane" not in html


def test_tenant_lookup_allows_slow_az_cmd_cold_start(monkeypatch):
    """Windows az.cmd can take several seconds on the first call. The
    Cockpit should wait long enough to resolve the tenant instead of
    incorrectly showing "Azure tenant unknown" while the user is logged in."""
    from agentops.agent import cockpit

    tenant = "16b3c013-d300-468d-ac64-7eda0820b6d3"
    captured: dict[str, int] = {}

    cockpit._TENANT_CACHE.clear()
    monkeypatch.setattr(
        cockpit.shutil,
        "which",
        lambda name: "C:\\Program Files\\Azure\\az.cmd" if name == "az" else None,
    )

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(returncode=0, stdout=f"{tenant}\n")

    monkeypatch.setattr(cockpit.subprocess, "run", fake_run)

    assert cockpit._az_tenant_id() == tenant
    assert captured["timeout"] == 30
    cockpit._TENANT_CACHE.clear()


def test_app_insights_logs_query_is_bounded(monkeypatch):
    from agentops.agent.cockpit import _appinsights_portal_url

    url = _appinsights_portal_url(
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "ApplicationId=11111111-1111-1111-1111-111111111111"
    )

    assert url is not None
    query = unquote(url.rsplit("/query/", 1)[1])
    assert "let agentops_requests = requests" in query
    assert "let azure_ai_dependencies = dependencies" in query
    assert "cloud_RoleName has_any ('agentops', 'test-agentops')" in query
    assert "agentops.eval.dataset" in query
    assert "openai.azure.com" in query
    assert "services.ai.azure.com" in query
    assert "gen_ai.system" in query
    assert "let logs = traces" not in query
    assert "| take 100" in query
    assert "| top 50 by timestamp desc" not in query
    assert not query.startswith("union dependencies, requests, traces")


def test_app_insights_doctor_findings_query_and_link(monkeypatch, tmp_path):
    from agentops.agent.cockpit import _appinsights_doctor_findings_portal_url

    conn = (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "ApplicationId=11111111-1111-1111-1111-111111111111"
    )
    url = _appinsights_doctor_findings_portal_url(conn)

    assert url is not None
    query = unquote(url.rsplit("/query/", 1)[1])
    assert query.startswith("let lookback = 24h;")
    assert "dependencies" in query
    assert "name startswith 'doctor finding '" in query
    assert "agentops.agent.finding.id" in query
    assert "agentops.agent.finding.recommendation" in query
    assert "| top 50 by timestamp desc" in query

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    assert "View findings in App Insights" in html


def test_app_insights_eval_runs_query_and_link(monkeypatch, tmp_path):
    from agentops.agent.cockpit import _appinsights_eval_runs_portal_url

    conn = (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "ApplicationId=11111111-1111-1111-1111-111111111111"
    )
    url = _appinsights_eval_runs_portal_url(conn)

    assert url is not None
    query = unquote(url.rsplit("/query/", 1)[1])
    assert query.startswith("let lookback = 24h;")
    assert "requests" in query
    assert "name startswith 'RUN '" in query
    assert "operation_Name startswith 'RUN '" in query
    assert "agentops.eval.dataset" in query
    assert "agentops.eval.cloud.eval_id" in query
    assert "agentops.eval.cloud.report_url" in query
    assert "| top 50 by timestamp desc" in query

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    assert "View CI evals in App Insights" not in html


def test_foundry_project_card_compacts_endpoint_and_exposes_copy(tmp_path, monkeypatch):
    """Long Foundry endpoints render as account::project with full-value copy."""
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://aif-agentops-experimentation.services.ai.azure.com/api/projects/proj-default",
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    assert "aif-agentops-experimentation::proj-default" in html
    assert (
        'data-copy="https://aif-agentops-experimentation.services.ai.azure.com/api/projects/proj-default"'
        in html
    )
    assert "copy-btn" in html


class _CostIsolationObserveService:
    async def query(
        self,
        *,
        view,
        filters,
        refresh=False,
        user_context=None,
    ):
        return {
            "view": view,
            "filters": filters,
            "refresh": refresh,
            "marker": "unchanged",
        }


class _AttributionObserveService(_CostIsolationObserveService):
    def __init__(
        self,
        *,
        attribution_result: dict | None = None,
        attribution_error: ValueError | None = None,
    ) -> None:
        self.attribution_calls: list[dict] = []
        self.attribution_result = attribution_result
        self.attribution_error = attribution_error

    async def attribution(self, *, request, user_context=None):
        self.attribution_calls.append(
            {"request": request, "user_context": user_context}
        )
        if self.attribution_error is not None:
            raise self.attribution_error
        if self.attribution_result is not None:
            return self.attribution_result
        return {
            "marker": "safe-aggregate",
            "rows": [],
            "raw_identity": None,
        }


_OBSERVE_FILTERS = {
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-09-01T00:00:00Z",
}


@pytest.mark.parametrize(
    ("raw_model", "expected_reason"),
    [
        (None, "not configured"),
        ('{"version":', "invalid"),
    ],
)
def test_cost_configuration_failure_isolated_from_all_other_observe_views(
    monkeypatch,
    tmp_path: Path,
    raw_model: str | None,
    expected_reason: str,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    if raw_model is None:
        monkeypatch.delenv("AGENTOPS_COST_MODEL", raising=False)
    else:
        monkeypatch.setenv("AGENTOPS_COST_MODEL", raw_model)
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=_CostIsolationObserveService(),
        )
    )

    for view in ("overview", "agents", "models", "tools", "runs", "coverage"):
        response = client.post(
            "/api/observe/query",
            json={"view": view, "filters": _OBSERVE_FILTERS},
        )
        assert response.status_code == 200
        assert response.json() == {
            "view": view,
            "filters": {
                **_OBSERVE_FILTERS,
                "foundry_resource_id": None,
                "project_resource_id": None,
                "agent_id": None,
                "model": None,
                "tool_name": None,
                "run_key": None,
                "cost_period_id": None,
                "cost_breakdown": None,
                "cost_component_id": None,
                "cost_agent_key": None,
                "user_filter_token": None,
                "department_filter_token": None,
            },
            "refresh": False,
            "marker": "unchanged",
        }

    cost = client.post(
        "/api/observe/query",
        json={
            "view": "cost",
            "filters": {**_OBSERVE_FILTERS, "cost_period_id": "2026-08"},
        },
    )
    assert cost.status_code == 422
    assert expected_reason in cost.json()["detail"]


def test_hosted_authentication_precedes_cost_configuration_gating(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    def _reject(_headers):
        raise PermissionError("Authentication required.")

    monkeypatch.delenv("AGENTOPS_COST_MODEL", raising=False)
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=_CostIsolationObserveService(),
            auth_context_resolver=_reject,
        )
    )

    response = client.post(
        "/api/observe/query",
        json={
            "view": "cost",
            "filters": {**_OBSERVE_FILTERS, "cost_period_id": "2026-08"},
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."
    assert "AGENTOPS_COST_MODEL" not in response.text


@pytest.mark.parametrize(
    ("raw_config", "expected_status", "expected_code"),
    [
        (None, 409, "attribution_not_enabled"),
        (
            json.dumps(
                {
                    "version": 1,
                    "enabled": False,
                    "deployment_namespace": None,
                    "generation": None,
                    "departments": [],
                }
            ),
            409,
            "attribution_not_enabled",
        ),
        (
            '{"enabled": true, "secret": "do-not-echo"}',
            503,
            "attribution_config_secret_field",
        ),
    ],
)
def test_attribution_route_fails_closed_without_affecting_existing_views(
    monkeypatch,
    tmp_path: Path,
    raw_config: str | None,
    expected_status: int,
    expected_code: str,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    if raw_config is None:
        monkeypatch.delenv("AGENTOPS_ATTRIBUTION_CONFIG", raising=False)
    else:
        monkeypatch.setenv("AGENTOPS_ATTRIBUTION_CONFIG", raw_config)
    service = _AttributionObserveService()
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": _OBSERVE_FILTERS,
        },
    )

    assert response.status_code == expected_status
    assert set(response.json()) == {"code", "message", "next_action"}
    assert response.json()["code"] == expected_code
    assert "do-not-echo" not in response.text
    assert service.attribution_calls == []
    assert client.post(
        "/api/observe/query",
        json={"view": "overview", "filters": _OBSERVE_FILTERS},
    ).status_code == 200


def test_attribution_route_validates_and_dispatches_safe_aggregate_request(
    monkeypatch,
    tmp_path: Path,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    service = _AttributionObserveService()
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )
    payload = {
        "metric": "usage",
        "group_by": "department",
        "filters": _OBSERVE_FILTERS,
    }

    response = client.post("/api/observe/attribution", json=payload)

    assert response.status_code == 200
    assert response.json()["marker"] == "safe-aggregate"
    assert response.json()["raw_identity"] is None
    assert len(service.attribution_calls) == 1
    assert service.attribution_calls[0]["request"]["metric"] == "usage"
    assert service.attribution_calls[0]["request"]["group_by"] == "department"

    strict = client.post(
        "/api/observe/attribution",
        json={**payload, "unexpected": True},
    )
    assert strict.status_code == 422
    assert len(service.attribution_calls) == 1

    missing_cost_selector = client.post(
        "/api/observe/attribution",
        json={**payload, "metric": "cost"},
    )
    assert missing_cost_selector.status_code == 422
    assert len(service.attribution_calls) == 1


@pytest.mark.parametrize(
    "selector_field",
    ["user_filter_token", "department_filter_token"],
)
def test_attribution_validation_redacts_protected_selectors(
    monkeypatch,
    tmp_path: Path,
    selector_field: str,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    service = _AttributionObserveService()
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )
    raw_token = "raw secret selector value"

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": {
                **_OBSERVE_FILTERS,
                selector_field: raw_token,
            },
        },
    )

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "code": "attribution_request_invalid",
        "message": "The attribution request is invalid.",
        "next_action": "Correct the attribution request and retry.",
    }
    assert raw_token not in response.text
    assert "input" not in response.json()
    assert service.attribution_calls == []


@pytest.mark.parametrize(
    ("raw_config", "expected_status"),
    [
        (None, 409),
        ('{"enabled": true, "token": "raw-config-token"}', 503),
    ],
)
def test_attribution_state_errors_preserve_private_selector_cache_policy(
    monkeypatch,
    tmp_path: Path,
    raw_config: str | None,
    expected_status: int,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    if raw_config is None:
        monkeypatch.delenv("AGENTOPS_ATTRIBUTION_CONFIG", raising=False)
    else:
        monkeypatch.setenv("AGENTOPS_ATTRIBUTION_CONFIG", raw_config)
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=_AttributionObserveService(),
        )
    )
    raw_token = "opaque-protected-selector"

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": {
                **_OBSERVE_FILTERS,
                "department_filter_token": raw_token,
            },
        },
    )

    assert response.status_code == expected_status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert set(response.json()) == {"code", "message", "next_action"}
    assert raw_token not in response.text
    assert "raw-config-token" not in response.text


def test_delegated_attribution_responses_are_private_and_never_cached(
    monkeypatch,
    tmp_path: Path,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    service = _AttributionObserveService(
        attribution_result={
            "data": {
                "access_boundary": "delegated",
                "rows": [{"kind": "user", "raw_identity": "alice@example.test"}],
            }
        }
    )
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "user",
            "filters": {
                **_OBSERVE_FILTERS,
                "department_filter_token": "at1~department",
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"


def test_delegated_attribution_failures_are_private_and_redacted(
    monkeypatch,
    tmp_path: Path,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app
    from agentops.core.attribution import AttributionTokenValidationError

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    service = _AttributionObserveService(
        attribution_error=AttributionTokenValidationError(
            "invalid_token",
            "The attribution selector is invalid or no longer current.",
        )
    )
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "user",
            "filters": {
                **_OBSERVE_FILTERS,
                "department_filter_token": "copied-secret-token",
            },
        },
    )

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "code": "invalid_token",
        "message": "The attribution selector is invalid or no longer current.",
        "next_action": "Select the attribution filter again.",
    }
    assert "copied-secret-token" not in response.text


def test_missing_delegated_assertion_is_private_and_never_retried(
    monkeypatch,
    tmp_path: Path,
):
    from fastapi.testclient import TestClient

    from agentops.agent.cockpit import create_app
    from agentops.agent.observe.auth import MissingUserAssertionError

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    service = _AttributionObserveService(
        attribution_error=MissingUserAssertionError(
            "Delegated Azure Monitor access is unavailable for this request."
        )
    )
    client = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/a/projects/p"
                ],
            },
            observe_service=service,
        )
    )

    response = client.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "user",
            "filters": {
                **_OBSERVE_FILTERS,
                "department_filter_token": "at1~department",
            },
        },
    )

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "code": "attribution_delegated_access_unavailable",
        "message": "Delegated Azure Monitor access is unavailable for this request.",
        "next_action": (
            "Sign in again and verify direct read access to the selected telemetry scope."
        ),
    }
    assert len(service.attribution_calls) == 1


# ---------------------------------------------------------------------------
# Project-observability-only mode (agent-less workspace)
# ---------------------------------------------------------------------------


def _write_observability_only_workspace(tmp_path: Path) -> None:
    """A workspace whose agentops.yaml has a dataset but no agent target."""
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")


def test_readiness_agentless_workspace_is_observability_only(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    _write_observability_only_workspace(tmp_path)

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )

    assert readiness["observability_only"] is True
    assert readiness["mode_label"] == "Project observability only"

    by_title = {check["title"]: check for check in readiness["checks"]}
    # An explicit informational "Evaluation target" row is present at index 0.
    assert readiness["checks"][0]["title"] == "Evaluation target"
    assert readiness["checks"][0]["status"] == "info"
    # Agent/eval-dependent release gates are not-applicable, never failed.
    for title in (
        "CI eval gate (workflow on PRs)",
        "CI/CD deploy stage",
        "Release evidence pack",
    ):
        if title in by_title:
            assert by_title[title]["status"] == "na"


def test_readiness_legacy_placeholder_is_observability_only(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )

    # The legacy my-agent:1 placeholder is treated as "no target configured".
    assert readiness["observability_only"] is True


def test_readiness_real_agent_is_not_observability_only(tmp_path: Path):
    from agentops.agent.cockpit import _build_readiness_checklist

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:3\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    readiness = _build_readiness_checklist(
        tmp_path,
        {"enabled": True, "detail": "ok", "portal_url": "https://x"},
        {"has_data": False},
        watchdog=None,
    )

    assert readiness["observability_only"] is False
    titles = [c["title"] for c in readiness["checks"]]
    assert "Evaluation target" not in titles


def test_next_actions_observability_only_emits_single_configure_action():
    from agentops.agent.cockpit import _build_next_actions

    actions = _build_next_actions(
        watchdog={"latest_findings": []},
        readiness={
            "observability_only": True,
            "checks": [
                {"title": "Evaluation target", "status": "info", "detail": "x"},
                {"title": "CI eval gate (workflow on PRs)", "status": "na", "detail": "y"},
                {"title": "CI/CD deploy stage", "status": "na", "detail": "z"},
                {"title": "Release evidence pack", "status": "na", "detail": "w"},
            ],
        },
    )

    titles = [action["title"] for action in actions["actions"]]
    # Exactly one configure-target action; agent-dependent na checks add none.
    assert titles == ["Configure an evaluation target when ready"]


def test_cockpit_html_agentless_not_blanket_no_go(tmp_path: Path):
    _write_observability_only_workspace(tmp_path)

    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    # The workspace is labelled project-observability-only, not NO-GO.
    assert "Project observability only" in html
