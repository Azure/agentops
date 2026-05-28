"""Build production-readiness evidence for a release candidate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, cast

from agentops.agent.analyzer import AnalysisResult
from agentops.agent.findings import Severity
from agentops.core.release_evidence import (
    ReleaseEvidence,
    ReleaseEvidenceCheck,
    ReleaseEvidenceLink,
)
from agentops.pipeline.official_eval import OFFICIAL_EVAL_RUNNER
from agentops.utils.yaml import load_yaml


@dataclass(frozen=True)
class EvidenceWriteResult:
    """Paths written by ``write_release_evidence``."""

    evidence: ReleaseEvidence
    directory: Path
    json_path: Path
    markdown_path: Path


_SECRET_PATTERNS = (
    (
        re.compile(r"(InstrumentationKey=)[^;,\s]+", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (
        re.compile(r"(Authorization:\s*Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (
        re.compile(
            r"(api[_-]?key|client[_-]?secret|connection[_-]?string)(['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
            re.IGNORECASE,
        ),
        r"\1\2<redacted>",
    ),
)


def build_release_evidence(
    workspace: Path,
    *,
    analysis: Optional[AnalysisResult] = None,
) -> ReleaseEvidence:
    """Collect repo-side release evidence into a stable schema."""

    root = workspace.resolve()
    official_eval = _official_eval_status(root)
    latest_eval = _latest_eval(root, official_eval=official_eval)
    workflows = _workflow_status(root)
    doctor = _doctor_status(analysis)
    foundry = _foundry_status(analysis)
    monitoring = _monitoring_status(analysis)
    trace_dataset = _trace_dataset_status(root)
    ailz = _ailz_status(analysis)

    checks: list[ReleaseEvidenceCheck] = []
    blockers: list[str] = []
    warnings: list[str] = []
    ready: list[str] = []

    _add_eval_check(checks, blockers, warnings, ready, latest_eval)
    _add_threshold_check(checks, warnings, ready, root, latest_eval)
    _add_baseline_check(checks, warnings, ready, root, latest_eval)
    _add_workflow_checks(checks, warnings, ready, workflows)
    _add_doctor_check(checks, blockers, warnings, ready, doctor)
    _add_foundry_check(checks, warnings, ready, foundry)
    _add_monitoring_check(checks, warnings, ready, monitoring)
    _add_trace_dataset_check(checks, warnings, ready, trace_dataset)
    _add_ailz_check(checks, warnings, ready, ailz)

    status = "blocked" if blockers else "ready_with_warnings" if warnings else "ready"
    links = _links(latest_eval)
    target = latest_eval.get("target")
    generated_at = datetime.now(timezone.utc).isoformat()

    evidence = ReleaseEvidence(
        generated_at=generated_at,
        workspace=str(root),
        status=status,
        target=str(target) if target else None,
        blockers=blockers,
        warnings=warnings,
        ready=ready,
        checks=checks,
        links=links,
        latest_eval=latest_eval,
        official_eval=official_eval,
        doctor=doctor,
        workflows=workflows,
        foundry=foundry,
        monitoring=monitoring,
        trace_dataset=trace_dataset,
        ailz=ailz,
    )
    return ReleaseEvidence.model_validate(_redact_obj(evidence.model_dump()))


def write_release_evidence(
    workspace: Path,
    *,
    analysis: Optional[AnalysisResult] = None,
    evidence: Optional[ReleaseEvidence] = None,
    out_dir: Optional[Path] = None,
) -> EvidenceWriteResult:
    """Write ``evidence.json`` and ``evidence.md`` under the release folder."""

    root = workspace.resolve()
    payload = evidence or build_release_evidence(root, analysis=analysis)
    target_dir = out_dir or (root / ".agentops" / "release" / "latest")
    if not target_dir.is_absolute():
        target_dir = root / target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    json_path = target_dir / "evidence.json"
    markdown_path = target_dir / "evidence.md"
    json_payload = json.dumps(payload.model_dump(), indent=2, default=str)
    json_path.write_text(_redact_text(json_payload) + "\n", encoding="utf-8")
    markdown_path.write_text(render_release_evidence_markdown(payload), encoding="utf-8")
    return EvidenceWriteResult(payload, target_dir, json_path, markdown_path)


def render_release_evidence_markdown(evidence: ReleaseEvidence) -> str:
    """Render a concise release-evidence report for PRs and reviews."""

    icon = {"ready": "✅", "ready_with_warnings": "⚠️", "blocked": "❌"}[evidence.status]
    lines = [
        "# AgentOps Release Evidence",
        "",
        f"**Production readiness:** {icon} `{evidence.status}`",
        "",
        f"- **Generated:** {evidence.generated_at}",
        f"- **Workspace:** `{evidence.workspace}`",
    ]
    if evidence.target:
        lines.append(f"- **Target:** `{evidence.target}`")
    lines.append("")

    if evidence.blockers:
        lines.append("## Blocking items")
        lines.append("")
        for item in evidence.blockers:
            lines.append(f"- ❌ {item}")
        lines.append("")

    if evidence.warnings:
        lines.append("## Warnings")
        lines.append("")
        for item in evidence.warnings:
            lines.append(f"- ⚠️ {item}")
        lines.append("")

    if evidence.ready:
        lines.append("## Ready signals")
        lines.append("")
        for item in evidence.ready:
            lines.append(f"- ✅ {item}")
        lines.append("")

    _append_doctor_finding_summary(lines, evidence.doctor)

    lines.append("## Readiness checks")
    lines.append("")
    lines.append("| Check | Status | Summary |")
    lines.append("|---|---|---|")
    for check in evidence.checks:
        status_icon = {"ready": "✅", "warning": "⚠️", "blocked": "❌", "unknown": "❔"}[check.status]
        lines.append(f"| {check.name} | {status_icon} `{check.status}` | {_cell(check.summary)} |")
    lines.append("")

    if evidence.links:
        lines.append("## Links")
        lines.append("")
        for link in evidence.links:
            lines.append(f"- [{link.label}]({link.url})")
        lines.append("")

    return _redact_text("\n".join(lines).rstrip() + "\n")


def _append_doctor_finding_summary(lines: list[str], doctor: dict[str, Any]) -> None:
    if doctor.get("status") != "ok":
        return
    top_findings = _as_list(doctor.get("top_findings"))
    if not top_findings:
        return

    counts = doctor.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    total = doctor.get("findings_total")
    try:
        total_count = int(total)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        total_count = len(top_findings)

    count_parts = []
    for severity in ("critical", "warning", "info"):
        count = counts.get(severity)
        if count:
            count_parts.append(f"{count} {severity}")
    count_text = f" ({' · '.join(count_parts)})" if count_parts else ""

    lines.append("## Doctor finding summary")
    lines.append("")
    lines.append(f"**Findings:** {total_count}{count_text}")
    lines.append("")
    for index, raw_finding in enumerate(top_findings, start=1):
        if not isinstance(raw_finding, dict):
            continue
        severity = _cell(str(raw_finding.get("severity") or "info"))
        category = _cell(str(raw_finding.get("category") or "uncategorized")).replace("_", " ")
        finding_id = _cell(str(raw_finding.get("id") or "unknown"))
        title = _cell(str(raw_finding.get("title") or ""))
        lines.append(
            f"{index}. **{severity}** [{category}] `{finding_id}` - {title}"
        )
    if total_count > len(top_findings):
        lines.append(
            f"{len(top_findings)} shown; see `.agentops/agent/report.md` for all findings."
        )
    lines.append("")


def _latest_eval(root: Path, *, official_eval: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    local_eval = _agentops_eval_status(root)
    official = official_eval if official_eval is not None else _official_eval_status(root)
    if _is_eval_available(local_eval):
        return local_eval
    if _is_eval_available(official):
        return official
    return local_eval


def _agentops_eval_status(root: Path) -> dict[str, Any]:
    path = root / ".agentops" / "results" / "latest" / "results.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "invalid", "path": str(path), "error": "expected JSON object"}

    summary_raw = payload.get("summary")
    summary = cast(dict[str, Any], summary_raw) if isinstance(summary_raw, dict) else {}
    target_raw = payload.get("target")
    target = cast(dict[str, Any], target_raw) if isinstance(target_raw, dict) else {}
    config_raw = payload.get("config")
    config = cast(dict[str, Any], config_raw) if isinstance(config_raw, dict) else {}
    raw_metrics = (
        payload.get("aggregate_metrics")
        or payload.get("metrics")
        or payload.get("run_metrics")
        or {}
    )
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    thresholds_raw = payload.get("thresholds")
    thresholds = cast(list[Any], thresholds_raw) if isinstance(thresholds_raw, list) else []
    cloud_raw = config.get("cloud_evaluation")
    cloud = cast(dict[str, Any], cloud_raw) if isinstance(cloud_raw, dict) else {}
    comparison = payload.get("comparison")

    passed = summary.get("overall_passed")
    if passed is None:
        passed = summary.get("run_pass")
    if passed is None and isinstance(metrics, dict) and "run_pass" in metrics:
        try:
            passed = bool(float(metrics["run_pass"]))
        except (TypeError, ValueError):
            passed = None

    runner = "agentops-cloud" if cloud else "agentops-local"
    return {
        "status": "ok",
        "runner": runner,
        "path": str(path),
        "passed": passed,
        "target": target.get("raw") or config.get("agent"),
        "target_kind": target.get("kind"),
        "started_at": payload.get("started_at") or payload.get("timestamp"),
        "items_total": summary.get("items_total"),
        "items_passed_all": summary.get("items_passed_all"),
        "metrics": metrics if isinstance(metrics, dict) else {},
        "threshold_count": len(thresholds),
        "has_comparison": isinstance(comparison, dict),
        "foundry_report_url": cloud.get("report_url"),
        "cloud_evaluation": cloud,
        "machine_readable_thresholds": True,
    }


def _official_eval_status(root: Path) -> dict[str, Any]:
    directory = root / ".agentops" / "official-eval"
    metadata_path = directory / "metadata.json"
    result_path = directory / "result.json"
    if not metadata_path.exists():
        return {"status": "missing", "metadata_path": str(metadata_path)}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "metadata_path": str(metadata_path), "error": str(exc)}
    if not isinstance(metadata, dict):
        return {"status": "invalid", "metadata_path": str(metadata_path), "error": "expected JSON object"}

    result: dict[str, Any] = {}
    result_error: str | None = None
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                result = loaded
            else:
                result_error = "expected JSON object"
        except (OSError, json.JSONDecodeError) as exc:
            result_error = str(exc)

    passed = _official_eval_passed(result)
    payload: dict[str, Any] = {
        "status": "ok",
        "runner": OFFICIAL_EVAL_RUNNER,
        "path": str(metadata_path),
        "metadata_path": str(metadata_path),
        "result_path": str(result_path) if result_path.exists() else None,
        "result_recorded": result_path.exists() and result_error is None,
        "passed": passed,
        "target": metadata.get("agent_ids"),
        "target_kind": "foundry_prompt",
        "deployment_name": metadata.get("deployment_name"),
        "data_path": metadata.get("data_path"),
        "items_total": metadata.get("items_total"),
        "official_evaluators": metadata.get("official_evaluators") if isinstance(metadata.get("official_evaluators"), list) else [],
        "skipped_agentops_evaluators": metadata.get("skipped_agentops_evaluators") if isinstance(metadata.get("skipped_agentops_evaluators"), list) else [],
        "warnings": metadata.get("warnings") if isinstance(metadata.get("warnings"), list) else [],
        "machine_readable_thresholds": False,
        "has_comparison": False,
        "action": metadata.get("action"),
        "azure_devops_task": metadata.get("azure_devops_task"),
        "result": result,
    }
    if result_error:
        payload["result_error"] = result_error
    return payload


def _workflow_status(root: Path) -> dict[str, Any]:
    github = root / ".github" / "workflows"
    ado = root / ".azuredevops" / "pipelines"
    pr = (github / "agentops-pr.yml").exists() or (ado / "agentops-pr.yml").exists()
    deploy_files = list(github.glob("agentops-deploy-*.yml")) + list(ado.glob("agentops-deploy-*.yml"))
    scheduled_doctor = (
        (github / "agentops-doctor.yml").exists()
        or (ado / "agentops-doctor.yml").exists()
        or (github / "agentops-watchdog.yml").exists()
        or (ado / "agentops-watchdog.yml").exists()
    )
    return {
        "pr_gate": pr,
        "deploy_workflows": [str(p.relative_to(root)) for p in deploy_files],
        "deploy_count": len(deploy_files),
        "scheduled_doctor": scheduled_doctor,
        # Legacy key kept so existing evidence consumers do not break.
        "watchdog": scheduled_doctor,
        "github_workflows": github.is_dir(),
        "azure_devops_pipelines": ado.is_dir(),
    }


def _doctor_status(analysis: Optional[AnalysisResult]) -> dict[str, Any]:
    if analysis is None:
        return {"status": "not_run"}
    counts = {"critical": 0, "warning": 0, "info": 0}
    for finding in analysis.findings:
        counts[finding.severity.value] += 1
    return {
        "status": "ok",
        "findings_total": len(analysis.findings),
        "max_severity": analysis.max_severity.value if analysis.max_severity else None,
        "counts": counts,
        "top_findings": [
            {
                "id": f.id,
                "severity": f.severity.value,
                "title": f.title,
                "category": f.category.value,
            }
            for f in analysis.findings[:10]
        ],
    }


def _foundry_status(analysis: Optional[AnalysisResult]) -> dict[str, Any]:
    if analysis is None or analysis.foundry is None:
        return {"status": "not_run"}
    foundry = analysis.foundry
    diag = dict(foundry.diagnostics or {})
    enabled_rules = [r for r in foundry.evaluation_rules if r.enabled is not False]
    return {
        "status": diag.get("status", "unknown"),
        "agents_count": len(foundry.agents),
        "evaluation_rules_count": len(foundry.evaluation_rules),
        "enabled_evaluation_rules": len(enabled_rules),
        "diagnostics": diag,
    }


def _monitoring_status(analysis: Optional[AnalysisResult]) -> dict[str, Any]:
    if analysis is None or analysis.monitor is None:
        return {"status": "not_run"}
    monitor = analysis.monitor
    return {
        "status": (monitor.diagnostics or {}).get("status", "unknown"),
        "request_count": monitor.request_count,
        "error_rate": monitor.error_rate,
        "p95_duration_seconds": monitor.p95_duration_seconds,
        "input_token_count": monitor.input_token_count,
        "output_token_count": monitor.output_token_count,
        "rate_limit_429_count": monitor.rate_limit_429_count,
        "diagnostics": dict(monitor.diagnostics or {}),
    }


def _trace_dataset_status(root: Path) -> dict[str, Any]:
    manifest = root / ".agentops" / "data" / "trace-regression-manifest.json"
    if not manifest.exists():
        return {"status": "missing", "manifest": str(manifest)}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "manifest": str(manifest), "error": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "invalid", "manifest": str(manifest), "error": "expected JSON object"}
    return {"status": "ok", "manifest": str(manifest), **payload}


def _ailz_status(analysis: Optional[AnalysisResult]) -> dict[str, Any]:
    if analysis is None:
        return {"status": "not_run"}
    readiness = [f for f in analysis.findings if f.id == "opex.ailz_readiness"]
    gaps = [f for f in analysis.findings if f.id == "opex.ailz_gaps"]
    if not readiness and not gaps:
        return {"status": "not_detected"}
    return {
        "status": "gaps" if gaps else "ready",
        "readiness": [f.summary for f in readiness],
        "gaps": [gap for f in gaps for gap in _as_list(f.evidence.get("gaps"))],
    }


def _add_eval_check(
    checks: list[ReleaseEvidenceCheck],
    blockers: list[str],
    warnings: list[str],
    ready: list[str],
    latest_eval: dict[str, Any],
) -> None:
    status = latest_eval.get("status")
    if status != "ok":
        message = "No latest evaluation result was found; run `agentops eval run` or the generated eval workflow before treating this agent as production-ready."
        blockers.append(message)
        checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="blocked", summary=message, evidence=latest_eval))
        return
    if latest_eval.get("runner") == OFFICIAL_EVAL_RUNNER:
        if latest_eval.get("passed") is False:
            message = "Official AI Agent Evaluation did not complete successfully; review the CI job before promotion."
            blockers.append(message)
            checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="blocked", summary=message, evidence=latest_eval))
            return
        if latest_eval.get("passed") is True:
            message = (
                "Official AI Agent Evaluation completed, but it does not emit "
                "AgentOps-normalized threshold evidence; run `agentops eval run` "
                "so release evidence can enforce quality thresholds."
            )
            blockers.append(message)
            checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="blocked", summary=message, evidence=latest_eval))
            return
        message = (
            "Official AI Agent Evaluation input is present, but no "
            "AgentOps-normalized pass/fail result was recorded."
        )
        blockers.append(message)
        checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="blocked", summary=message, evidence=latest_eval))
        return
    if latest_eval.get("passed") is False:
        message = "Latest evaluation failed one or more thresholds."
        blockers.append(message)
        checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="blocked", summary=message, evidence=latest_eval))
        return
    if latest_eval.get("passed") is True:
        message = "Latest evaluation passed configured thresholds."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="ready", summary=message, evidence=latest_eval))
        return
    message = "Latest evaluation exists, but pass/fail status could not be determined."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Latest eval gate", status="warning", summary=message, evidence=latest_eval))


def _add_threshold_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    root: Path,
    latest_eval: dict[str, Any],
) -> None:
    config = _agentops_config(root)
    thresholds = config.get("thresholds") if isinstance(config, dict) else None
    if isinstance(thresholds, dict) and thresholds:
        if latest_eval.get("runner") == OFFICIAL_EVAL_RUNNER:
            message = (
                "agentops.yaml declares thresholds, but the official AI Agent Evaluation "
                "runner does not emit AgentOps-normalized threshold evidence yet."
            )
            warnings.append(message)
            checks.append(ReleaseEvidenceCheck(name="Threshold policy", status="warning", summary=message, evidence={"thresholds": list(thresholds)}))
            return
        message = "Explicit production thresholds are declared in agentops.yaml."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Threshold policy", status="ready", summary=message, evidence={"thresholds": list(thresholds)}))
        return
    message = "No explicit thresholds are declared; defaults are useful for exploration but weak for production gates."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Threshold policy", status="warning", summary=message))


def _add_baseline_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    root: Path,
    latest_eval: dict[str, Any],
) -> None:
    has_baseline = (root / ".agentops" / "baseline" / "results.json").exists() or bool(latest_eval.get("has_comparison"))
    if has_baseline:
        message = "A baseline or comparison is available for regression decisions."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Regression baseline", status="ready", summary=message))
        return
    message = "No baseline comparison was found; capture a known-good results.json before promoting production releases."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Regression baseline", status="warning", summary=message))


def _add_workflow_checks(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    workflows: dict[str, Any],
) -> None:
    if workflows.get("pr_gate"):
        message = "AgentOps PR gate workflow is present."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="PR gate", status="ready", summary=message, evidence=workflows))
    else:
        message = "No AgentOps PR gate workflow was found."
        warnings.append(message)
        checks.append(ReleaseEvidenceCheck(name="PR gate", status="warning", summary=message, evidence=workflows))

    if int(workflows.get("deploy_count") or 0) > 0:
        message = "Environment deploy workflows are present."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Deploy workflows", status="ready", summary=message, evidence=workflows))
    else:
        message = "No AgentOps deploy workflow was found for dev/qa/prod promotion."
        warnings.append(message)
        checks.append(ReleaseEvidenceCheck(name="Deploy workflows", status="warning", summary=message, evidence=workflows))

    if workflows.get("scheduled_doctor") or workflows.get("watchdog"):
        ready.append("Optional scheduled AgentOps Doctor workflow is present.")


def _add_doctor_check(
    checks: list[ReleaseEvidenceCheck],
    blockers: list[str],
    warnings: list[str],
    ready: list[str],
    doctor: dict[str, Any],
) -> None:
    if doctor.get("status") != "ok":
        message = "Doctor was not run for this evidence pack."
        warnings.append(message)
        checks.append(ReleaseEvidenceCheck(name="Doctor readiness", status="warning", summary=message))
        return
    max_severity = doctor.get("max_severity")
    if max_severity == Severity.CRITICAL.value:
        message = "Doctor reported critical findings."
        blockers.append(message)
        checks.append(ReleaseEvidenceCheck(name="Doctor readiness", status="blocked", summary=message, evidence=doctor))
    elif max_severity == Severity.WARNING.value:
        message = "Doctor reported warnings that should be reviewed before production."
        warnings.append(message)
        checks.append(ReleaseEvidenceCheck(name="Doctor readiness", status="warning", summary=message, evidence=doctor))
    else:
        message = "Doctor reported no blocking or warning findings."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Doctor readiness", status="ready", summary=message, evidence=doctor))


def _add_foundry_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    foundry: dict[str, Any],
) -> None:
    if foundry.get("status") == "ok":
        ready.append("Foundry control-plane source is reachable.")
        if int(foundry.get("enabled_evaluation_rules") or 0) > 0:
            ready.append("Foundry continuous evaluation rules are enabled.")
            checks.append(ReleaseEvidenceCheck(name="Foundry continuous evaluation", status="ready", summary="Foundry continuous evaluation rules are enabled.", evidence=foundry))
        else:
            message = "Foundry control-plane is reachable, but no enabled continuous evaluation rule was detected."
            warnings.append(message)
            checks.append(ReleaseEvidenceCheck(name="Foundry continuous evaluation", status="warning", summary=message, evidence=foundry))
        return
    message = "Foundry control-plane readiness is unknown; configure `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` for production evidence."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Foundry control plane", status="warning", summary=message, evidence=foundry))


def _add_monitoring_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    monitoring: dict[str, Any],
) -> None:
    if monitoring.get("status") == "ok":
        ready.append("Application Insights / Azure Monitor source is reachable.")
        checks.append(ReleaseEvidenceCheck(name="Runtime monitoring", status="ready", summary="Application Insights / Azure Monitor source is reachable.", evidence=monitoring))
        return
    message = "Application Insights / Azure Monitor readiness is unknown; production traces and runtime metrics may not be available."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Runtime monitoring", status="warning", summary=message, evidence=monitoring))


def _add_trace_dataset_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    trace_dataset: dict[str, Any],
) -> None:
    if trace_dataset.get("status") == "ok":
        rows = trace_dataset.get("rows")
        message = f"Production trace regression dataset is available ({rows} row(s))."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="Trace-to-dataset flywheel", status="ready", summary=message, evidence=trace_dataset))
        return
    message = "No production trace regression dataset was found yet; harvest reviewed traces to turn production issues into regression tests."
    warnings.append(message)
    checks.append(ReleaseEvidenceCheck(name="Trace-to-dataset flywheel", status="warning", summary=message, evidence=trace_dataset))


def _add_ailz_check(
    checks: list[ReleaseEvidenceCheck],
    warnings: list[str],
    ready: list[str],
    ailz: dict[str, Any],
) -> None:
    status = ailz.get("status")
    if status == "not_detected":
        checks.append(ReleaseEvidenceCheck(name="AI Landing Zone readiness", status="unknown", summary="No AI Landing Zone signals were detected for this workspace.", evidence=ailz))
    elif status == "ready":
        message = "AI Landing Zone readiness signals are wired."
        ready.append(message)
        checks.append(ReleaseEvidenceCheck(name="AI Landing Zone readiness", status="ready", summary=message, evidence=ailz))
    elif status == "gaps":
        message = "AI Landing Zone signals were detected, but gaps remain."
        warnings.append(message)
        checks.append(ReleaseEvidenceCheck(name="AI Landing Zone readiness", status="warning", summary=message, evidence=ailz))
    else:
        checks.append(ReleaseEvidenceCheck(name="AI Landing Zone readiness", status="unknown", summary="AI Landing Zone readiness was not evaluated.", evidence=ailz))


def _links(latest_eval: dict[str, Any]) -> list[ReleaseEvidenceLink]:
    links: list[ReleaseEvidenceLink] = []
    report_url = latest_eval.get("foundry_report_url")
    if report_url:
        links.append(ReleaseEvidenceLink(label="Foundry evaluation report", url=str(report_url)))
    return links


def _official_eval_passed(result: dict[str, Any]) -> Optional[bool]:
    for key in ("status", "outcome", "conclusion", "job_status"):
        value = result.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower().replace("_", "").replace("-", "")
        if normalized in {"success", "succeeded"}:
            return True
        if normalized in {"failure", "failed", "cancelled", "canceled", "timedout"}:
            return False
    return None


def _is_eval_available(eval_status: dict[str, Any]) -> bool:
    return eval_status.get("status") == "ok"


def _evidence_mtime(eval_status: dict[str, Any]) -> float:
    for key in ("result_path", "metadata_path", "path"):
        value = eval_status.get(key)
        if not value:
            continue
        path = Path(str(value))
        try:
            return path.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _agentops_config(root: Path) -> dict[str, Any]:
    path = root / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _redact_text(text: str) -> str:
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    return value
