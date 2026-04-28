"""Rule registry for the WAF-AI posture check.

Each rule is a small callable that receives the
:class:`AzureResourcesPayload` and the source name, and returns a list
of :class:`Finding`s (zero, one, or many). Rules are independent and
pure.

The ``posture`` check (see :mod:`agentops.agent.checks.posture`)
iterates the rules registered here and aggregates the findings.

To add a new rule:

* Add a module under this package.
* Implement ``def evaluate(payload, source_name) -> list[Finding]``.
* Register it in :data:`RULE_REGISTRY` below.
"""

from __future__ import annotations

from typing import Callable, Dict, List

from agentops.agent.findings import Finding
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RuleFn = Callable[[AzureResourcesPayload, str], List[Finding]]


def _build_registry() -> Dict[str, RuleFn]:
    from agentops.agent.checks.posture_rules.content_filter import (
        evaluate as content_filter_rule,
    )
    from agentops.agent.checks.posture_rules.diagnostics import (
        evaluate as diagnostics_rule,
    )
    from agentops.agent.checks.posture_rules.local_auth import (
        evaluate as local_auth_rule,
    )
    from agentops.agent.checks.posture_rules.managed_identity import (
        evaluate as managed_identity_rule,
    )
    from agentops.agent.checks.posture_rules.network import (
        evaluate as network_rule,
    )

    return {
        "waf.security.local_auth_disabled": local_auth_rule,
        "waf.security.public_network_access": network_rule,
        "waf.security.managed_identity": managed_identity_rule,
        "waf.security.diagnostic_settings": diagnostics_rule,
        "waf.security.content_filter": content_filter_rule,
    }


RULE_REGISTRY: Dict[str, RuleFn] = _build_registry()
