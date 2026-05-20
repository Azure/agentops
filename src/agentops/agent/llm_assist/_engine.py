"""Top-level entry point for the LLM-assisted check suite."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

from agentops.agent.config import LLMAssistCheckConfig
from agentops.agent.findings import Finding
from agentops.agent.llm_assist._bundle_rule import check_bundle_coverage
from agentops.agent.llm_assist._client import LLMJudge
from agentops.agent.llm_assist._dataset_rules import (
    check_dataset_bias_signals,
    check_dataset_pii_risk,
)
from agentops.agent.llm_assist._prompt_rules import (
    check_prompt_jailbreak_surface,
    check_prompt_safety_guardrails,
    check_prompt_transparency,
)
from agentops.agent.llm_assist._spec_rules import (
    run_spec_implementation_gap_rule,
)
from agentops.agent.sources.foundry_control import FoundryControlPayload


_ALL_RULES = (
    "rai.prompt_transparency",
    "rai.prompt_safety_guardrails",
    "rai.prompt_jailbreak_surface",
    "rai.dataset_pii_risk",
    "rai.dataset_bias_signals",
    "opex.bundle_coverage",
    "opex.spec_conformance.llm.implementation_gap",
)


def _enabled_rules(config: LLMAssistCheckConfig) -> Set[str]:
    if not config.rules:
        return set(_ALL_RULES)
    return {r.strip() for r in config.rules if r and r.strip()}


def run_llm_assist_check(
    workspace: Path,
    config: LLMAssistCheckConfig,
    foundry: Optional[FoundryControlPayload],
) -> List[Finding]:
    """Run all enabled LLM-judged rules and merge their findings.

    Short-circuits cleanly when the suite is disabled, the judge model
    cannot be reached, or there is no Foundry agent / dataset to read.
    """
    if not config.enabled:
        return []

    judge = LLMJudge(config=config, workspace=workspace)
    # Skip the whole suite when no deployment is resolvable - the judge
    # cannot be called and we would just log warnings repeatedly.
    if not judge.resolve_deployment():
        return []

    enabled = _enabled_rules(config)
    findings: List[Finding] = []

    agents = list(foundry.agents) if foundry and foundry.agents else []
    if agents:
        if "rai.prompt_transparency" in enabled:
            findings.extend(
                check_prompt_transparency(judge, agents, config.min_confidence)
            )
        if "rai.prompt_safety_guardrails" in enabled:
            findings.extend(
                check_prompt_safety_guardrails(
                    judge, agents, config.min_confidence
                )
            )
        if "rai.prompt_jailbreak_surface" in enabled:
            findings.extend(
                check_prompt_jailbreak_surface(
                    judge, agents, config.min_confidence
                )
            )

    if "rai.dataset_pii_risk" in enabled:
        findings.extend(
            check_dataset_pii_risk(
                judge,
                workspace,
                config.max_dataset_rows,
                config.min_confidence,
            )
        )
    if "rai.dataset_bias_signals" in enabled:
        findings.extend(
            check_dataset_bias_signals(
                judge,
                workspace,
                config.max_dataset_rows,
                config.min_confidence,
            )
        )

    if agents and "opex.bundle_coverage" in enabled:
        findings.extend(
            check_bundle_coverage(judge, workspace, agents, config.min_confidence)
        )

    return findings
