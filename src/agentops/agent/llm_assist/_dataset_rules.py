"""LLM-judged Responsible-AI checks against the evaluation dataset."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from agentops.agent.findings import Category, Finding
from agentops.agent.llm_assist._base import (
    BaseVerdict,
    FindingBuilderArgs,
    build_llm_finding,
    hash_text,
)
from agentops.agent.llm_assist._client import LLMJudge


_PII_SYSTEM = """You audit a sample of eval dataset rows for the
Microsoft Responsible AI Standard's Privacy & Security principle.
The rows are JSONL records. You decide whether the sample contains
personally identifiable information (PII).

Categories that count: personal names tied to context, email
addresses, phone numbers, government / employee / student ids,
financial account numbers, residential addresses, dates of birth tied
to a real-looking identity, IP addresses tied to a specific person.

Public-figure examples or obvious placeholder data (e.g. "John Doe",
"test@example.com") do NOT count.

Respond as compact JSON. Do NOT echo the offending row text back -
just describe categories and a count.

{"risk": "low|medium|high", "confidence": <0.0-1.0>,
 "reasoning": "<one short paragraph>",
 "suggestions": ["<fix 1>", "<fix 2>", "<fix 3>"],
 "categories": ["name" | "email" | "phone" | "id" | "address" |
                "dob" | "ip", ...],
 "row_count": <int>}
"""


class PIIVerdict(BaseVerdict):
    categories: List[str] = []
    row_count: int = 0


_BIAS_SYSTEM = """You audit a sample of eval dataset rows for the
Microsoft Responsible AI Standard's Fairness principle. You decide
whether the row sample shows demographic / role / domain skew that
could bias the agent's evaluated quality.

Examples of signal:

* All examples reference a single gender, age group, or ethnicity.
* All scenarios target one industry / domain / region.
* Tone or register is uniform (only formal, or only casual).
* All examples represent the "happy path"; edge cases are absent.

Respond as compact JSON. Do not echo dataset rows.

{"risk": "low|medium|high", "confidence": <0.0-1.0>,
 "reasoning": "<one short paragraph>",
 "suggestions": ["<fix 1>", "<fix 2>", "<fix 3>"],
 "skew_axes": ["gender" | "age" | "domain" | "tone" | "happy_path" |
               "geography" | "other", ...]}
"""


class BiasVerdict(BaseVerdict):
    skew_axes: List[str] = []


def _load_dataset_sample(workspace: Path, max_rows: int) -> Optional[str]:
    data_dir = workspace / ".agentops" / "data"
    if not data_dir.is_dir():
        return None
    rows: List[str] = []
    for path in sorted(data_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(line)
                    if len(rows) >= max_rows:
                        break
        except OSError:
            continue
        if len(rows) >= max_rows:
            break
    if not rows:
        return None
    return "\n".join(rows)


def check_dataset_pii_risk(
    judge: LLMJudge,
    workspace: Path,
    max_rows: int,
    min_confidence: float,
) -> List[Finding]:
    sample = _load_dataset_sample(workspace, max_rows)
    if sample is None:
        return []
    ih = hash_text("dataset_pii", str(max_rows), sample)
    result = judge.call(
        system=_PII_SYSTEM,
        user=f"Dataset rows (one JSON per line):\n```\n{sample}\n```",
        schema=PIIVerdict,
        inputs_hash=ih,
    )
    if result is None:
        return []
    verdict, meta = result
    if verdict.confidence < min_confidence:
        return []
    finding = build_llm_finding(
        FindingBuilderArgs(
            rule_id="responsible_ai.llm.dataset_pii_risk",
            title="Possible PII detected in evaluation dataset",
            category=Category.RESPONSIBLE_AI,
            summary_template=(
                "The judge model flagged possible PII in the dataset "
                "sample (risk={risk}): {reasoning}"
            ),
            recommendation=(
                "Audit `.agentops/data/*.jsonl` for personally "
                "identifiable information. Replace real names, email "
                "addresses, phone numbers, and ids with placeholders "
                "(`john.doe@example.com`, `+1-555-0100`). Bump the "
                "dataset `version:` field after the scrub so historical "
                "runs remain comparable."
            ),
            verdict=verdict,
            meta=meta,
            extra_evidence={
                "categories": getattr(verdict, "categories", []),
                "row_count_judged": int(getattr(verdict, "row_count", 0)),
            },
        )
    )
    return [finding] if finding is not None else []


def check_dataset_bias_signals(
    judge: LLMJudge,
    workspace: Path,
    max_rows: int,
    min_confidence: float,
) -> List[Finding]:
    sample = _load_dataset_sample(workspace, max_rows)
    if sample is None:
        return []
    ih = hash_text("dataset_bias", str(max_rows), sample)
    result = judge.call(
        system=_BIAS_SYSTEM,
        user=f"Dataset rows (one JSON per line):\n```\n{sample}\n```",
        schema=BiasVerdict,
        inputs_hash=ih,
    )
    if result is None:
        return []
    verdict, meta = result
    if verdict.confidence < min_confidence:
        return []
    finding = build_llm_finding(
        FindingBuilderArgs(
            rule_id="responsible_ai.llm.dataset_bias_signals",
            title="Evaluation dataset shows distribution skew",
            category=Category.RESPONSIBLE_AI,
            summary_template=(
                "The judge model identified distribution skew in the "
                "dataset sample (risk={risk}): {reasoning}"
            ),
            recommendation=(
                "Diversify the dataset along the flagged axes (gender, "
                "age, domain, tone, geography, happy/sad paths). The "
                "Microsoft Responsible AI Standard's Fairness principle "
                "asks for representative test data; uniform samples "
                "underestimate the agent's real-world failure rate."
            ),
            verdict=verdict,
            meta=meta,
            extra_evidence={
                "skew_axes": getattr(verdict, "skew_axes", []),
            },
        )
    )
    return [finding] if finding is not None else []
