"""GenAIOps Maturity Model - derives the project's current level from
Doctor findings + eval history.

The model is the public Microsoft GenAIOps Maturity Model (see
https://techcommunity.microsoft.com/blog/azure-ai-services-blog/genaiops).
Levels (compressed):

* **L0 - Ad-hoc.** No discipline; no eval history.
* **L1 - Initial.** Eval runs exist but nothing automated.
* **L2 - Repeatable.** Automated eval in CI on PR changes.
* **L3 - Managed.** Deployment workflows + production telemetry.
* **L4 - Optimised.** Continuous evaluation + drift / stability
  monitoring in place.

The Doctor's job is to compute the current level *without asking*,
based purely on signals it already collects: the set of finding ids
plus whether eval history is non-empty.

The output is purely informational. It does not affect exit codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from agentops.agent.findings import Finding
from agentops.agent.sources.results_history import ResultsHistory


@dataclass(frozen=True)
class MaturityAssessment:
    """Outcome of :func:`compute_level`."""

    level: int
    label: str
    next_gap: Optional[str] = None  # finding id or short reason
    explanation: str = ""


_L1_BLOCKERS = ("opex.no_pr_gate",)
_L2_BLOCKERS = (
    "opex.no_deploy_workflow",
    "errors.no_runtime_telemetry",
    "opex.no_foundry_control_configured",
    "opex.no_foundry_agents",
)
_L3_BLOCKERS = (
    "safety.config.continuous_eval_missing",
    "safety.config.continuous_eval_disabled",
)


def _first_matching_id(
    finding_ids: Iterable[str], wanted: Iterable[str]
) -> Optional[str]:
    wanted_set = set(wanted)
    for fid in finding_ids:
        if fid in wanted_set:
            return fid
    return None


def _first_flaky_metric(finding_ids: Iterable[str]) -> Optional[str]:
    for fid in finding_ids:
        if fid.startswith("opex.flaky_metric"):
            return fid
    return None


_LEVEL_LABELS = {
    0: "Ad-hoc",
    1: "Initial",
    2: "Repeatable",
    3: "Managed",
    4: "Optimised",
}


def compute_level_from_ids(
    finding_ids: Sequence[str], has_history: bool
) -> MaturityAssessment:
    """Pure version: compute the level given just finding ids + a flag.

    This is the form used by the dashboard (which loads finding ids
    out of historical analysis records on disk) and by tests.
    :func:`compute_level` is a thin wrapper for the analyzer pipeline
    that already has ``Finding`` objects and a ``ResultsHistory``.
    """
    if not has_history:
        return MaturityAssessment(
            level=0,
            label=_LEVEL_LABELS[0],
            next_gap="no_history",
            explanation=(
                "No eval runs found in `.agentops/results/`. Run "
                "`agentops eval run` once to land on L1."
            ),
        )

    blocker = _first_matching_id(finding_ids, _L1_BLOCKERS)
    if blocker is not None:
        return MaturityAssessment(
            level=1,
            label=_LEVEL_LABELS[1],
            next_gap=blocker,
            explanation=(
                "Eval runs exist locally but CI does not gate PRs. "
                f"Address `{blocker}` to move to L2."
            ),
        )

    blocker = _first_matching_id(finding_ids, _L2_BLOCKERS)
    if blocker is not None:
        return MaturityAssessment(
            level=2,
            label=_LEVEL_LABELS[2],
            next_gap=blocker,
            explanation=(
                "PR gate is in place. To move to L3, address "
                f"`{blocker}` (deploy workflows + production "
                "telemetry give Doctor what it needs to grade running "
                "agents)."
            ),
        )

    blocker = _first_matching_id(finding_ids, _L3_BLOCKERS) or _first_flaky_metric(
        finding_ids
    )
    if blocker is not None:
        return MaturityAssessment(
            level=3,
            label=_LEVEL_LABELS[3],
            next_gap=blocker,
            explanation=(
                "Deployment workflows and production telemetry are "
                f"wired. To move to L4, address `{blocker}` so "
                "running agents are continuously evaluated against "
                "stable metrics."
            ),
        )

    return MaturityAssessment(
        level=4,
        label=_LEVEL_LABELS[4],
        next_gap=None,
        explanation=(
            "Every Doctor signal that gates the GenAIOps Maturity "
            "Model is green. Keep an eye on the regression and "
            "flaky-metric checks over time."
        ),
    )


def compute_level(
    findings: Sequence[Finding],
    history: Optional[ResultsHistory],
) -> MaturityAssessment:
    """Compute the project's GenAIOps Maturity Model level + next gap."""
    return compute_level_from_ids(
        finding_ids=[f.id for f in findings],
        has_history=bool(history and history.runs),
    )


def maturity_levels() -> List[MaturityAssessment]:
    """All five levels in order - useful for tooltips / dashboards."""
    return [
        MaturityAssessment(level=i, label=_LEVEL_LABELS[i]) for i in range(5)
    ]

