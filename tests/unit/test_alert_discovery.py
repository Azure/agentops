"""Acceptance tests for read-only Azure Monitor alert-rule discovery.

Every Azure SDK boundary is mocked; these tests never require Azure
credentials and never hit the network. They cover the eight scenarios called
out in issue #455 Part A: ready, no-recent-signal, not-configured,
rule-disabled, wrong-scope, missing-action-group, inaccessible -> cannot_verify,
and IaC-only -> must NOT be ready.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional, Sequence

import pytest

from agentops.utils import alert_discovery as ad

TARGET = (
    "/subscriptions/sub1/resourceGroups/rg1/providers/"
    "Microsoft.Insights/components/appi1"
)
OTHER = (
    "/subscriptions/sub1/resourceGroups/rg1/providers/"
    "Microsoft.Insights/components/other-appi"
)
ENDPOINT = "https://foundry.example.com/api/projects/proj"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ad.reset_cache()
    yield
    ad.reset_cache()


# --------------------------------------------------------------------------- #
# Fake Azure SDK rule objects.
# --------------------------------------------------------------------------- #
def _metric_alert(
    name: str,
    *,
    scope: str = TARGET,
    enabled: bool = True,
    with_condition: bool = True,
    action_group_ids: Sequence[str] = ("/ag/one",),
    metric_name: str = "requests/failed",
) -> SimpleNamespace:
    criteria = SimpleNamespace(
        all_of=(
            [SimpleNamespace(metric_name=metric_name, name=name, threshold=5.0)]
            if with_condition
            else []
        )
    )
    return SimpleNamespace(
        id=f"/rules/{name}",
        name=name,
        enabled=enabled,
        scopes=[scope],
        criteria=criteria,
        actions=[SimpleNamespace(action_group_id=ag) for ag in action_group_ids],
        evaluation_frequency="PT5M",
        window_size="PT15M",
    )


def _scheduled_rule(
    name: str,
    *,
    scope: str = TARGET,
    enabled: bool = True,
    query: str = "AppRequests | where Success == false",
    action_group_ids: Sequence[str] = ("/ag/two",),
) -> SimpleNamespace:
    criteria = SimpleNamespace(
        all_of=[SimpleNamespace(query=query, threshold=3.0)]
    )
    return SimpleNamespace(
        id=f"/rules/{name}",
        name=name,
        enabled=enabled,
        scopes=[scope],
        criteria=criteria,
        actions=SimpleNamespace(action_groups=list(action_group_ids)),
        evaluation_frequency="PT5M",
        window_size="PT15M",
    )


def _patch_azure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resource_id: Optional[str],
    reason: Optional[str],
    metric_rules: Optional[List[SimpleNamespace]] = None,
    scheduled_rules: Optional[List[SimpleNamespace]] = None,
    build_error: Optional[Exception] = None,
) -> None:
    monkeypatch.setattr(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_resource_id_with_reason",
        lambda endpoint: (resource_id, reason),
    )

    def _fake_build(subscription_id: str):
        if build_error is not None:
            raise build_error
        return SimpleNamespace(subscription_id=subscription_id)

    monkeypatch.setattr(ad, "_build_monitor_client", _fake_build)
    monkeypatch.setattr(
        ad, "_list_metric_alerts", lambda client, rg: list(metric_rules or [])
    )
    monkeypatch.setattr(
        ad,
        "_list_scheduled_query_rules",
        lambda client, rg: list(scheduled_rules or []),
    )


# --------------------------------------------------------------------------- #
# 1. Ready.
# --------------------------------------------------------------------------- #
def test_ready_when_enabled_scoped_rule_with_action_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        metric_rules=[_metric_alert("failed-requests")],
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_READY
    assert coverage.target_resource_id == TARGET
    assert len(coverage.rules) == 1
    assert coverage.rules[0].is_healthy is True
    # errors category is covered by a failed-requests rule.
    assert coverage.by_category["errors"] == "covered"


# --------------------------------------------------------------------------- #
# 2. No recent signal.
# --------------------------------------------------------------------------- #
def test_no_recent_signal_when_healthy_but_signal_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        metric_rules=[_metric_alert("failed-requests")],
    )
    coverage = ad.discover_alert_coverage(ENDPOINT, recent_signal=False)
    assert coverage.state == ad.STATE_NO_RECENT_SIGNAL
    assert coverage.reason


# --------------------------------------------------------------------------- #
# 3. Not configured.
# --------------------------------------------------------------------------- #
def test_not_configured_when_no_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(monkeypatch, resource_id=TARGET, reason=None)
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_NOT_CONFIGURED
    assert coverage.rules == ()


# --------------------------------------------------------------------------- #
# 4. Rule disabled -> misconfigured.
# --------------------------------------------------------------------------- #
def test_misconfigured_when_rule_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        metric_rules=[_metric_alert("failed-requests", enabled=False)],
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_MISCONFIGURED
    assert "disabled" in (coverage.reason or "")
    assert coverage.rules[0].is_healthy is False


# --------------------------------------------------------------------------- #
# 5. Wrong scope -> not configured (the rule targets a different resource).
# --------------------------------------------------------------------------- #
def test_wrong_scope_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        metric_rules=[_metric_alert("failed-requests", scope=OTHER)],
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_NOT_CONFIGURED
    # The rule is still surfaced (for context) but is not scoped to target.
    assert coverage.rules[0].scoped_to_target is False


# --------------------------------------------------------------------------- #
# 6. Missing action group -> misconfigured.
# --------------------------------------------------------------------------- #
def test_missing_action_group_is_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        metric_rules=[_metric_alert("failed-requests", action_group_ids=())],
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_MISCONFIGURED
    assert "action group" in (coverage.reason or "")
    assert coverage.rules[0].action_group_count == 0


# --------------------------------------------------------------------------- #
# 7. Inaccessible -> cannot_verify (never reported as absence).
# --------------------------------------------------------------------------- #
def test_unresolved_appinsights_is_cannot_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=None,
        reason="insufficient RBAC to read connection metadata",
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_CANNOT_VERIFY
    assert "RBAC" in (coverage.reason or "")


def test_monitor_client_error_is_cannot_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        build_error=RuntimeError("boom"),
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_CANNOT_VERIFY


def test_missing_sdk_is_cannot_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(
        monkeypatch,
        resource_id=TARGET,
        reason=None,
        build_error=ImportError("azure-mgmt-monitor missing"),
    )
    coverage = ad.discover_alert_coverage(ENDPOINT)
    assert coverage.state == ad.STATE_CANNOT_VERIFY
    assert "azure-mgmt-monitor" in (coverage.reason or "")


# --------------------------------------------------------------------------- #
# 8. IaC-only -> must NOT be ready.
# --------------------------------------------------------------------------- #
def test_iac_provenance_alone_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_azure(monkeypatch, resource_id=TARGET, reason=None)
    coverage = ad.discover_alert_coverage(
        ENDPOINT,
        iac_provenance=("infra/alerts.bicep",),
    )
    assert coverage.state != ad.STATE_READY
    assert coverage.state == ad.STATE_NOT_CONFIGURED
    # Provenance is preserved for context only.
    assert coverage.iac_provenance == ("infra/alerts.bicep",)


# --------------------------------------------------------------------------- #
# No endpoint -> not_applicable, and no Azure calls are attempted.
# --------------------------------------------------------------------------- #
def test_no_endpoint_is_not_applicable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("Azure must not be contacted without an endpoint")

    monkeypatch.setattr(ad, "_build_monitor_client", _boom)
    coverage = ad.discover_alert_coverage("", iac_provenance=("infra/x.bicep",))
    assert coverage.state == ad.STATE_NOT_APPLICABLE
    assert coverage.iac_provenance == ("infra/x.bicep",)


# --------------------------------------------------------------------------- #
# Pure state-machine and helper coverage.
# --------------------------------------------------------------------------- #
def test_parse_resource_id_roundtrip() -> None:
    parsed = ad.parse_resource_id(TARGET)
    assert parsed == ("sub1", "rg1", "appi1")
    assert ad.parse_resource_id("not-an-arm-id") is None


def test_scheduled_query_rule_snapshot_classifies_and_scopes() -> None:
    rule = _scheduled_rule(
        "groundedness-drift",
        query="AppTraces | where groundedness < 3",
    )
    snap = ad._scheduled_query_rule_snapshot(rule, TARGET)
    assert snap.rule_type == "scheduledQueryRule"
    assert snap.scoped_to_target is True
    assert snap.action_group_count == 1
    assert "quality" in snap.signal_categories
    assert snap.is_healthy is True


def test_evaluate_coverage_orders_rules_deterministically() -> None:
    a = ad._metric_alert_snapshot(_metric_alert("zzz"), TARGET)
    b = ad._metric_alert_snapshot(_metric_alert("aaa"), TARGET)
    coverage = ad.evaluate_coverage(
        [a, b], target_resource_id=TARGET, recent_signal=None
    )
    assert [r.name for r in coverage.rules] == ["aaa", "zzz"]


def test_cache_reused_for_repeated_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _counting_collect(client, rg, target):
        calls["n"] += 1
        return [ad._metric_alert_snapshot(_metric_alert("failed"), TARGET)]

    monkeypatch.setattr(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_resource_id_with_reason",
        lambda endpoint: (TARGET, None),
    )
    monkeypatch.setattr(
        ad, "_build_monitor_client", lambda sub: SimpleNamespace()
    )
    monkeypatch.setattr(ad, "_collect_rule_snapshots", _counting_collect)

    first = ad.discover_alert_coverage(ENDPOINT)
    second = ad.discover_alert_coverage(ENDPOINT)
    assert first.state == ad.STATE_READY
    assert second.state == ad.STATE_READY
    assert calls["n"] == 1  # second call served from cache
