"""Foundry observability readiness checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from agentops.agent.findings import Category, Finding, Severity
from agentops.utils.yaml import load_yaml

SOURCE_NAME = "observability"


def run_observability_check(workspace: Path) -> List[Finding]:
    """Validate repo-side intent for Foundry observability signals.

    These checks are deliberately read-only. Foundry owns the runtime surfaces
    for traces, intelligent sampling, replay, multi-turn eval, and rubric
    evaluators; AgentOps verifies whether the repo has enough metadata and
    evidence to make those signals part of release readiness.
    """

    config = _safe_config(workspace)
    if not config and not (workspace / ".agentops").exists():
        return []

    findings: List[Finding] = []
    findings.extend(_check_multiturn_coverage(config, workspace))
    findings.extend(_check_rubric_coverage(config))
    findings.extend(_check_trace_sampling(config, workspace))
    findings.extend(_check_trace_replay(config, workspace))
    return findings


def _check_multiturn_coverage(config: dict[str, Any], workspace: Path) -> List[Finding]:
    if str(config.get("dataset_kind") or "auto") == "multi-turn":
        return []
    manifest = _trace_manifest(workspace)
    lineage = manifest.get("lineage") if isinstance(manifest, dict) else {}
    if isinstance(lineage, dict) and int(lineage.get("multi_turn_rows") or 0) > 0:
        return []
    return [
        Finding(
            id="observability.multiturn_coverage_missing",
            severity=Severity.INFO,
            category=Category.QUALITY,
            title="Multi-turn evaluation coverage is not declared yet",
            summary=(
                "Foundry multi-turn evaluation is designed to catch context "
                "carryover, tone drift, contradictions, and task-completion "
                "failures across a full conversation. AgentOps did not find "
                "`dataset_kind: multi-turn` or trace-derived conversation rows."
            ),
            recommendation=(
                "After the single-turn smoke gate is green, add a conversation "
                "dataset or use Foundry traces-to-dataset output with `messages` "
                "rows, then set `dataset_kind: multi-turn` in agentops.yaml."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_rubric_coverage(config: dict[str, Any]) -> List[Finding]:
    rubrics = config.get("rubrics")
    if isinstance(rubrics, list) and rubrics:
        return []
    return [
        Finding(
            id="observability.rubric_missing",
            severity=Severity.INFO,
            category=Category.QUALITY,
            title="No context-specific rubric evaluator is declared",
            summary=(
                "Foundry rubric evaluators let teams score the agent against "
                "task-specific criteria such as task success, tone, safety, cost, "
                "and latency. AgentOps did not find a `rubrics:` block in "
                "agentops.yaml."
            ),
            recommendation=(
                "Declare at least one rubric in agentops.yaml and bind its "
                "dimension metrics to thresholds, or reference the rubric through "
                "the azd eval recipe used by `execution: azd`."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_trace_sampling(config: dict[str, Any], workspace: Path) -> List[Finding]:
    observability = config.get("observability")
    trace_sampling = (
        observability.get("trace_sampling")
        if isinstance(observability, dict)
        else None
    )
    if isinstance(trace_sampling, dict) and trace_sampling.get("enabled") is True:
        return []
    manifest = _trace_manifest(workspace)
    lineage = manifest.get("lineage") if isinstance(manifest, dict) else {}
    if isinstance(lineage, dict) and lineage.get("sampling_policies"):
        return []
    return [
        Finding(
            id="observability.trace_sampling_missing",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Intelligent trace sampling is not evidence-ready",
            summary=(
                "Foundry intelligent trace sampling evaluates the most "
                "signal-rich production traces without scoring every request. "
                "AgentOps did not find `observability.trace_sampling.enabled: true` "
                "or sampling metadata in the trace-regression manifest."
            ),
            recommendation=(
                "Enable Foundry trace sampling or document the sampling policy in "
                "`observability.trace_sampling`, then regenerate trace-derived "
                "dataset candidates so release evidence includes the lineage."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_trace_replay(config: dict[str, Any], workspace: Path) -> List[Finding]:
    observability = config.get("observability")
    if isinstance(observability, dict) and observability.get("trace_replay_url"):
        return []
    manifest = _trace_manifest(workspace)
    lineage = manifest.get("lineage") if isinstance(manifest, dict) else {}
    if isinstance(lineage, dict) and lineage.get("replay_urls"):
        return []
    return [
        Finding(
            id="observability.trace_replay_missing",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Trace replay link is not captured in release evidence",
            summary=(
                "Foundry trace replay and visualization make incident review "
                "faster by linking each failure to the exact prompts, decisions, "
                "tool calls, and outputs. AgentOps did not find a replay URL in "
                "agentops.yaml or the trace-regression manifest."
            ),
            recommendation=(
                "After selecting representative traces in Foundry, keep the replay "
                "link in `observability.trace_replay_url` or include it in trace "
                "exports before running `agentops eval promote-traces --apply`."
            ),
            source=SOURCE_NAME,
        )
    ]


def _trace_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / ".agentops" / "data" / "trace-regression-manifest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_config(workspace: Path) -> dict[str, Any]:
    path = workspace / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
