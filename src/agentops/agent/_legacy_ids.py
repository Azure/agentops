"""Legacy id / category aliases for the WAF-aligned rename.

This module is the single auditable home for the
``genaiops`` -> ``operational_excellence`` / ``genaiops.*`` ->
``opex.*`` rename. It exists to soften the upgrade for users with
existing ``agent.yaml`` files that reference the old names; the
canonical surface of every other module already uses the new names.

The shim is read-only: it rewrites legacy keys in memory at config
load time and emits a one-shot deprecation warning per process. No
persistent dual surface.
"""

from __future__ import annotations

import logging
from typing import List, Optional

log = logging.getLogger(__name__)

# Legacy category key -> canonical category key.
LEGACY_CATEGORY_ALIASES = {
    "genaiops": "operational_excellence",
}

# Legacy finding / rule id prefix -> canonical prefix.
# Any id that starts with the left-hand string is rewritten by
# substituting the right-hand string for the matching prefix.
LEGACY_ID_PREFIXES = (
    ("genaiops.", "opex."),
)

_warned_categories: set[str] = set()
_warned_ids: set[str] = set()


def canonical_category(value: str) -> str:
    """Return the canonical category key for ``value``.

    If ``value`` is a legacy alias, emit a one-shot deprecation
    warning and return the canonical key. Unknown values are
    returned unchanged.
    """
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    canonical = LEGACY_CATEGORY_ALIASES.get(key)
    if canonical is None:
        return value
    if key not in _warned_categories:
        _warned_categories.add(key)
        log.warning(
            "agentops doctor: category '%s' has been renamed to '%s' "
            "(WAF-AI alignment). Update your config / CLI flag; the "
            "legacy name will be removed in a future release.",
            key,
            canonical,
        )
    return canonical


def canonical_id(value: str) -> str:
    """Return the canonical finding-id for ``value``.

    If ``value`` starts with a legacy prefix, rewrite it and emit a
    one-shot deprecation warning. Unknown values are returned
    unchanged.
    """
    if not isinstance(value, str):
        return value
    for legacy, canonical in LEGACY_ID_PREFIXES:
        if value.startswith(legacy):
            rewritten = canonical + value[len(legacy):]
            if value not in _warned_ids:
                _warned_ids.add(value)
                log.warning(
                    "agentops doctor: finding/rule id '%s' has been "
                    "renamed to '%s' (WAF-AI alignment). Update your "
                    "config; the legacy id will be removed in a "
                    "future release.",
                    value,
                    rewritten,
                )
            return rewritten
    return value


def canonicalize_id_list(values: Optional[List[str]]) -> List[str]:
    """Apply :func:`canonical_id` element-wise. Preserves order."""
    if not values:
        return list(values or [])
    return [canonical_id(v) for v in values]
