"""Tests for :mod:`agentops.agent.cockpit`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentops.agent.cockpit import (
    build_cockpit_payload,
    render_cockpit_html,
)
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.history import append_analysis, build_record
from agentops.agent.time_range import TimeRange


# Tests run against a wide time range so the cockpit filter does not
# accidentally exclude fixture runs based on the test wall clock.
_WIDE = TimeRange(
    key="custom",
    label="test-window",
    start=datetime(2000, 1, 1, tzinfo=timezone.utc),
    end=datetime(2100, 1, 1, tzinfo=timezone.utc),
    hours=24 * 365 * 100,
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
    assert payload["eval"]["has_runs"] is False
    assert payload["metrics"] == []
    assert payload["watchdog"]["has_history"] is False
    html = render_cockpit_html(payload)
    assert "No eval runs yet" in html
    assert "No analysis history yet" in html
    assert "agentops eval run" in html


def test_cockpit_loads_eval_runs(tmp_path: Path):
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-11T20-00-00Z",
        passed=True,
        metrics={"coherence": 4.5, "similarity": 4.0, "fluency": 3.7, "f1_score": 0.9},
    )
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-11T21-00-00Z",
        passed=False,
        metrics={"coherence": 4.0, "similarity": 3.0, "fluency": 3.0, "f1_score": 0.6},
        target="agent-smoke:3",
    )

    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    assert payload["eval"]["has_runs"] is True
    eval_keys = {c["key"] for c in payload["eval"]["cards"]}
    assert "total_runs" in eval_keys
    assert "pass_rate" in eval_keys
    assert "latest_run" in eval_keys
    # Latest target wins.
    latest_card = next(c for c in payload["eval"]["cards"] if c["key"] == "latest_run")
    assert latest_card["value"] == "agent-smoke:3"
    assert latest_card["badge"]["tone"] == "crit"

    metric_keys = {c["key"] for c in payload["metrics"]}
    assert {"coherence", "similarity", "fluency", "f1_score"} <= metric_keys


def test_pass_rate_badge_reflects_history(tmp_path: Path):
    for i in range(4):
        _write_eval_run(
            tmp_path,
            timestamp_dir=f"2026-05-11T0{i}-00-00Z",
            passed=True,
            metrics={"coherence": 4.0},
        )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    pass_card = next(c for c in payload["eval"]["cards"] if c["key"] == "pass_rate")
    assert pass_card["value"] == "100%"
    assert pass_card["badge"]["tone"] == "ok"


def test_metric_trend_badge_detects_regression_for_quality(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0},
    )
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T02-00-00Z", passed=True,
        metrics={"coherence": 3.0},
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    coh = next(c for c in payload["metrics"] if c["key"] == "coherence")
    assert coh["badge"]["label"] == "regressed"
    assert coh["badge"]["tone"] == "warn"


def test_metric_trend_badge_treats_latency_inversely(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"avg_latency_seconds": 5.0},
    )
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T02-00-00Z", passed=True,
        metrics={"avg_latency_seconds": 2.0},
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    lat = next(c for c in payload["metrics"] if c["key"] == "avg_latency_seconds")
    # Latency dropping is an improvement, not a regression.
    assert lat["badge"]["label"] == "improved"
    assert lat["badge"]["tone"] == "ok"


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


def test_html_includes_all_sections_when_data_present(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0, "fluency": 4.0},
    )
    _make_history(tmp_path, (Severity.INFO, Category.QUALITY))
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    # New strategic sections.
    assert "Foundry connection" in html
    assert "Foundry launchpad" in html
    assert "Azure Monitor" in html
    assert "Observability readiness" in html
    assert "Next actions" in html
    # Renamed sections.
    assert "Eval runs" in html
    assert "AgentOps Doctor" in html
    # Existing sections.
    assert "CI/CD" in html
    assert "Quality metrics" in html
    # Production telemetry is now rendered as a deliberate teaser into
    # Foundry Monitor; the section title is "Production preview".
    assert "Production preview" in html
    assert "Open Foundry Monitor" in html
    assert "<svg" in html

    # Sections render in the strategic order: Foundry connection →
    # Foundry launchpad → Observability readiness →
    # AgentOps Doctor → Eval runs → Quality metrics → Production
    # preview → CI/CD → Next actions.
    connection_pos = html.find('<span class="section-title-text">Foundry connection')
    open_pos = html.find('<span class="section-title-text">Foundry launchpad')
    readiness_pos = html.find('<span class="section-title-text">Observability readiness')
    doctor_pos = html.find('<span class="section-title-text">AgentOps Doctor')
    eval_pos = html.find('<span class="section-title-text">Eval runs')
    metrics_pos = html.find('<span class="section-title-text">Quality metrics')
    deploy_pos = html.find('<span class="section-title-text">CI/CD')
    actions_pos = html.find('<span class="section-title-text">Next actions')
    for pos in (
        connection_pos, open_pos, readiness_pos, doctor_pos,
        eval_pos, metrics_pos, deploy_pos, actions_pos,
    ):
        assert pos != -1, "missing strategic section in cockpit HTML"
    assert (
        connection_pos < open_pos < readiness_pos < doctor_pos
        < eval_pos < metrics_pos < deploy_pos < actions_pos
    )
    assert '<details class="section-block" open' in html



def test_open_in_foundry_panel_separates_foundry_from_azure_monitor(tmp_path: Path):
    """The launchpad separates configured-agent, project, and raw telemetry links."""
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)

    open_panel = payload["open_in_foundry"]
    groups = open_panel.get("groups") or []
    keys = [g.get("key") for g in groups]
    assert keys == ["agent", "project", "azure_monitor"], (
        "Agent links must precede project links, which must precede Azure Monitor"
    )

    agent_titles = {t["title"] for t in groups[0]["targets"]}
    assert {"Agent build", "Monitor", "Traces"}.issubset(agent_titles)

    project_titles = {t["title"] for t in groups[1]["targets"]}
    assert {
        "Evaluations",
        "Datasets",
        "Red Teaming",
        "Operate overview",
    }.issubset(project_titles)
    # The Azure Monitor subgroup carries only the App Insights tile.
    azure_titles = {t["title"] for t in groups[2]["targets"]}
    assert azure_titles == {"App Insights"}

    html = render_cockpit_html(payload)
    # Subheaders render.
    assert ">Configured agent<" in html
    assert ">Foundry project<" in html
    assert ">Azure Monitor<" in html
    # The legacy flat ``targets`` key is kept for backwards-compat and
    # combines both groups in display order.
    flat_keys = [t["key"] for t in open_panel["targets"]]
    assert flat_keys[0] == "agent"
    assert flat_keys[-1] == "app_insights"


def test_readiness_splits_tracing_and_includes_continuous_eval(tmp_path: Path):
    """Readiness now lists separate server-side and client-side tracing
    rows plus a dedicated continuous-evaluation row sourced from the
    latest Doctor analysis."""
    from agentops.agent.cockpit import _build_readiness_checklist

    telemetry = {"enabled": True, "detail": "ok", "portal_url": "https://x"}
    deployments = {"has_data": False}

    # No Doctor history → continuous-eval row is muted, not silently
    # green. The cockpit must not pretend a feature is configured just
    # because Doctor was never run.
    readiness = _build_readiness_checklist(
        tmp_path, telemetry, deployments, watchdog=None,
    )
    titles = [c["title"] for c in readiness["checks"]]
    assert any("Server-side tracing" in t for t in titles)
    assert any("Client-side tracing" in t for t in titles)
    cont_row = next(
        c for c in readiness["checks"]
        if "Continuous evaluation rules" in c["title"]
    )
    assert cont_row["status"] == "muted"
    assert "agentops doctor" in cont_row["detail"]


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


def test_production_section_is_a_teaser_into_foundry_monitor(tmp_path: Path):
    """Production telemetry now ships as a 2-card teaser (error rate +
    P95 latency) with a prominent "Open Foundry Monitor" CTA. The
    cockpit deliberately delegates invocations and tokens to Foundry
    Monitor so AgentOps does not compete with the system of record."""
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    # Section title reflects the new "preview" framing.
    assert "Production preview" in html
    assert "Production telemetry" not in html
    # The skeleton placeholder for the deferred load shows only the
    # two surviving teaser cards.
    assert "Error rate" in html
    assert "P95 latency" in html
    assert "Invocations" not in html or "Invocations" in (
        # The token "Invocations" may still appear in unrelated copy
        # (e.g. App Insights description in the Foundry connection
        # card). Restrict the assertion to the production grid.
        ""
    )

    # The production grid renders exactly two skeleton placeholder cards
    # (Error rate / P95 latency). Invocations and tokens are intentionally
    # delegated to Foundry Monitor so AgentOps does not compete with it.
    # The ``skeleton-card`` class is unique to the deferred production
    # grid, so counting it across the full HTML is sufficient.
    assert html.count("skeleton-card") == 2


def test_production_section_links_to_foundry_monitor_first(tmp_path: Path):
    """The Production preview section must surface the Foundry Monitor
    deep-link as a primary CTA so users always know where the full
    runtime dashboard lives."""
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-11T01-00-00Z",
        passed=True,
        metrics={"coherence": 5.0},
        cloud_evaluation={
            "eval_id": "evl_x",
            "run_id": "run_x",
            "report_url": (
                "https://acct.services.ai.azure.com/api/projects/p/"
                "build/evaluations/evl_x"
            ),
        },
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)

    assert "Open Foundry Monitor" in html
    # The Foundry Monitor link is styled as the primary section CTA.
    assert "section-link-primary" in html



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
    # Range bar is present.
    assert "range-pills" in r.text
    assert 'range=1d' in r.text and 'range=7d' in r.text and 'range=30d' in r.text

    # Custom range round-trip (also returns shell unless _partial is set).
    r = client.get("/?range=custom&from=2020-01-01&to=2030-01-01&_partial=1")
    assert r.status_code == 200

    # Unknown range falls back to 7d default.
    r = client.get("/?range=eternity&_partial=1")
    assert r.status_code == 200

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


def test_doctor_section_has_no_foundry_control_plane_link(tmp_path):
    """The AgentOps Doctor surfaces *local* findings only — there is no
    "Foundry control plane" equivalent that mirrors them. The section
    header must not advertise an external link that would 404."""
    _make_history(tmp_path, (Severity.WARNING, Category.OPERATIONAL_EXCELLENCE))
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    assert "Open Foundry control plane" not in html


def test_tenant_card_source_moves_to_tooltip(tmp_path, monkeypatch):
    """The "(from az account show)" suffix used to live inline in the
    tenant card detail. It is now an ``(i)`` hover tooltip so the card
    body stays compact."""
    # Force the tenant detection to a known value without invoking az.
    monkeypatch.setattr(
        "agentops.agent.cockpit._az_tenant_id",
        lambda: "16b3c013-d300-468d-ac64-7eda0820b6d3",
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    # Inline source text is gone.
    assert "(from <code>az account show</code>)" not in html
    # Tooltip surfaces the same source via title= attribute.
    assert "Resolved from `az account show`." in html


def test_app_insights_card_source_moves_to_tooltip(
    tmp_path, monkeypatch
):
    """The "Connected via APPLICATIONINSIGHTS_CONNECTION_STRING" detail
    used to render inline. It is now an ``(i)`` hover tooltip on the
    App Insights card."""
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://example.in.applicationinsights.azure.com/",
    )
    payload = build_cockpit_payload(tmp_path, time_range=_WIDE)
    html = render_cockpit_html(payload)
    # The verbose inline message is gone.
    assert "Connected via <code>APPLICATIONINSIGHTS_CONNECTION_STRING</code>" not in html
    # The tooltip carries the env-var reference.
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in html
    assert "info-i" in html


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
