"""LLM-judged spec-conformance rule: spec-vs-implementation gap analysis.

Gated by ``checks.operational_excellence.spec_conformance.llm_assist``
*and* the global ``checks.llm_assist`` enable flag, so users who don't
want any LLM-judged checks running can disable the suite once.

The rule never emits ``critical``: spec-vs-implementation gap analysis
is a soft signal by nature. Input is capped to keep prompt size
predictable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from agentops.agent.checks.spec_conformance import detect_documents
from agentops.agent.config import (
    LLMAssistCheckConfig,
    SpecConformanceCheckConfig,
)
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.llm_assist._base import (
    BaseVerdict,
    FindingBuilderArgs,
    build_llm_finding,
    hash_text,
    severity_for,
)
from agentops.agent.llm_assist._client import LLMJudge
from agentops.agent.sources.spec_detectors import SpecDocument


_SYSTEM = """You are auditing a project's specification against its
implementation for the Microsoft Well-Architected Framework for AI
Operational Excellence pillar. You receive:

1. A merged specification (spec-kit `.specify/` files and/or
   `AGENTS.md`/`copilot-instructions.md`).
2. A workspace fingerprint listing key files under `src/`,
   `.agentops/`, and the project root.

Identify capabilities the spec promises that the workspace shows no
evidence of implementing. Be conservative — only flag gaps with
concrete evidence (named modules, evaluators, datasets, endpoints
mentioned in the spec but absent from the fingerprint). Ignore
stylistic or wording differences.

Respond as compact JSON:

{"risk": "low|medium|high", "confidence": <0.0-1.0>,
 "reasoning": "<one short paragraph>",
 "suggestions": ["<fix 1>", "<fix 2>", "<fix 3>"],
 "missing_capabilities": ["<capability label 1>", ...]}
"""


class _SpecGapVerdict(BaseVerdict):
    missing_capabilities: List[str] = []


_RULE_ID = "opex.spec_conformance.llm.implementation_gap"
_INPUT_TOO_LARGE_ID = "opex.spec_conformance.llm.input_too_large"


def run_spec_implementation_gap_rule(
    workspace: Path,
    llm_config: LLMAssistCheckConfig,
    spec_config: SpecConformanceCheckConfig,
) -> List[Finding]:
    """Run the LLM spec-vs-implementation gap rule.

    No-ops cleanly when:

    * the global ``llm_assist`` suite is disabled;
    * the spec-conformance sub-config disables the LLM rule;
    * the env override (``AGENTOPS_DOCTOR_LLM_ASSIST=0``) is set;
    * no spec document can be detected;
    * the judge model deployment cannot be resolved.
    """

    if not llm_config.enabled:
        return []
    if not spec_config.enabled:
        return []
    if not spec_config.llm_assist.enabled:
        return []
    env_flag = os.environ.get("AGENTOPS_DOCTOR_LLM_ASSIST")
    if env_flag is not None and env_flag.strip() == "0":
        return []

    documents = detect_documents(workspace, spec_config)
    if not documents:
        return []

    merged_spec = _merge_spec_text(
        documents, spec_config.llm_assist.max_input_chars
    )
    fingerprint, fp_truncated = _workspace_fingerprint(
        workspace, spec_config.llm_assist.max_workspace_paths
    )
    spec_truncated = merged_spec.endswith("[...truncated...]")

    if spec_truncated or fp_truncated:
        return [
            Finding(
                id=_INPUT_TOO_LARGE_ID,
                severity=Severity.INFO,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="Spec-conformance LLM rule skipped: input too large",
                summary=(
                    "The merged spec or workspace fingerprint exceeds "
                    "the configured cap. The deterministic rules still "
                    "ran; only the LLM gap-analysis was skipped."
                ),
                recommendation=(
                    "Increase "
                    "`checks.operational_excellence.spec_conformance."
                    "llm_assist.max_input_chars` / `max_workspace_paths`"
                    ", or split the spec into smaller files."
                ),
                source="llm_judge",
                evidence={
                    "spec_truncated": spec_truncated,
                    "fingerprint_truncated": fp_truncated,
                },
            )
        ]

    judge = LLMJudge(config=llm_config, workspace=workspace)
    if not judge.resolve_deployment():
        return []

    inputs_hash = hash_text("spec_implementation_gap", merged_spec, fingerprint)
    result = judge.call(
        system=_SYSTEM,
        user=(
            "Specification:\n```\n"
            f"{merged_spec}\n```\n\n"
            "Workspace fingerprint:\n```\n"
            f"{fingerprint}\n```"
        ),
        schema=_SpecGapVerdict,
        inputs_hash=inputs_hash,
    )
    if result is None:
        return []
    verdict, meta = result

    severity_floor = spec_config.llm_assist.severity_floor
    if verdict.confidence < severity_floor:
        return []

    finding = build_llm_finding(
        FindingBuilderArgs(
            rule_id=_RULE_ID,
            title="Spec capabilities may not be reflected in the implementation",
            category=Category.OPERATIONAL_EXCELLENCE,
            summary_template=(
                "Judge model identified spec capabilities not "
                "evidenced in the workspace (risk={risk}): "
                "{reasoning}"
            ),
            recommendation=(
                "Reconcile the spec with the implementation: either "
                "build the missing capabilities or update the spec to "
                "reflect what's actually shipped."
            ),
            verdict=verdict,
            meta=meta,
            extra_evidence={
                "missing_capabilities": list(verdict.missing_capabilities)[:10],
                "detected_formats": [d.format for d in documents],
            },
        )
    )
    if finding is None:
        return []
    # Hard cap at WARNING by design — spec drift is a soft signal.
    finding.severity = min(finding.severity, severity_for("medium"))
    return [finding]


def _merge_spec_text(documents: List[SpecDocument], max_chars: int) -> str:
    chunks: List[str] = []
    remaining = max_chars
    for doc in documents:
        if remaining <= 0:
            break
        block = f"=== {doc.format} ===\n{doc.text}\n"
        if len(block) > remaining:
            chunks.append(block[:remaining] + "\n[...truncated...]")
            remaining = 0
        else:
            chunks.append(block)
            remaining -= len(block)
    return "\n".join(chunks)


def _workspace_fingerprint(workspace: Path, max_paths: int) -> tuple[str, bool]:
    """Build a deterministic, size-bounded summary of the workspace tree."""
    roots = [
        workspace / "src",
        workspace / ".agentops",
        workspace / "run.yaml",
        workspace / ".agentops" / "run.yaml",
        workspace / "AGENTS.md",
        workspace / "CHANGELOG.md",
        workspace / "README.md",
    ]
    seen: List[str] = []
    truncated = False
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            seen.append(str(root.relative_to(workspace)).replace("\\", "/"))
            continue
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                continue
            rel = str(p.relative_to(workspace)).replace("\\", "/")
            if any(
                rel.startswith(prefix)
                for prefix in (".agentops/results/", ".agentops/cache/")
            ):
                continue
            seen.append(rel)
            if len(seen) >= max_paths:
                truncated = True
                break
        if truncated:
            break
    return "\n".join(seen), truncated
