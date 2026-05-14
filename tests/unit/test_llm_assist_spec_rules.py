"""Tests for the LLM-judged spec-conformance rule (mocked judge)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

from agentops.agent.config import (
    LLMAssistCheckConfig,
    SpecConformanceCheckConfig,
)
from agentops.agent.findings import Category, Severity
from agentops.agent.llm_assist._client import JudgementMeta
from agentops.agent.llm_assist._spec_rules import (
    run_spec_implementation_gap_rule,
)


class _FakeJudge:
    def __init__(self, verdict: Optional[Dict[str, Any]], deployment: str = "gpt-test"):
        self._verdict = verdict
        self._deployment = deployment

    def resolve_deployment(self) -> Optional[str]:
        return self._deployment

    def call(self, *, system, user, schema, inputs_hash):
        if self._verdict is None:
            return None
        verdict = schema.model_validate(self._verdict)
        return verdict, JudgementMeta(
            cache_hit=False,
            model_deployment=self._deployment,
            input_tokens=10,
            output_tokens=5,
        )


def _spec_workspace(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text(
        "# Agent spec\n\n## Capabilities\n\n- Retrieval-augmented Q&A using "
        "`GroundednessEvaluator`.\n- Tool calls for booking.\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.py").write_text("# agent code\n", encoding="utf-8")
    return tmp_path


def _llm_config(**overrides: Any) -> LLMAssistCheckConfig:
    base = dict(
        enabled=True,
        deployment_name="gpt-test",
        project_endpoint="https://test.api.azureml.ms",
        min_confidence=0.5,
    )
    base.update(overrides)
    return LLMAssistCheckConfig(**base)


def _spec_config(**overrides: Any) -> SpecConformanceCheckConfig:
    base = {"enabled": True, "llm_assist": {"enabled": True, "severity_floor": 0.3}}
    base.update(overrides)
    return SpecConformanceCheckConfig.model_validate(base)


def test_disabled_global_llm_assist_returns_empty(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    findings = run_spec_implementation_gap_rule(
        tmp_path, _llm_config(enabled=False), _spec_config()
    )
    assert findings == []


def test_disabled_spec_llm_subconfig_returns_empty(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    cfg = _spec_config(llm_assist={"enabled": False})
    findings = run_spec_implementation_gap_rule(tmp_path, _llm_config(), cfg)
    assert findings == []


def test_env_kill_switch_overrides_config(tmp_path: Path, monkeypatch) -> None:
    _spec_workspace(tmp_path)
    monkeypatch.setenv("AGENTOPS_DOCTOR_LLM_ASSIST", "0")
    findings = run_spec_implementation_gap_rule(
        tmp_path, _llm_config(), _spec_config()
    )
    assert findings == []


def test_no_spec_documents_returns_empty(tmp_path: Path) -> None:
    findings = run_spec_implementation_gap_rule(
        tmp_path, _llm_config(), _spec_config()
    )
    assert findings == []


def test_emits_warning_finding_when_judge_reports_medium_risk(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    fake = _FakeJudge(
        {
            "risk": "medium",
            "confidence": 0.85,
            "reasoning": "GroundednessEvaluator is referenced but not in any bundle.",
            "suggestions": ["Add GroundednessEvaluator to the bundle."],
            "missing_capabilities": ["Grounded retrieval"],
        }
    )
    with patch(
        "agentops.agent.llm_assist._spec_rules.LLMJudge", return_value=fake
    ):
        findings = run_spec_implementation_gap_rule(
            tmp_path, _llm_config(), _spec_config()
        )
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "opex.spec_conformance.llm.implementation_gap"
    assert f.category == Category.OPERATIONAL_EXCELLENCE
    # Spec-vs-impl rule must never emit critical.
    assert f.severity in {Severity.INFO, Severity.WARNING}


def test_low_confidence_below_floor_returns_empty(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    fake = _FakeJudge(
        {
            "risk": "high",
            "confidence": 0.1,
            "reasoning": "uncertain",
            "suggestions": [],
            "missing_capabilities": [],
        }
    )
    cfg = _spec_config(llm_assist={"enabled": True, "severity_floor": 0.7})
    with patch(
        "agentops.agent.llm_assist._spec_rules.LLMJudge", return_value=fake
    ):
        findings = run_spec_implementation_gap_rule(tmp_path, _llm_config(), cfg)
    assert findings == []


def test_never_emits_critical_even_for_high_risk(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    fake = _FakeJudge(
        {
            "risk": "high",
            "confidence": 0.99,
            "reasoning": "everything is missing",
            "suggestions": ["implement it"],
            "missing_capabilities": ["X", "Y", "Z"],
        }
    )
    with patch(
        "agentops.agent.llm_assist._spec_rules.LLMJudge", return_value=fake
    ):
        findings = run_spec_implementation_gap_rule(
            tmp_path, _llm_config(), _spec_config()
        )
    assert len(findings) == 1
    assert findings[0].severity is not Severity.CRITICAL


def test_input_too_large_emits_info_finding_and_skips_judge(tmp_path: Path) -> None:
    # Force the spec text to exceed the cap.
    (tmp_path / "AGENTS.md").write_text("a" * 5_000, encoding="utf-8")
    cfg = _spec_config(
        llm_assist={
            "enabled": True,
            "severity_floor": 0.3,
            "max_input_chars": 1_000,
            "max_workspace_paths": 200,
        }
    )
    with patch(
        "agentops.agent.llm_assist._spec_rules.LLMJudge",
        side_effect=AssertionError("judge must not be invoked"),
    ):
        findings = run_spec_implementation_gap_rule(
            tmp_path, _llm_config(), cfg
        )
    assert len(findings) == 1
    assert findings[0].id == "opex.spec_conformance.llm.input_too_large"
    assert findings[0].severity == Severity.INFO


def test_no_deployment_resolved_returns_empty(tmp_path: Path) -> None:
    _spec_workspace(tmp_path)
    fake = _FakeJudge(verdict=None, deployment="")
    # resolve_deployment returns "" — falsy — so the rule short-circuits.
    fake._deployment = ""
    with patch(
        "agentops.agent.llm_assist._spec_rules.LLMJudge", return_value=fake
    ):
        findings = run_spec_implementation_gap_rule(
            tmp_path, _llm_config(), _spec_config()
        )
    assert findings == []
