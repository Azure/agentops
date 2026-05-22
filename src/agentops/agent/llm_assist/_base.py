"""Base helpers for individual LLM-judged rules.

Every rule shares the same shape: a focused system prompt, a Pydantic
schema for the verdict, and a small builder that converts a verdict
into a :class:`Finding`. This module factors out the duplicate code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.llm_assist._client import JudgementMeta


class BaseVerdict(BaseModel):
    """Minimum schema every judge response must satisfy."""

    model_config = ConfigDict(extra="allow")
    risk: str = Field(description="Low, Medium, or High")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    suggestions: List[str] = Field(
        default_factory=list,
        description=(
            "Two to four concrete, actionable fixes the user can apply, "
            "tailored to what the judge actually observed."
        ),
    )


def hash_text(*chunks: str) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk.encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def normalised_risk(verdict: BaseVerdict) -> str:
    """Return verdict.risk lower-cased and bounded to {low, medium, high}."""
    raw = (getattr(verdict, "risk", "") or "").strip().lower()
    if raw in {"low", "medium", "high"}:
        return raw
    if raw in {"none", "ok", "clean"}:
        return "low"
    if raw in {"warning", "moderate"}:
        return "medium"
    if raw in {"critical", "severe"}:
        return "high"
    return "low"


def severity_for(risk: str) -> Severity:
    # LLM findings cap at WARNING by design.
    return Severity.WARNING if risk in {"medium", "high"} else Severity.INFO


@dataclass
class FindingBuilderArgs:
    rule_id: str
    title: str
    category: Category
    summary_template: str
    recommendation: str
    verdict: BaseVerdict
    meta: JudgementMeta
    extra_evidence: Dict[str, Any]


def build_llm_finding(args: FindingBuilderArgs) -> Optional[Finding]:
    risk = normalised_risk(args.verdict)
    if risk == "low":
        return None
    severity = severity_for(risk)

    # If the judge produced concrete suggestions, splice them into the
    # recommendation so the user sees actionable, case-specific fixes
    # right next to the canonical guidance.
    suggestions: List[str] = []
    for raw in getattr(args.verdict, "suggestions", []) or []:
        text = str(raw).strip()
        if text:
            suggestions.append(text)
    recommendation = args.recommendation
    if suggestions:
        bullets = "\n".join(f"- {s}" for s in suggestions[:6])
        recommendation = (
            f"{args.recommendation}\n\n"
            f"**Concrete fixes the judge model suggested for this "
            f"specific case:**\n{bullets}"
        )

    evidence: Dict[str, Any] = {
        "confidence": round(args.verdict.confidence, 3),
        "reasoning": args.verdict.reasoning,
        "model_deployment": args.meta.model_deployment,
        "cache_hit": args.meta.cache_hit,
        "risk": risk,
    }
    if suggestions:
        evidence["suggestions"] = suggestions
    evidence.update(args.extra_evidence)
    if args.meta.input_tokens or args.meta.output_tokens:
        evidence["tokens"] = {
            "input": args.meta.input_tokens,
            "output": args.meta.output_tokens,
        }
    return Finding(
        id=args.rule_id,
        severity=severity,
        category=args.category,
        title=f"[LLM-judged] {args.title}",
        summary=args.summary_template.format(
            risk=risk, reasoning=args.verdict.reasoning
        ),
        recommendation=recommendation,
        source="llm_judge",
        evidence=evidence,
    )
