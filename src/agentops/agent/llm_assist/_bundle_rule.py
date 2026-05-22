"""LLM-judged Operational Excellence check: evaluator-bundle coverage.

Reads the project's evaluator bundle YAML and a short agent description
excerpt, then asks the judge model whether the bundle covers the
evaluators a project of that shape typically needs (e.g. a RAG agent
without ``GroundednessEvaluator``).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from agentops.agent.findings import Category, Finding
from agentops.agent.llm_assist._base import (
    BaseVerdict,
    FindingBuilderArgs,
    build_llm_finding,
    hash_text,
)
from agentops.agent.llm_assist._client import LLMJudge
from agentops.agent.sources.foundry_control import FoundryAgentSummary


_COVERAGE_SYSTEM = """You audit a project's evaluator bundle for the
Microsoft Well-Architected Framework for AI Operational Excellence
pillar. You receive:

1. The bundle YAML (evaluators list + thresholds).
2. The agent's name + a short instructions excerpt that hints at its
   use case (RAG, conversational, tool-using, etc.).

Decide which Foundry / azure-ai-evaluation built-in evaluators are
notably missing for that use case. Examples:

* RAG agent without GroundednessEvaluator or RetrievalEvaluator.
* Tool-using agent without ToolCallAccuracyEvaluator.
* Customer-support chat agent without CoherenceEvaluator.
* Any agent serving end-users without content-safety evaluators
  (Violence, SelfHarm, Sexual, HateUnfairness).

Respond as compact JSON. Do NOT recommend custom evaluators; stick to
Foundry / azure-ai-evaluation built-ins.

{"risk": "low|medium|high", "confidence": <0.0-1.0>,
 "reasoning": "<one short paragraph>",
 "suggestions": ["<fix 1>", "<fix 2>", "<fix 3>"],
 "missing_evaluators": ["GroundednessEvaluator", ...]}
"""


class CoverageVerdict(BaseVerdict):
    missing_evaluators: List[str] = []


def _load_bundle(workspace: Path) -> Optional[str]:
    bundles = workspace / ".agentops" / "bundles"
    if not bundles.is_dir():
        return None
    yamls = sorted(bundles.glob("*.yaml"))
    if not yamls:
        return None
    bundle_path = yamls[0]
    try:
        text = bundle_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text


def _agent_excerpt(agents: List[FoundryAgentSummary]) -> Optional[str]:
    for agent in agents:
        if not agent.instructions:
            continue
        excerpt = agent.instructions.strip()
        if len(excerpt) > 800:
            excerpt = excerpt[:800] + "..."
        return (
            f"Agent name: {agent.name or agent.agent_id}\n"
            f"Model: {agent.model or 'unknown'}\n\n"
            f"Instructions excerpt:\n{excerpt}"
        )
    if agents:
        a = agents[0]
        return (
            f"Agent name: {a.name or a.agent_id}\nModel: "
            f"{a.model or 'unknown'}\n(instructions unavailable)"
        )
    return None


def check_bundle_coverage(
    judge: LLMJudge,
    workspace: Path,
    agents: List[FoundryAgentSummary],
    min_confidence: float,
) -> List[Finding]:
    bundle_text = _load_bundle(workspace)
    if bundle_text is None:
        return []
    agent_excerpt = _agent_excerpt(agents)
    if agent_excerpt is None:
        return []

    # Sanity check: skip when the YAML is unparseable.
    try:
        yaml.safe_load(bundle_text)
    except yaml.YAMLError:
        return []

    ih = hash_text("bundle_coverage", bundle_text, agent_excerpt)
    result = judge.call(
        system=_COVERAGE_SYSTEM,
        user=(
            "Bundle YAML:\n```yaml\n"
            f"{bundle_text}\n```\n\n"
            f"Agent context:\n{agent_excerpt}"
        ),
        schema=CoverageVerdict,
        inputs_hash=ih,
    )
    if result is None:
        return []
    verdict, meta = result
    if verdict.confidence < min_confidence:
        return []
    finding = build_llm_finding(
        FindingBuilderArgs(
            rule_id="opex.llm.bundle_coverage",
            title="Evaluator bundle may be missing built-ins for this agent",
            category=Category.OPERATIONAL_EXCELLENCE,
            summary_template=(
                "The judge model identified built-in evaluators that "
                "fit this agent's use case but are not in the bundle "
                "(risk={risk}): {reasoning}"
            ),
            recommendation=(
                "Review the suggested evaluators in this finding's "
                "evidence and add them to `.agentops/bundles/*.yaml` "
                "if they fit. Use the canonical names from "
                "`docs/foundry-evaluation-sdk-built-in-evaluators.md`."
            ),
            verdict=verdict,
            meta=meta,
            extra_evidence={
                "missing_evaluators": getattr(
                    verdict, "missing_evaluators", []
                ),
            },
        )
    )
    return [finding] if finding is not None else []
