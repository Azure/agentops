"""Pure composition helpers for privacy-safe Observe attribution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionResolution,
    AttributionUsage,
    derive_pseudonymous_user_key,
    resolve_attribution,
)


class SingletonAttributionError(RuntimeError):
    """Signals that an aggregate result must be discarded and rerun delegated."""


def resolve_department(
    *,
    user_key: str,
    raw_identity: str | None,
    config: AttributionConfiguration,
    principal_user_id: str | None = None,
    principal_user_name: str | None = None,
    principal_group_ids: Sequence[str] = (),
) -> AttributionResolution:
    """Resolve a department without applying one principal's claims to another."""
    identity = raw_identity.strip() if isinstance(raw_identity, str) else None
    principal_identities = {
        value.strip()
        for value in (principal_user_id, principal_user_name)
        if isinstance(value, str) and value.strip()
    }
    return resolve_attribution(
        user_key,
        config,
        identity_matches_principal=identity is not None and identity in principal_identities,
        principal_group_ids=tuple(principal_group_ids),
    )


def config_with_principal_group_mappings(
    config: AttributionConfiguration,
    *,
    tenant_id: str,
    principal_user_id: str | None,
    principal_user_name: str | None,
    principal_group_ids: Sequence[str] = (),
) -> AttributionConfiguration:
    """Add group-derived keys only for the exact signed-in principal identities."""
    if config.deployment_namespace is None or config.generation is None:
        raise ValueError("principal group mappings require enabled attribution")
    deployment_namespace = config.deployment_namespace
    generation = config.generation
    additions: dict[str, list[str]] = {}
    identities = {
        value.strip()
        for value in (principal_user_id, principal_user_name)
        if isinstance(value, str) and value.strip()
    }
    for identity in identities:
        user_key = derive_pseudonymous_user_key(
            deployment_namespace=deployment_namespace,
            generation=generation,
            tenant_id=tenant_id,
            raw_identity=identity,
        )
        resolution = resolve_department(
            user_key=user_key,
            raw_identity=identity,
            config=config,
            principal_user_id=principal_user_id,
            principal_user_name=principal_user_name,
            principal_group_ids=principal_group_ids,
        )
        if resolution.source == "principal_group" and resolution.department_id:
            additions.setdefault(resolution.department_id, []).append(user_key)

    if not additions:
        return config
    departments = [
        department.model_copy(
            update={
                "user_keys": [
                    *department.user_keys,
                    *additions.get(department.id, ()),
                ]
            }
        )
        for department in config.departments
    ]
    return config.model_copy(update={"departments": departments})


def principal_alias_user_keys(
    config: AttributionConfiguration,
    *,
    tenant_id: str,
    principal_user_id: str | None,
    principal_user_name: str | None,
) -> tuple[str, ...]:
    """Derive the distinct keys for the validated current-principal aliases."""
    identities = {
        value.strip()
        for value in (principal_user_id, principal_user_name)
        if isinstance(value, str) and value.strip()
    }
    if (
        not config.enabled
        or config.deployment_namespace is None
        or config.generation is None
    ):
        return ()
    return tuple(
        sorted(
            derive_pseudonymous_user_key(
                deployment_namespace=config.deployment_namespace,
                generation=config.generation,
                tenant_id=tenant_id,
                raw_identity=identity,
            )
            for identity in identities
        )
    )


def classify_department_cardinality(
    rows: Sequence[Mapping[str, Any]],
    *,
    principal_aliases: Sequence[str] = (),
) -> bool:
    """Return whether all active identity-bearing partitions are aggregate-safe.

    Unknown cardinality is treated conservatively. Empty partitions are ignored;
    a partition containing exactly one active identified user is never safe.
    When query rows identify the current principal's presence, that principal is
    counted once across all aliases and source partitions for the department.
    """
    alias_keys = {value for value in principal_aliases if value}
    partitions: dict[Any, dict[str, Any]] = {}
    for row in rows:
        value = row.get("member_count")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        principal_present = row.get("principal_member_present", 0)
        if (
            isinstance(principal_present, bool)
            or not isinstance(principal_present, int)
            or principal_present not in (0, 1)
            or principal_present > value
        ):
            return False
        partition = row.get("department_id")
        state = partitions.setdefault(
            partition,
            {
                "identities": set(),
                "principal": False,
                "unknown_nonprincipal_max": 0,
            },
        )
        state["principal"] = state["principal"] or principal_present == 1

        member_keys = row.get("member_user_keys")
        if member_keys is None:
            state["unknown_nonprincipal_max"] = max(
                state["unknown_nonprincipal_max"],
                value - principal_present,
            )
            continue
        if (
            not isinstance(member_keys, Sequence)
            or isinstance(member_keys, (str, bytes, bytearray))
            or any(not isinstance(key, str) or not key for key in member_keys)
        ):
            return False
        normalized = {
            "__principal__" if key in alias_keys else key for key in member_keys
        }
        if len(normalized) != value:
            return False
        state["identities"].update(normalized)

    for state in partitions.values():
        identities = state["identities"]
        known_nonprincipal = len(identities - {"__principal__"})
        principal_present = state["principal"] or "__principal__" in identities
        # Per-source counts cannot be added: the same person can occur in every
        # source.  Without identities, only the largest source cardinality is a
        # safe lower bound on the global distinct population.
        nonprincipal = max(known_nonprincipal, state["unknown_nonprincipal_max"])
        if nonprincipal + int(principal_present) == 1:
            return False
    return True


_USAGE_FIELDS = (
    "invocations",
    "input_tokens",
    "output_tokens",
    "tool_invocations",
    "active_session_seconds",
)


def usage_from_row(row: Mapping[str, Any]) -> AttributionUsage:
    """Read a flat or nested normalized attribution usage row."""
    nested = row.get("usage")
    source = nested if isinstance(nested, Mapping) else row
    return AttributionUsage(
        invocations=_non_negative_int(source.get("invocations"), default=0),
        input_tokens=_nullable_non_negative_int(source.get("input_tokens")),
        output_tokens=_nullable_non_negative_int(source.get("output_tokens")),
        tool_invocations=_nullable_non_negative_int(source.get("tool_invocations")),
        active_session_seconds=source.get("active_session_seconds"),
    )


def sum_usage(values: Iterable[AttributionUsage]) -> AttributionUsage:
    """Sum usage exactly while preserving never-reported fields as ``None``."""
    items = list(values)
    totals: dict[str, Any] = {}
    for field in _USAGE_FIELDS:
        reported = [getattr(item, field) for item in items if getattr(item, field) is not None]
        totals[field] = sum(reported) if reported else None
    totals["invocations"] = sum(item.invocations for item in items)
    return AttributionUsage(**totals)


def rank_and_fold_user_usage(
    rows: Iterable[tuple[str, AttributionUsage]],
    *,
    max_rows: int | None = 500,
) -> tuple[
    tuple[tuple[str, AttributionUsage], ...],
    int,
    AttributionUsage | None,
]:
    """Merge exact source rows, then rank and reserve one row for overflow."""
    if max_rows is not None and max_rows < 2:
        raise ValueError("max_rows must allow at least one user and one overflow row")
    by_user: dict[str, list[AttributionUsage]] = {}
    for user_key, usage in rows:
        if not isinstance(user_key, str) or not user_key:
            raise ValueError("user_key must be a non-empty string")
        by_user.setdefault(user_key, []).append(usage)
    ranked = sorted(
        ((user_key, sum_usage(parts)) for user_key, parts in by_user.items()),
        key=lambda item: (-item[1].invocations, item[0]),
    )
    if max_rows is None or len(ranked) <= max_rows:
        return tuple(ranked), 0, None
    visible = ranked[: max_rows - 1]
    hidden = ranked[max_rows - 1 :]
    return (
        tuple(visible),
        len(hidden),
        sum_usage(usage for _, usage in hidden),
    )


def zero_usage() -> AttributionUsage:
    """Return an additive identity for usage reconciliation."""
    return AttributionUsage(
        invocations=0,
        input_tokens=None,
        output_tokens=None,
        tool_invocations=None,
        active_session_seconds=None,
    )


def _non_negative_int(value: Any, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("attribution count must be a non-negative integer")
    return value


def _nullable_non_negative_int(value: Any) -> int | None:
    return None if value is None else _non_negative_int(value)


def decimal_or_none(value: Any) -> Decimal | None:
    """Normalize an optional non-negative decimal used by internal callers."""
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("attribution decimal must be finite and non-negative")
    return result
