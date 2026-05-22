"""LLM-judged Doctor checks.

This package adds an opt-in layer of Doctor checks that invoke a judge
model (via Foundry's OpenAI client) to evaluate semantic signals -
prompt quality, dataset PII risk, bias, bundle coverage. See
``docs/doctor-explained.md`` for the full rationale.

Entry point is :func:`run_llm_assist_check`. Everything else here is
implementation detail; do not import from sub-modules directly.
"""

from __future__ import annotations

from agentops.agent.llm_assist._engine import run_llm_assist_check

__all__ = ["run_llm_assist_check"]
