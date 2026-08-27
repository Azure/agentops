"""Read-only discovery of Azure Monitor alert rules for a Foundry agent.

This module answers a single question for the cockpit "Alerts wired"
readiness card and (optionally) Doctor: *does the Application Insights
resource attached to this Foundry project actually have Azure Monitor
alert rules deployed, enabled, correctly scoped, and wired to an action
group?*

Design contract
---------------
* **Read-only.** It only *lists* and *reads* alert-rule metadata. It never
  creates, mutates, deletes, or tests an alert rule or action group.
* **No secrets.** Action-group receiver addresses, phone numbers, and webhook
  URLs are never read or emitted - only the *count* of attached action groups
  and whether at least one is present.
* **Best-effort and never raises into callers.** A missing SDK, an auth
  failure, or an unexpected response shape resolves to ``cannot_verify`` -
  which explicitly means "could not check", not "not configured".
* **Deterministic.** Rules are sorted and categories are ordered so tests are
  reproducible.
* **Lazy Azure imports.** Every Azure SDK import happens inside a function so
  importing this module never requires the ``[cockpit]`` extra.

The heavy Foundry lookup (project endpoint -> App Insights ARM id) is delegated
to :func:`agentops.utils.foundry_discovery.resolve_appinsights_resource_id_with_reason`,
which is itself cached. This module adds a short-lived cache over the Azure
Monitor listing so repeated cockpit renders stay cheap.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

# Coverage states. Cockpit maps these to card statuses:
#   ready            -> ok
#   no_recent_signal -> info
#   not_configured   -> warn
#   misconfigured    -> warn
#   cannot_verify    -> cannot_verify
#   not_applicable   -> info / hidden
STATE_READY = "ready"
STATE_NO_RECENT_SIGNAL = "no_recent_signal"
STATE_NOT_CONFIGURED = "not_configured"
STATE_MISCONFIGURED = "misconfigured"
STATE_CANNOT_VERIFY = "cannot_verify"
STATE_NOT_APPLICABLE = "not_applicable"

# Signal categories, in deterministic display order.
CATEGORY_QUALITY = "quality"
CATEGORY_SAFETY = "safety"
CATEGORY_ERRORS = "errors"
CATEGORY_LATENCY = "latency"
_CATEGORY_ORDER: Tuple[str, ...] = (
    CATEGORY_QUALITY,
    CATEGORY_SAFETY,
    CATEGORY_ERRORS,
    CATEGORY_LATENCY,
)

# Keyword tables used to classify a rule's signal category from its name and
# the metric/query text it evaluates. Ordered longest-first only matters for
# readability; membership is a plain substring test.
_CATEGORY_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        CATEGORY_SAFETY,
        (
            "content_safety",
            "contentsafety",
            "self_harm",
            "selfharm",
            "self harm",
            "hate_unfairness",
            "hateunfairness",
            "protected_material",
            "protectedmaterial",
            "jailbreak",
            "violence",
            "sexual",
            "unfairness",
            "toxicity",
            "safety",
        ),
    ),
    (
        CATEGORY_LATENCY,
        (
            "response_time",
            "responsetime",
            "duration",
            "latency",
            "p95",
            "p99",
            "slow",
            "timeout",
            "elapsed",
        ),
    ),
    (
        CATEGORY_ERRORS,
        (
            "failed_requests",
            "failedrequests",
            "failure",
            "failed",
            "5xx",
            "server_error",
            "servererror",
            "exception",
            "dependency",
            "availability",
            "error",
        ),
    ),
    (
        CATEGORY_QUALITY,
        (
            "groundedness",
            "coherence",
            "fluency",
            "similarity",
            "relevance",
            "completeness",
            "retrieval",
            "rubric",
            "quality",
            "drift",
            "score",
            "eval",
        ),
    ),
)


@dataclass(frozen=True)
class AlertRuleSnapshot:
    """A single Azure Monitor alert rule, reduced to non-sensitive facts."""

    name: str
    rule_type: str  # "metricAlert" | "scheduledQueryRule"
    resource_id: str
    enabled: bool
    scopes: Tuple[str, ...]
    signal_categories: Tuple[str, ...]  # subset of _CATEGORY_ORDER
    evaluation_frequency: Optional[str]
    window_size: Optional[str]
    has_condition: bool
    threshold: Optional[float]
    action_group_count: int  # COUNT ONLY - never receiver addresses
    action_groups_enabled: bool
    scoped_to_target: bool
    problems: Tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        """A rule that is enabled, scoped to the target, has a firing
        condition, and can notify at least one action group."""
        return (
            self.enabled
            and self.scoped_to_target
            and self.has_condition
            and self.action_group_count > 0
            and self.action_groups_enabled
        )


@dataclass(frozen=True)
class AlertCoverage:
    """Overall alerting posture for one Foundry-linked App Insights resource."""

    state: str
    reason: Optional[str]
    rules: Tuple[AlertRuleSnapshot, ...]
    by_category: Mapping[str, str]  # category -> "covered" | "gap"
    iac_provenance: Tuple[str, ...]  # informational only, never proof
    target_resource_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Per-process cache (short-lived) so repeated cockpit renders do not re-list
# Azure Monitor rules. Cleared by :func:`reset_cache`.
# --------------------------------------------------------------------------- #
_SUCCESS_TTL_SECONDS = 30 * 60
_FAILURE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_coverage_cache: dict[str, Tuple[float, AlertCoverage]] = {}


def _store_coverage(key: str, coverage: AlertCoverage) -> None:
    with _cache_lock:
        _coverage_cache[key] = (time.time(), coverage)


def _lookup_coverage(key: str) -> Optional[AlertCoverage]:
    with _cache_lock:
        entry = _coverage_cache.get(key)
    if entry is None:
        return None
    ts, coverage = entry
    ttl = (
        _FAILURE_TTL_SECONDS
        if coverage.state == STATE_CANNOT_VERIFY
        else _SUCCESS_TTL_SECONDS
    )
    if time.time() - ts > ttl:
        return None
    return coverage


def reset_cache() -> None:
    """Clear the per-process alert-coverage cache (test helper)."""
    with _cache_lock:
        _coverage_cache.clear()


# --------------------------------------------------------------------------- #
# Small pure helpers (fully unit-testable without Azure).
# --------------------------------------------------------------------------- #
def _as_bool(value: Any, *, default: bool = True) -> bool:
    """Normalize the many enabled representations across SDK versions."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "enabled"}:
        return True
    if text in {"false", "0", "no", "disabled"}:
        return False
    return default


def _classify_signal_categories(*texts: Optional[str]) -> Tuple[str, ...]:
    """Return the ordered signal categories implied by the given text blobs."""
    blob = " ".join(t for t in texts if t).lower()
    found: List[str] = []
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in blob for keyword in keywords):
            found.append(category)
    # Return in the canonical order regardless of match order.
    return tuple(c for c in _CATEGORY_ORDER if c in found)


def parse_resource_id(resource_id: str) -> Optional[Tuple[str, str, str]]:
    """Return ``(subscription_id, resource_group, component_name)``.

    Parses an App Insights component ARM id of the form
    ``/subscriptions/{sub}/resourceGroups/{rg}/providers/
    Microsoft.Insights/components/{name}``. Returns ``None`` when the id does
    not match that shape.
    """
    if not resource_id:
        return None
    pattern = (
        r"^/subscriptions/(?P<sub>[^/]+)"
        r"/resourceGroups/(?P<rg>[^/]+)"
        r"/providers/[Mm]icrosoft\.[Ii]nsights/components/(?P<name>[^/]+)$"
    )
    match = re.match(pattern, resource_id.strip())
    if not match:
        return None
    return match.group("sub"), match.group("rg"), match.group("name")


def _scopes_match(scopes: Sequence[str], target_resource_id: str) -> bool:
    """Whether any scope refers to the target App Insights resource."""
    target = target_resource_id.strip().lower()
    for scope in scopes:
        text = str(scope or "").strip().lower()
        if not text:
            continue
        if text == target or target in text:
            return True
    return False


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_alert_snapshot(
    rule: Any, target_resource_id: str
) -> AlertRuleSnapshot:
    name = str(getattr(rule, "name", "") or "")
    resource_id = str(getattr(rule, "id", "") or "")
    enabled = _as_bool(getattr(rule, "enabled", None), default=True)
    scopes = tuple(
        str(s) for s in (getattr(rule, "scopes", None) or []) if s is not None
    )

    criteria = getattr(rule, "criteria", None)
    all_of = list(getattr(criteria, "all_of", None) or [])
    has_condition = bool(all_of)
    threshold: Optional[float] = None
    metric_texts: List[str] = []
    for condition in all_of:
        metric_texts.append(str(getattr(condition, "metric_name", "") or ""))
        metric_texts.append(str(getattr(condition, "name", "") or ""))
        if threshold is None:
            threshold = _coerce_float(getattr(condition, "threshold", None))

    actions = list(getattr(rule, "actions", None) or [])
    action_group_ids = [
        str(getattr(a, "action_group_id", "") or "")
        for a in actions
        if getattr(a, "action_group_id", None)
    ]
    action_group_count = len(action_group_ids)

    scoped = _scopes_match(scopes, target_resource_id)
    problems = _rule_problems(
        enabled=enabled,
        scoped=scoped,
        has_condition=has_condition,
        action_group_count=action_group_count,
    )
    return AlertRuleSnapshot(
        name=name,
        rule_type="metricAlert",
        resource_id=resource_id,
        enabled=enabled,
        scopes=scopes,
        signal_categories=_classify_signal_categories(name, *metric_texts),
        evaluation_frequency=_iso_or_none(
            getattr(rule, "evaluation_frequency", None)
        ),
        window_size=_iso_or_none(getattr(rule, "window_size", None)),
        has_condition=has_condition,
        threshold=threshold,
        action_group_count=action_group_count,
        action_groups_enabled=action_group_count > 0,
        scoped_to_target=scoped,
        problems=problems,
    )


def _scheduled_query_rule_snapshot(
    rule: Any, target_resource_id: str
) -> AlertRuleSnapshot:
    name = str(getattr(rule, "name", "") or "")
    resource_id = str(getattr(rule, "id", "") or "")
    enabled = _as_bool(
        getattr(rule, "enabled", getattr(rule, "is_enabled", None)),
        default=True,
    )
    scopes = tuple(
        str(s) for s in (getattr(rule, "scopes", None) or []) if s is not None
    )
    # Older 2018-04-16 rules embed the resource under source.data_source_id.
    if not scopes:
        source = getattr(rule, "source", None)
        legacy = getattr(source, "data_source_id", None)
        if legacy:
            scopes = (str(legacy),)

    criteria = getattr(rule, "criteria", None)
    all_of = list(getattr(criteria, "all_of", None) or [])
    query_texts: List[str] = []
    threshold: Optional[float] = None
    for condition in all_of:
        query_texts.append(str(getattr(condition, "query", "") or ""))
        query_texts.append(
            str(getattr(condition, "metric_measure_column", "") or "")
        )
        if threshold is None:
            threshold = _coerce_float(getattr(condition, "threshold", None))
    # Legacy shape: source.query + trigger.threshold.
    source = getattr(rule, "source", None)
    legacy_query = getattr(source, "query", None)
    if legacy_query:
        query_texts.append(str(legacy_query))
    has_condition = bool(all_of) or bool(legacy_query)

    action_group_ids = _scheduled_action_group_ids(rule)
    action_group_count = len(action_group_ids)

    scoped = _scopes_match(scopes, target_resource_id)
    problems = _rule_problems(
        enabled=enabled,
        scoped=scoped,
        has_condition=has_condition,
        action_group_count=action_group_count,
    )
    return AlertRuleSnapshot(
        name=name,
        rule_type="scheduledQueryRule",
        resource_id=resource_id,
        enabled=enabled,
        scopes=scopes,
        signal_categories=_classify_signal_categories(name, *query_texts),
        evaluation_frequency=_iso_or_none(
            getattr(rule, "evaluation_frequency", None)
        ),
        window_size=_iso_or_none(getattr(rule, "window_size", None)),
        has_condition=has_condition,
        threshold=threshold,
        action_group_count=action_group_count,
        action_groups_enabled=action_group_count > 0,
        scoped_to_target=scoped,
        problems=problems,
    )


def _scheduled_action_group_ids(rule: Any) -> List[str]:
    """Extract action-group ids across scheduled-query-rule SDK shapes."""
    ids: List[str] = []
    actions = getattr(rule, "actions", None)
    # 2021-08-01: actions.action_groups -> list[str].
    action_groups = getattr(actions, "action_groups", None)
    if action_groups:
        ids.extend(str(g) for g in action_groups if g)
    # 2018-04-16: action.action_group -> list[str].
    action = getattr(rule, "action", None)
    legacy_groups = getattr(action, "action_group", None)
    if legacy_groups:
        ids.extend(str(g) for g in legacy_groups if g)
    return [i for i in ids if i]


def _rule_problems(
    *,
    enabled: bool,
    scoped: bool,
    has_condition: bool,
    action_group_count: int,
) -> Tuple[str, ...]:
    problems: List[str] = []
    if not enabled:
        problems.append("rule is disabled")
    if not scoped:
        problems.append("rule is not scoped to the Foundry App Insights resource")
    if not has_condition:
        problems.append("rule has no firing condition")
    if action_group_count == 0:
        problems.append("rule has no action group to notify")
    return tuple(problems)


def _iso_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_by_category(
    healthy_rules: Sequence[AlertRuleSnapshot],
) -> Mapping[str, str]:
    covered: set[str] = set()
    for rule in healthy_rules:
        covered.update(rule.signal_categories)
    return {
        category: ("covered" if category in covered else "gap")
        for category in _CATEGORY_ORDER
    }


def _sort_rules(
    rules: Iterable[AlertRuleSnapshot],
) -> Tuple[AlertRuleSnapshot, ...]:
    return tuple(sorted(rules, key=lambda r: (r.rule_type, r.name, r.resource_id)))


def evaluate_coverage(
    rules: Sequence[AlertRuleSnapshot],
    *,
    target_resource_id: Optional[str],
    recent_signal: Optional[bool],
    reason: Optional[str] = None,
    iac_provenance: Sequence[str] = (),
) -> AlertCoverage:
    """Pure state machine over already-collected rule snapshots.

    Separated from Azure I/O so every branch is trivially unit-testable.
    """
    ordered = _sort_rules(rules)
    relevant = [r for r in ordered if r.scoped_to_target]
    healthy = [r for r in relevant if r.is_healthy]
    provenance = tuple(dict.fromkeys(str(p) for p in iac_provenance if p))
    resolved_reason: Optional[str]

    if healthy:
        if recent_signal is False:
            state = STATE_NO_RECENT_SIGNAL
            resolved_reason = reason or (
                "Alert rules are configured and enabled, but no production "
                "telemetry has arrived in the lookback window to confirm they "
                "are receiving signal."
            )
        else:
            state = STATE_READY
            resolved_reason = reason
    elif relevant:
        state = STATE_MISCONFIGURED
        resolved_reason = reason or (
            "Alert rules target this resource but none are ready: "
            + "; ".join(
                sorted(
                    {problem for r in relevant for problem in r.problems}
                )
            )
            + "."
        )
    else:
        state = STATE_NOT_CONFIGURED
        resolved_reason = reason or (
            "No Azure Monitor alert rule is scoped to the Foundry-linked "
            "Application Insights resource."
        )

    return AlertCoverage(
        state=state,
        reason=resolved_reason,
        rules=ordered,
        by_category=_build_by_category(healthy),
        iac_provenance=provenance,
        target_resource_id=target_resource_id,
    )


# --------------------------------------------------------------------------- #
# Azure I/O (lazy, best-effort). Everything below is mocked in tests.
# --------------------------------------------------------------------------- #
def _build_monitor_client(subscription_id: str) -> Any:
    """Build a read-only MonitorManagementClient. Lazy SDK import."""
    from agentops.agent.sources._credentials import get_shared_credential
    from azure.mgmt.monitor import MonitorManagementClient

    credential = get_shared_credential(
        exclude_developer_cli_credential=True,
        process_timeout=30,
    )
    return MonitorManagementClient(credential, subscription_id)


def _list_metric_alerts(
    client: Any, resource_group: str
) -> List[Any]:
    """List metric alert rules, preferring resource-group scope."""
    metric_alerts = getattr(client, "metric_alerts", None)
    if metric_alerts is None:
        return []
    list_by_rg = getattr(metric_alerts, "list_by_resource_group", None)
    if callable(list_by_rg):
        return list(list_by_rg(resource_group))
    list_by_sub = getattr(metric_alerts, "list_by_subscription", None)
    if callable(list_by_sub):
        return list(list_by_sub())
    return []


def _list_scheduled_query_rules(
    client: Any, resource_group: str
) -> List[Any]:
    """List scheduled-query alert rules, preferring resource-group scope."""
    rules = getattr(client, "scheduled_query_rules", None)
    if rules is None:
        return []
    list_by_rg = getattr(rules, "list_by_resource_group", None)
    if callable(list_by_rg):
        return list(list_by_rg(resource_group))
    list_by_sub = getattr(rules, "list_by_subscription", None)
    if callable(list_by_sub):
        return list(list_by_sub())
    return []


def _collect_rule_snapshots(
    client: Any, resource_group: str, target_resource_id: str
) -> List[AlertRuleSnapshot]:
    snapshots: List[AlertRuleSnapshot] = []
    for rule in _list_metric_alerts(client, resource_group):
        snapshots.append(_metric_alert_snapshot(rule, target_resource_id))
    for rule in _list_scheduled_query_rules(client, resource_group):
        snapshots.append(
            _scheduled_query_rule_snapshot(rule, target_resource_id)
        )
    return snapshots


def _cannot_verify(
    reason: str,
    *,
    target_resource_id: Optional[str],
    iac_provenance: Sequence[str],
) -> AlertCoverage:
    return AlertCoverage(
        state=STATE_CANNOT_VERIFY,
        reason=reason,
        rules=(),
        by_category={category: "gap" for category in _CATEGORY_ORDER},
        iac_provenance=tuple(dict.fromkeys(str(p) for p in iac_provenance if p)),
        target_resource_id=target_resource_id,
    )


def _not_applicable(
    reason: str,
    *,
    iac_provenance: Sequence[str],
) -> AlertCoverage:
    return AlertCoverage(
        state=STATE_NOT_APPLICABLE,
        reason=reason,
        rules=(),
        by_category={category: "gap" for category in _CATEGORY_ORDER},
        iac_provenance=tuple(dict.fromkeys(str(p) for p in iac_provenance if p)),
        target_resource_id=None,
    )


def _discover_azure_alert_coverage(
    project_endpoint: Optional[str],
    *,
    lookback_days: int,
    recent_signal: Optional[bool],
) -> AlertCoverage:
    if not project_endpoint:
        return _not_applicable(
            "No Foundry project endpoint is configured, so Azure Monitor alert "
            "rules cannot be inventoried.",
            iac_provenance=(),
        )

    cache_key = f"{project_endpoint}::{lookback_days}"
    if recent_signal is None:
        cached = _lookup_coverage(cache_key)
        if cached is not None:
            return cached

    from agentops.utils.foundry_discovery import (
        _summarize_discovery_exception,
        resolve_appinsights_resource_id_with_reason,
    )

    resource_id, reason = resolve_appinsights_resource_id_with_reason(
        project_endpoint
    )
    if not resource_id:
        # We could not resolve the App Insights resource. This is "could not
        # check", never "not configured".
        coverage = _cannot_verify(
            reason
            or "Could not resolve the Foundry-linked Application Insights "
            "resource id.",
            target_resource_id=None,
            iac_provenance=(),
        )
        if recent_signal is None:
            _store_coverage(cache_key, coverage)
        return coverage

    parsed = parse_resource_id(resource_id)
    if parsed is None:
        coverage = _cannot_verify(
            "The Foundry-linked Application Insights resource id is not a "
            "recognized ARM component id; cannot inventory alert rules.",
            target_resource_id=resource_id,
            iac_provenance=(),
        )
        if recent_signal is None:
            _store_coverage(cache_key, coverage)
        return coverage

    subscription_id, resource_group, _name = parsed

    try:
        client = _build_monitor_client(subscription_id)
        snapshots = _collect_rule_snapshots(
            client, resource_group, resource_id
        )
    except ImportError:
        coverage = _cannot_verify(
            "azure-mgmt-monitor is not installed in the cockpit's Python "
            "environment. Install the cockpit extra with "
            "`pip install 'agentops[cockpit]'`.",
            target_resource_id=resource_id,
            iac_provenance=(),
        )
        if recent_signal is None:
            _store_coverage(cache_key, coverage)
        return coverage
    except Exception as exc:  # noqa: BLE001
        coverage = _cannot_verify(
            _summarize_discovery_exception(
                exc, context="Azure Monitor alert-rule inventory"
            ),
            target_resource_id=resource_id,
            iac_provenance=(),
        )
        if recent_signal is None:
            _store_coverage(cache_key, coverage)
        return coverage

    coverage = evaluate_coverage(
        snapshots,
        target_resource_id=resource_id,
        recent_signal=recent_signal,
    )
    if recent_signal is None:
        _store_coverage(cache_key, coverage)
    return coverage


def discover_alert_coverage(
    project_endpoint: Optional[str],
    *,
    lookback_days: int = 7,
    recent_signal: Optional[bool] = None,
    iac_provenance: Sequence[str] = (),
) -> AlertCoverage:
    """Inventory Azure Monitor alert rules for a Foundry project's telemetry.

    Parameters
    ----------
    project_endpoint:
        The Foundry project endpoint. When falsy, the result is
        ``not_applicable`` with no Azure calls.
    lookback_days:
        Window used for the (optional) recent-signal correlation. Only affects
        the ``ready`` vs ``no_recent_signal`` split.
    recent_signal:
        ``True`` when production telemetry was seen recently, ``False`` when it
        was verified absent, ``None`` when the caller does not correlate. When
        ``None`` the healthy state resolves to ``ready`` (the rule itself is
        verified) and results are cached. When set, the cache is bypassed so
        callers can inject deterministic signal in tests.
    iac_provenance:
        Optional file paths where alert rules are declared as infrastructure.
        Echoed back as *provenance only* - never treated as proof of a
        deployed rule.

    Returns
    -------
    AlertCoverage
        A read-only summary with a stable ``state`` and non-sensitive rule
        snapshots.
    """
    coverage = _discover_azure_alert_coverage(
        project_endpoint,
        lookback_days=lookback_days,
        recent_signal=recent_signal,
    )
    provenance = tuple(dict.fromkeys(str(p) for p in iac_provenance if p))
    if provenance and not coverage.iac_provenance:
        coverage = replace(coverage, iac_provenance=provenance)
    return coverage


__all__ = [
    "AlertRuleSnapshot",
    "AlertCoverage",
    "discover_alert_coverage",
    "evaluate_coverage",
    "parse_resource_id",
    "reset_cache",
    "STATE_READY",
    "STATE_NO_RECENT_SIGNAL",
    "STATE_NOT_CONFIGURED",
    "STATE_MISCONFIGURED",
    "STATE_CANNOT_VERIFY",
    "STATE_NOT_APPLICABLE",
]
