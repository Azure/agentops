"""Read-only CI/CD analysis for Azure AI accelerator repositories."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agentops.core.agentops_config import classify_agent
from agentops.core.azd_eval import find_eval_yaml, load_eval_recipe, recipe_metric_names
from agentops.pipeline.official_eval import (
    AGENTOPS_CLOUD_RUNNER,
    AGENTOPS_LOCAL_RUNNER,
    AZD_EVAL_RUNNER,
    OFFICIAL_EVAL_RUNNER,
    analyze_official_eval_support,
    official_eval_action_ref,
)
from agentops.utils.yaml import load_yaml

_TEXT_LIMIT = 200_000
_SCAN_LIMIT = 80
_TEXT_WRAP_WIDTH = 92
_IGNORE_PARTS = {
    ".agentops",
    ".azure",
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
}


@dataclass(frozen=True)
class WorkflowSignal:
    """A local file-system signal used to classify CI/CD shape."""

    key: str
    label: str
    detail: str
    path: Optional[str] = None
    confidence: str = "high"

    def to_dict(self) -> Dict[str, str]:
        data = {
            "key": self.key,
            "label": self.label,
            "detail": self.detail,
            "confidence": self.confidence,
        }
        if self.path:
            data["path"] = self.path
        return data


@dataclass(frozen=True)
class WorkflowStage:
    """Recommended pipeline stage."""

    name: str
    owner: str
    purpose: str
    commands: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "purpose": self.purpose,
            "commands": list(self.commands),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class WorkflowAnalysis:
    """Stable result contract for `agentops workflow analyze`."""

    version: int
    directory: str
    classification: str
    recommended_deploy_mode: str
    recommended_eval_runner: str
    deployment_strategy: str
    eval_strategy: str
    complexity: str
    requires_copilot_adaptation: bool
    copilot_skills_installed: bool
    copilot_prompt: Optional[str] = None
    official_eval_reasons: List[str] = field(default_factory=list)
    official_evaluators: List[str] = field(default_factory=list)
    signals: List[WorkflowSignal] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommended_commands: List[str] = field(default_factory=list)
    stages: List[WorkflowStage] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "directory": self.directory,
            "classification": self.classification,
            "recommended_deploy_mode": self.recommended_deploy_mode,
            "recommended_eval_runner": self.recommended_eval_runner,
            "deployment_strategy": self.deployment_strategy,
            "eval_strategy": self.eval_strategy,
            "complexity": self.complexity,
            "requires_copilot_adaptation": self.requires_copilot_adaptation,
            "copilot_skills_installed": self.copilot_skills_installed,
            "copilot_prompt": self.copilot_prompt,
            "official_eval_reasons": list(self.official_eval_reasons),
            "official_evaluators": list(self.official_evaluators),
            "signals": [signal.to_dict() for signal in self.signals],
            "warnings": list(self.warnings),
            "recommended_commands": list(self.recommended_commands),
            "stages": [stage.to_dict() for stage in self.stages],
            "next_steps": list(self.next_steps),
        }


def analyze_workflow_project(directory: Path) -> WorkflowAnalysis:
    """Analyze a copied accelerator/app repo and recommend CI/CD shape.

    This is intentionally local-only: it does not call Azure, `azd`, GitHub, or
    Foundry. The goal is to give a coding agent and the user a grounded plan
    before generating or adapting workflow files.
    """

    root = directory.resolve()
    signals: List[WorkflowSignal] = []
    warnings: List[str] = []

    azure_yaml = root / "azure.yaml"
    has_azd = azure_yaml.exists()
    if has_azd:
        signals.append(
            WorkflowSignal(
                "azd_project",
                "Azure Developer CLI project",
                "azure.yaml found; prefer azd for provision/deploy lifecycle.",
                _rel(root, azure_yaml),
            )
        )

    agentops = _agentops_signal(root)
    prompt_agent = bool(agentops.get("prompt_agent"))
    if agentops:
        signals.append(agentops["signal"])
        if agentops.get("prompt_file"):
            signals.append(
                WorkflowSignal(
                    "prompt_file",
                    "Source-controlled prompt file",
                    "Prompt-agent CI/CD can create a candidate Foundry version from this file.",
                    str(agentops["prompt_file"]),
                )
            )
        if prompt_agent and not agentops.get("prompt_agent_bootstrap"):
            signals.append(
                WorkflowSignal(
                    "prompt_agent_bootstrap_missing",
                    "Prompt-agent bootstrap defaults",
                    (
                        "agentops.yaml targets a Foundry prompt agent but does not declare "
                        "`prompt_agent_bootstrap`. Without it, CI in an empty target Foundry "
                        "project (dev/qa/prod) fails with a 404 instead of auto-creating the "
                        "first version. Add `prompt_agent_bootstrap: { model: <deployment> }` "
                        "to enable auto-bootstrap."
                    ),
                    "agentops.yaml",
                    confidence="medium",
                )
            )

    bicep_files = _find_files(root, "*.bicep")
    if bicep_files:
        signals.append(
            WorkflowSignal(
                "bicep_infra",
                "Bicep infrastructure",
                f"Found {len(bicep_files)} Bicep file(s); keep infra changes behind azd/Bicep review.",
                _rel(root, bicep_files[0]),
            )
        )

    manifest = _read_json(root / "manifest.json")
    ailz_manifest = isinstance(manifest, dict) and any(
        key in manifest for key in ("ailz_tag", "ailz_version", "components")
    )
    if ailz_manifest:
        signals.append(
            WorkflowSignal(
                "ailz_manifest",
                "AI Landing Zone manifest",
                "manifest.json pins landing-zone/components; treat it as release input.",
                "manifest.json",
            )
        )

    ailz_preflight = has_ailz_preflight(root)
    if ailz_preflight:
        signals.append(
            WorkflowSignal(
                "ailz_preflight",
                "AI Landing Zone preflight",
                "Invoke-PreflightChecks.ps1 found; run it before azd provision in CI/CD.",
                "scripts/Invoke-PreflightChecks.ps1",
            )
        )

    infra_text = "\n".join(_read_text(path) for path in _infra_scan_files(root, bicep_files))
    readme_text = _read_text(root / "README.md")
    readme_lower = readme_text.lower()

    network_isolated = _has_network_isolation(infra_text)
    if network_isolated:
        signals.append(
            WorkflowSignal(
                "network_isolation",
                "Network-isolated Azure AI topology",
                "Structural infra mentions private endpoints, VNet/jumpbox, firewall, or NETWORK_ISOLATION.",
                confidence="high",
            )
        )
        warnings.append(
            "Network-isolated deployments often cannot complete all data-plane "
            "steps from GitHub-hosted runners. Use azd hooks plus a self-hosted "
            "runner in the VNet, jumpbox handoff, or ACR Tasks agent pool for "
            "private build/deploy/post-provision work."
        )
    elif "network isolation" in readme_lower or "private endpoint" in readme_lower:
        signals.append(
            WorkflowSignal(
                "network_isolation_hint",
                "Network isolation mentioned",
                "README mentions network isolation/private endpoints; verify infra before choosing runner topology.",
                "README.md",
                confidence="medium",
            )
        )

    if _looks_like_container_app(root, infra_text, readme_lower):
        signals.append(
            WorkflowSignal(
                "container_app",
                "Containerized Azure app",
                "Container App/Docker signals found; build and deploy steps are project-specific.",
                confidence="high",
            )
        )

    accelerator_hint = _accelerator_hint(readme_lower)
    if accelerator_hint:
        signals.append(accelerator_hint)

    existing_ci = _existing_ci_signal(root)
    if existing_ci:
        signals.append(existing_ci)

    if not (root / "agentops.yaml").exists():
        warnings.append(
            "No agentops.yaml found. Run `agentops init` and prove `agentops eval run` locally before making the pipeline blocking."
        )

    official_eval_reasons: List[str] = []
    official_evaluators: List[str] = []
    recommended_eval_runner = AGENTOPS_LOCAL_RUNNER
    if (root / "agentops.yaml").exists():
        azd_recipe = _azd_eval_recipe(root)
        official_support = analyze_official_eval_support(root / "agentops.yaml")
        official_eval_reasons = list(official_support.reasons)
        official_evaluators = list(official_support.official_evaluators)
        if azd_recipe is not None:
            recommended_eval_runner = AZD_EVAL_RUNNER
            try:
                recipe = load_eval_recipe(azd_recipe)
                official_evaluators = sorted(recipe_metric_names(recipe))
            except Exception:
                official_evaluators = []
            rel_recipe = _relative_path(azd_recipe, root)
            official_eval_reasons = [
                f"Found azd eval recipe at `{rel_recipe}`.",
                "azd/Foundry own eval assets; AgentOps will enforce thresholds and evidence.",
            ]
            signals.append(
                WorkflowSignal(
                    "azd_ai_agent_eval",
                    "azd AI agent eval",
                    "eval.yaml found; CI should run `agentops eval run` on the azd backend with a pinned azure.ai.agents extension.",
                    rel_recipe,
                )
            )
        elif official_support.eligible:
            recommended_eval_runner = AGENTOPS_CLOUD_RUNNER
            signals.append(
                WorkflowSignal(
                    "agentops_cloud_evaluation",
                    "AgentOps cloud eval runner",
                    "prompt agent and dataset are compatible; CI can run Foundry cloud eval with AgentOps threshold enforcement.",
                    "agentops.yaml",
                )
            )
            warnings.extend(official_support.warnings)
        else:
            warnings.append(
                "Microsoft Foundry AI Agent Evaluation not selected: "
                + " ".join(official_support.reasons)
            )

    recommended_deploy_mode = _recommended_deploy_mode(has_azd, prompt_agent)
    classification = _classification(has_azd, prompt_agent, network_isolated, ailz_manifest, accelerator_hint)
    complexity = _complexity(network_isolated, has_azd, bicep_files, accelerator_hint, ailz_manifest)
    deployment_strategy = _deployment_strategy(recommended_deploy_mode, network_isolated, ailz_preflight)
    eval_strategy = _eval_strategy(recommended_eval_runner)
    requires_copilot = (
        recommended_deploy_mode == "placeholder"
        or network_isolated
        or bool(accelerator_hint)
        or len(bicep_files) > 5
    )

    skills_installed = _skills_installed(root)
    copilot_prompt = _copilot_prompt(classification, recommended_deploy_mode, network_isolated) if requires_copilot else None
    recommended_commands = [
        "agentops workflow analyze --format markdown",
        f"agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode {recommended_deploy_mode} --force",
    ]
    if ailz_preflight:
        recommended_commands.insert(1, "pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict")
    if requires_copilot and not skills_installed:
        recommended_commands.insert(1, "agentops skills install --platform copilot")
    if has_azd:
        recommended_commands.append("azd provision")
        recommended_commands.append("azd deploy")

    stages = _stages(
        recommended_deploy_mode,
        recommended_eval_runner,
        network_isolated,
        prompt_agent,
        ailz_preflight,
    )
    next_steps = _next_steps(
        recommended_deploy_mode,
        recommended_eval_runner,
        requires_copilot,
        network_isolated,
        skills_installed,
        ailz_preflight,
    )

    return WorkflowAnalysis(
        version=1,
        directory=str(root),
        classification=classification,
        recommended_deploy_mode=recommended_deploy_mode,
        recommended_eval_runner=recommended_eval_runner,
        deployment_strategy=deployment_strategy,
        eval_strategy=eval_strategy,
        complexity=complexity,
        requires_copilot_adaptation=requires_copilot,
        copilot_skills_installed=skills_installed,
        copilot_prompt=copilot_prompt,
        official_eval_reasons=official_eval_reasons,
        official_evaluators=official_evaluators,
        signals=signals,
        warnings=warnings,
        recommended_commands=recommended_commands,
        stages=stages,
        next_steps=next_steps,
    )


def recommended_deploy_mode(directory: Path) -> str:
    """Return the same deploy-mode decision used by workflow generation."""
    return analyze_workflow_project(directory).recommended_deploy_mode


def recommended_eval_runner(directory: Path) -> str:
    """Return the same eval-runner decision used by workflow generation."""
    return analyze_workflow_project(directory).recommended_eval_runner


def _display_eval_runner(eval_runner: str) -> str:
    if eval_runner == AZD_EVAL_RUNNER:
        return "azd AI agent eval"
    if eval_runner == AGENTOPS_CLOUD_RUNNER:
        return "AgentOps cloud eval in Foundry"
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        return "Microsoft Foundry AI Agent Evaluation"
    if eval_runner == AGENTOPS_LOCAL_RUNNER:
        return "AgentOps local eval"
    return eval_runner


def has_ailz_preflight(directory: Path) -> bool:
    """Return True when the official AI Landing Zone preflight script exists."""
    root = directory.resolve()
    return (root / "scripts" / "Invoke-PreflightChecks.ps1").exists()


def render_workflow_analysis(analysis: WorkflowAnalysis, output_format: str = "text") -> str:
    """Render analysis as text, Markdown, or JSON."""
    if output_format == "json":
        return json.dumps(analysis.to_dict(), indent=2) + "\n"
    if output_format == "markdown":
        return _render_markdown(analysis)
    if output_format == "text":
        return _render_text(analysis)
    raise ValueError("output_format must be text, markdown, or json")


def _render_text(analysis: WorkflowAnalysis) -> str:
    lines = [
        "AgentOps workflow analysis",
        f"Workspace: {analysis.directory}",
        f"Project: {analysis.classification}",
        "",
        "Recommendation",
    ]
    lines.extend(_render_text_recommendation(analysis))
    lines.append("")
    lines.append("Signals")
    lines.extend(_render_text_signal_rows(_signal_rows(analysis)))
    if analysis.warnings:
        lines.append("")
        lines.append("Warnings")
        for warning in analysis.warnings:
            lines.extend(_wrapped_status_line("warn", "warning", warning))
    if analysis.official_eval_reasons:
        lines.append("")
        lines.append("Foundry eval")
        lines.extend(_render_text_foundry_eval_rows(_foundry_eval_rows(analysis)))
    if analysis.copilot_prompt:
        lines.append("")
        lines.append("Copilot handoff")
        lines.extend(_wrapped_status_line("todo", "copy/paste", analysis.copilot_prompt))
    lines.append("")
    lines.append("Pipeline plan")
    for index, stage in enumerate(analysis.stages, start=1):
        lines.append(f"  {index}. {stage.name}")
        lines.extend(_wrap_text(stage.purpose, indent="     "))
    commands = _text_commands(analysis.recommended_commands)
    if commands:
        lines.append("")
        lines.append("Commands")
        lines.extend(f"  {command}" for command in commands)
    lines.append("")
    lines.append("Next")
    for index, step in enumerate(analysis.next_steps, start=1):
        lines.extend(_wrapped_numbered_step(index, step))
    return "\n".join(lines) + "\n"


def _render_markdown(analysis: WorkflowAnalysis) -> str:
    lines = [
        "# AgentOps workflow analysis",
        "",
        f"- **Directory:** `{analysis.directory}`",
        f"- **Classification:** {analysis.classification}",
        "",
        "## Workflow decision checklist",
        "",
    ]
    lines.extend(_render_markdown_table(("Check", "Status", "Explanation"), _decision_checklist_rows(analysis)))
    lines.extend(
        [
            "",
            "## Detected signals",
            "",
        ]
    )
    lines.extend(_render_markdown_table(("Status", "Type", "Finding", "Evidence"), _signal_rows(analysis)))
    if analysis.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in analysis.warnings)
    if analysis.official_eval_reasons:
        lines.extend(["", "## Foundry eval checks", ""])
        lines.extend(_render_markdown_table(("Status", "Check", "Explanation"), _foundry_eval_rows(analysis)))
    if analysis.copilot_prompt:
        lines.extend(["", "## Copilot handoff", ""])
        lines.extend(["Copy/paste this into Copilot:", "", "```text", analysis.copilot_prompt, "```"])
    lines.extend(["", "## Recommended commands", ""])
    lines.extend(f"```bash\n{command}\n```" for command in analysis.recommended_commands)
    lines.extend(["", "## Pipeline stages", ""])
    for stage in analysis.stages:
        lines.append(f"### {stage.name}")
        lines.append("")
        lines.append(f"- **Owner:** {stage.owner}")
        lines.append(f"- **Purpose:** {stage.purpose}")
        if stage.commands:
            lines.append("- **Commands:** " + ", ".join(f"`{c}`" for c in stage.commands))
        for note in stage.notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(["## Next steps", ""])
    lines.extend(f"- {step}" for step in analysis.next_steps)
    return "\n".join(lines).rstrip() + "\n"


def _render_text_recommendation(analysis: WorkflowAnalysis) -> List[str]:
    adaptation_value = (
        "needed - review project-specific build/deploy steps"
        if analysis.requires_copilot_adaptation
        else "not needed - generated workflow should work as-is"
    )
    skills_value = (
        "installed - available for workflow adaptation handoff"
        if analysis.copilot_skills_installed
        else (
            "missing - run `agentops skills install --platform copilot` for handoff"
            if analysis.requires_copilot_adaptation
            else "not needed - no Copilot handoff for this project shape"
        )
    )
    return _render_text_fields(
        [
            ("deploy", analysis.recommended_deploy_mode),
            ("evaluate", _display_eval_runner(analysis.recommended_eval_runner)),
            ("workflow edits", adaptation_value),
            ("Copilot skills", skills_value),
        ]
    )


def _text_commands(commands: Sequence[str]) -> List[str]:
    return [command for command in commands if command != "agentops workflow analyze --format markdown"]


def _render_text_fields(rows: Sequence[tuple[str, str]]) -> List[str]:
    width = max(len(label) for label, _ in rows)
    lines: List[str] = []
    for label, value in rows:
        lines.extend(_wrap_text(value, indent=f"  {label.ljust(width)}  "))
    return lines


def _render_text_signal_rows(rows: Sequence[Sequence[str]]) -> List[str]:
    lines: List[str] = []
    for status, signal_type, _finding, evidence in rows:
        marker, _ = _split_status_value(str(status))
        detail = _soften_text(str(evidence))
        lines.extend(_wrapped_status_line(_status_word(marker), str(signal_type), detail))
    return lines


def _render_text_foundry_eval_rows(rows: Sequence[Sequence[str]]) -> List[str]:
    lines: List[str] = []
    for status, check, explanation in rows:
        marker, _ = _split_status_value(str(status))
        lines.extend(
            _wrapped_status_line(
                _status_word(marker),
                str(check),
                _friendly_foundry_eval_text(str(check), str(explanation)),
            )
        )
    return lines


def _split_status_value(status: str) -> tuple[str, str]:
    if status.startswith("[") and "]" in status:
        marker, _, value = status.partition(" ")
        return marker, value.strip()
    return status, ""


def _status_word(marker: str) -> str:
    if marker == "[x]":
        return "ok"
    if marker == "[?]":
        return "hint"
    if marker == "[ ]":
        return "todo"
    return marker.strip("[]").lower() or "info"


def _wrapped_status_line(status: str, label: str, text: str) -> List[str]:
    prefix = f"  {status.ljust(4)} {label.ljust(13)} "
    wrapped = textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [prefix.rstrip()]


def _wrapped_numbered_step(index: int, text: str) -> List[str]:
    prefix = f"  {index}. "
    wrapped = textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [prefix.rstrip()]


def _friendly_foundry_eval_text(check: str, text: str) -> str:
    if check == "Agent target":
        return "Foundry prompt agent (`name:version`)."
    if check == "Evaluators":
        return _friendly_evaluator_list(text.split(", "))
    return _soften_text(text)


def _friendly_evaluator_list(evaluators: Iterable[str]) -> str:
    return ", ".join(
        evaluator.removeprefix("builtin.").replace("_", " ")
        for evaluator in evaluators
        if evaluator
    )


def _soften_text(text: str) -> str:
    return text.replace("foundry_prompt", "Foundry prompt agent")


def _wrap_text(text: str, *, indent: str) -> List[str]:
    return textwrap.wrap(
        text,
        width=_TEXT_WRAP_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [indent.rstrip()]


def _render_markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    normalized = [[_escape_markdown_cell(str(cell)) for cell in row] for row in rows]
    if not normalized:
        normalized = [["-" for _ in headers]]
    header_line = "| " + " | ".join(_escape_markdown_cell(str(header)) for header in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in normalized]
    return [header_line, separator, *body]


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _decision_checklist_rows(analysis: WorkflowAnalysis) -> List[tuple[str, str, str]]:
    if analysis.requires_copilot_adaptation:
        adaptation_status = "[ ] needs edits"
        adaptation_detail = (
            "Detected project-specific topology or deploy signals; review generated "
            "workflow before making it blocking."
        )
    else:
        adaptation_status = "[x] not needed"
        adaptation_detail = "Generated workflow should be directly usable for this project shape."

    if analysis.requires_copilot_adaptation:
        skills_status = "[x] installed" if analysis.copilot_skills_installed else "[ ] missing"
        skills_detail = "Needed only for the Copilot workflow-adaptation handoff."
    else:
        skills_status = "[x] not required"
        skills_detail = "No workflow handoff is required for the current recommendation."

    return [
        (
            "Deploy mode",
            f"[x] {analysis.recommended_deploy_mode}",
            _deploy_mode_check_detail(analysis.recommended_deploy_mode),
        ),
        (
            "Eval runner",
            f"[x] {_display_eval_runner(analysis.recommended_eval_runner)}",
            _eval_runner_check_detail(analysis.recommended_eval_runner),
        ),
        ("Complexity", "[x] " + analysis.complexity, "Used to decide whether CI needs extra review."),
        ("Workflow adaptation", adaptation_status, adaptation_detail),
        ("Copilot skills", skills_status, skills_detail),
    ]


def _deploy_mode_check_detail(mode: str) -> str:
    if mode == "azd":
        return "Use azd for provision/deploy; AgentOps supplies gates and evidence."
    if mode == "prompt-agent":
        return "Stage and evaluate a Foundry prompt candidate, then record deployment."
    return "Generate CI placeholders; add the project-specific build/deploy steps."


def _eval_runner_check_detail(eval_runner: str) -> str:
    if eval_runner == AZD_EVAL_RUNNER:
        return "azd/Foundry run the eval from eval.yaml; AgentOps enforces thresholds and writes evidence."
    if eval_runner == AGENTOPS_CLOUD_RUNNER:
        return "Foundry executes the eval; AgentOps downloads results, applies thresholds, and writes evidence."
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        return "Prompt agent plus dataset fit Foundry eval; AgentOps keeps evidence."
    return "AgentOps runs local eval and writes normalized results/report artifacts."


def _signal_rows(analysis: WorkflowAnalysis) -> List[tuple[str, str, str, str]]:
    if not analysis.signals:
        return [
            (
                "[ ]",
                "Signals",
                "No strong project signals",
                "No accelerator, azd, AgentOps, or CI files were detected.",
            )
        ]
    return [
        (
            "[x]" if signal.confidence == "high" else "[?]",
            _signal_type(signal.key),
            signal.label,
            signal.detail + (f" ({signal.path})" if signal.path else ""),
        )
        for signal in analysis.signals
    ]


def _foundry_eval_rows(analysis: WorkflowAnalysis) -> List[tuple[str, str, str]]:
    selected = analysis.recommended_eval_runner in {
        AZD_EVAL_RUNNER,
        AGENTOPS_CLOUD_RUNNER,
        OFFICIAL_EVAL_RUNNER,
    }
    if selected:
        rows = [
            (
                "[x]",
                "Agent target",
                analysis.official_eval_reasons[0]
                if analysis.official_eval_reasons
                else "Foundry prompt agent.",
            ),
            (
                "[x]",
                "Dataset",
                analysis.official_eval_reasons[1]
                if len(analysis.official_eval_reasons) > 1
                else "Compatible with Foundry cloud eval.",
            ),
        ]
        if analysis.official_evaluators:
            rows.append(("[x]", "Evaluators", ", ".join(analysis.official_evaluators)))
        return rows

    return [
        ("[ ]", "Microsoft Foundry eval", reason)
        for reason in analysis.official_eval_reasons
    ]


def _signal_type(key: str) -> str:
    return {
        "agentops_config": "Config",
        "azd_ai_agent_eval": "Eval runner",
        "official_ai_agent_evaluation": "Eval runner",
        "agentops_cloud_evaluation": "Eval runner",
        "azd_project": "Deploy mode",
        "prompt_file": "Prompt source",
        "prompt_agent_bootstrap_missing": "Prompt-agent bootstrap",
        "bicep_infra": "Infrastructure",
        "ailz_manifest": "Landing zone",
        "ailz_preflight": "Preflight",
        "network_isolation": "Runner topology",
        "network_isolation_hint": "Runner topology",
        "container_app": "Application host",
        "accelerator_hint": "Accelerator",
        "existing_ci": "Existing CI",
    }.get(key, "Signal")


def _agentops_signal(root: Path) -> Dict[str, Any]:
    path = root / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
        target = classify_agent(str(data.get("agent", "") or ""), data.get("protocol"))
    except Exception as exc:
        return {
            "signal": WorkflowSignal(
                "agentops_config",
                "AgentOps config",
                f"agentops.yaml exists but could not be classified: {exc}",
                "agentops.yaml",
                confidence="medium",
            )
        }
    prompt_file = data.get("prompt_file") if isinstance(data, dict) else None
    bootstrap = data.get("prompt_agent_bootstrap") if isinstance(data, dict) else None
    return {
        "prompt_agent": target.kind == "foundry_prompt",
        "prompt_file": prompt_file,
        "prompt_agent_bootstrap": bool(bootstrap),
        "signal": WorkflowSignal(
            "agentops_config",
            "AgentOps config",
            f"agentops.yaml targets {target.kind}.",
            "agentops.yaml",
        ),
    }


def _find_files(root: Path, pattern: str) -> List[Path]:
    found: List[Path] = []
    for path in root.rglob(pattern):
        if _ignored(path, root):
            continue
        found.append(path)
        if len(found) >= _SCAN_LIMIT:
            break
    return found


def _infra_scan_files(root: Path, bicep_files: Iterable[Path]) -> List[Path]:
    candidates = [
        root / "azure.yaml",
        root / "main.parameters.json",
        root / "infra" / "main.parameters.json",
        root / "manifest.json",
    ]
    candidates.extend(list(bicep_files)[:_SCAN_LIMIT])
    return [path for path in candidates if path.exists() and not _ignored(path, root)]


def _ignored(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _IGNORE_PARTS for part in rel.parts)


def _read_text(path: Path) -> str:
    try:
        if not path.exists() or path.stat().st_size > _TEXT_LIMIT:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_json(path: Path) -> Any:
    text = _read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _has_network_isolation(text: str) -> bool:
    lowered = text.lower()
    structural_terms = (
        "network_isolation",
        "networkisolation",
        "privateendpoint",
        "private endpoint",
        "microsoft.network/privatednszones",
        "azurefirewall",
        "azure firewall",
        "bastion",
        "jumpbox",
        "acr_task_agent_pool",
        "acr task",
        "egressnexthopip",
    )
    return any(term in lowered for term in structural_terms)


def _looks_like_container_app(root: Path, infra_text: str, readme_lower: str) -> bool:
    docker = bool(_find_files(root, "Dockerfile"))
    infra_lower = infra_text.lower()
    return (
        "microsoft.app/containerapps" in infra_lower
        or "container app" in readme_lower
        or "containerapp" in infra_lower
        or docker
    )


def _accelerator_hint(readme_lower: str) -> Optional[WorkflowSignal]:
    if "gpt-rag" in readme_lower or "retrieval-augmented generation" in readme_lower:
        return WorkflowSignal(
            "accelerator_hint",
            "Azure AI accelerator hint",
            "README looks like a RAG accelerator; expect app-specific data/index seeding and eval datasets.",
            "README.md",
            confidence="medium",
        )
    if "live voice" in readme_lower or "voice live" in readme_lower:
        return WorkflowSignal(
            "accelerator_hint",
            "Azure AI accelerator hint",
            "README looks like a voice accelerator; expect real-time app deploy plus scenario/rubric evaluation assets.",
            "README.md",
            confidence="medium",
        )
    if "ai landing zone" in readme_lower or "landing zone" in readme_lower:
        return WorkflowSignal(
            "accelerator_hint",
            "AI Landing Zone hint",
            "README mentions AI Landing Zone; verify topology and runner access before CI/CD rollout.",
            "README.md",
            confidence="medium",
        )
    return None


def _existing_ci_signal(root: Path) -> Optional[WorkflowSignal]:
    github = root / ".github" / "workflows"
    ado = root / ".azuredevops" / "pipelines"
    if github.is_dir():
        return WorkflowSignal(
            "existing_ci",
            "Existing GitHub Actions workflows",
            "Existing workflows found; prefer updating generated AgentOps files rather than creating parallel pipeline names.",
            ".github/workflows",
        )
    if ado.is_dir():
        return WorkflowSignal(
            "existing_ci",
            "Existing Azure DevOps pipelines",
            "Existing pipelines found; prefer updating generated AgentOps files rather than creating parallel pipeline names.",
            ".azuredevops/pipelines",
        )
    return None


def _recommended_deploy_mode(has_azd: bool, prompt_agent: bool) -> str:
    if has_azd:
        return "azd"
    if prompt_agent:
        return "prompt-agent"
    return "placeholder"


def _classification(
    has_azd: bool,
    prompt_agent: bool,
    network_isolated: bool,
    ailz_manifest: bool,
    accelerator_hint: Optional[WorkflowSignal],
) -> str:
    if network_isolated or ailz_manifest:
        return "Azure AI accelerator / landing-zone application"
    if has_azd and accelerator_hint:
        return "azd-managed Azure AI accelerator"
    if has_azd:
        return "azd-managed Azure AI application"
    if prompt_agent:
        return "Foundry prompt-agent project"
    return "custom AI application"


def _complexity(
    network_isolated: bool,
    has_azd: bool,
    bicep_files: List[Path],
    accelerator_hint: Optional[WorkflowSignal],
    ailz_manifest: bool,
) -> str:
    if network_isolated:
        return "high - network-isolated deployment topology"
    if ailz_manifest:
        return "medium - AI Landing Zone deployment path"
    if len(bicep_files) > 5 or (has_azd and accelerator_hint):
        return "medium - accelerator or multi-resource Azure app"
    if has_azd:
        return "medium - azd-managed app"
    return "low - simple AgentOps workflow scaffold"


def _deployment_strategy(mode: str, network_isolated: bool, ailz_preflight: bool) -> str:
    if mode == "azd":
        suffix = (
            " Use private runner/jumpbox/ACR Tasks for private data-plane steps."
            if network_isolated
            else ""
        )
        preflight = (
            " Run the AI Landing Zone preflight before provision."
            if ailz_preflight
            else ""
        )
        return "AgentOps gates; azd owns provision/deploy and hooks." + preflight + suffix
    if mode == "prompt-agent":
        return "AgentOps stages a Foundry prompt candidate, evaluates it, then records the deployed version."
    return "AgentOps writes gates/placeholders; Copilot must adapt project-specific build/deploy steps."


def _eval_strategy(eval_runner: str) -> str:
    if eval_runner == AZD_EVAL_RUNNER:
        return (
            "Use azd AI agent eval so Foundry owns eval.yaml, datasets, evaluators, "
            "and rubric assets while AgentOps applies thresholds and evidence."
        )
    if eval_runner == AGENTOPS_CLOUD_RUNNER:
        return (
            "Use AgentOps cloud eval so Foundry executes the prompt-agent eval "
            "and AgentOps applies thresholds to normalized results."
        )
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        return (
            "Use Microsoft Foundry AI Agent Evaluation for prompt-agent execution, "
            "then keep AgentOps Doctor/evidence as advisory provenance."
        )
    return "Use AgentOps local eval as the CI gate and normalized results artifact."


def _eval_stage(eval_runner: str) -> WorkflowStage:
    if eval_runner == AZD_EVAL_RUNNER:
        return WorkflowStage(
            "PR evaluation gate",
            "azd + Foundry + AgentOps",
            "Run azd AI agent eval from eval.yaml and let AgentOps enforce thresholds before merge.",
            ["agentops eval run --config agentops.yaml --output .agentops/results/latest"],
        )
    if eval_runner == AGENTOPS_CLOUD_RUNNER:
        return WorkflowStage(
            "PR evaluation gate",
            "Foundry + AgentOps",
            "Run Foundry cloud evaluation and let AgentOps enforce thresholds before merge.",
            ["agentops eval run --config <cloud-eval-config> --output .agentops/results/latest"],
        )
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        return WorkflowStage(
            "PR evaluation gate",
            "Microsoft Foundry + AgentOps",
            "Run Microsoft Foundry AI Agent Evaluation as advisory provenance.",
            [
                "python -m agentops.pipeline.official_eval prepare",
                official_eval_action_ref(),
            ],
        )
    return WorkflowStage(
        "PR evaluation gate",
        "AgentOps",
        "Run repeatable evals before merge and publish report artifacts.",
        ["agentops eval run"],
    )


def _stages(
    mode: str,
    eval_runner: str,
    network_isolated: bool,
    prompt_agent: bool,
    ailz_preflight: bool,
) -> List[WorkflowStage]:
    stages = [
        _eval_stage(eval_runner),
        WorkflowStage(
            "Operational readiness",
            "AgentOps Doctor",
            "Run repo, CI/CD, telemetry, and Foundry readiness checks.",
            ["agentops doctor"],
        ),
    ]
    if mode == "azd" and ailz_preflight:
        stages.append(
            WorkflowStage(
                "AI Landing Zone preflight",
                "AI Landing Zone + azd",
                "Validate topology, parameters, CIDRs, BYO resources, and observability wiring before ARM deployment.",
                ["pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict"],
            )
        )
    if mode == "azd":
        notes = ["Use azd hooks for pre/post provision or deploy customization."]
        if prompt_agent:
            notes.append("Keep prompt-agent evaluation in AgentOps even though azd owns app deployment.")
        if network_isolated:
            notes.append("Run private data-plane work from a runner with VNet/private endpoint access.")
        stages.append(
            WorkflowStage(
                "DEV/QA/PROD deploy",
                "azd",
                "Provision and deploy accelerator infrastructure/application.",
                ["azd provision", "azd deploy"],
                notes,
            )
        )
    elif mode == "prompt-agent":
        stages.append(
            WorkflowStage(
                "Foundry prompt candidate deploy",
                "Foundry + AgentOps",
                "Create/reuse candidate prompt-agent version, evaluate it, then record deployment.",
                [
                    "python -m agentops.pipeline.prompt_deploy stage",
                    (
                        "agentops eval run --config <azd-eval-config>"
                        if eval_runner == AZD_EVAL_RUNNER
                        else "python -m agentops.pipeline.official_eval prepare"
                        if eval_runner == OFFICIAL_EVAL_RUNNER
                        else "agentops eval run --config <cloud-eval-config>"
                        if eval_runner == AGENTOPS_CLOUD_RUNNER
                        else "agentops eval run"
                    ),
                ],
            )
        )
    else:
        stages.append(
            WorkflowStage(
                "Project-specific deploy",
                "Copilot + project tooling",
                "Replace placeholders with the repo's build/deploy primitives.",
                notes=["Prefer azd if the project can be converted to azure.yaml."],
            )
        )
    return stages


def _next_steps(
    mode: str,
    eval_runner: str,
    requires_copilot: bool,
    network_isolated: bool,
    skills_installed: bool,
    ailz_preflight: bool,
) -> List[str]:
    eval_step = "Run `agentops eval run` locally and commit agentops.yaml plus datasets."
    if eval_runner == AZD_EVAL_RUNNER:
        eval_step = (
            "Run `agentops eval run` locally and commit agentops.yaml, eval.yaml, "
            "generated evaluator/rubric assets, and datasets."
        )
    steps = [
        eval_step,
        f"Generate workflows with `agentops workflow generate --deploy-mode {mode}`.",
    ]
    if eval_runner == AGENTOPS_CLOUD_RUNNER:
        cloud_eval_step = (
            "Set AZURE_OPENAI_DEPLOYMENT so Foundry cloud eval can judge responses, "
            "then review AgentOps results.json/report.md after the run."
        )
        steps.insert(
            1,
            cloud_eval_step,
        )
    elif eval_runner == AZD_EVAL_RUNNER:
        steps.insert(
            1,
            "CI installs a pinned azure.ai.agents extension before running the azd backend.",
        )
    elif eval_runner == OFFICIAL_EVAL_RUNNER:
        steps.insert(
            1,
            "Set AZURE_OPENAI_DEPLOYMENT so Microsoft Foundry AI Agent Evaluation can judge responses.",
        )
    if ailz_preflight:
        steps.insert(0, "Run `pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict` before provisioning the AI Landing Zone.")
    if requires_copilot:
        if not skills_installed:
            steps.append("Install the AgentOps Copilot skills first: `agentops skills install --platform copilot`.")
        steps.append(
            "Copy/paste the Copilot handoff prompt shown below to inspect project-specific build/deploy hooks and adapt the workflow."
        )
    if network_isolated:
        steps.append(
            "Decide where private-network deploy steps run: self-hosted runner, jumpbox, or ACR Tasks agent pool."
        )
    steps.append("Configure environment approvals and Azure federated identity/service connection before making gates required.")
    return steps


def _azd_eval_recipe(root: Path) -> Optional[Path]:
    try:
        return find_eval_yaml(root)
    except Exception:
        return None


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _skills_installed(root: Path) -> bool:
    return (
        (root / ".github" / "skills" / "agentops-workflow" / "SKILL.md").exists()
        or (root / ".claude" / "commands" / "agentops-workflow.md").exists()
    )


def _copilot_prompt(classification: str, mode: str, network_isolated: bool) -> str:
    network_note = (
        " This repo appears network-isolated; plan self-hosted runner, jumpbox handoff, or ACR Tasks for private steps."
        if network_isolated
        else ""
    )
    return (
        "/agentops-workflow Use the AgentOps workflow analysis above to adapt this "
        f"{classification} pipeline. Keep AgentOps as eval/Doctor/Cockpit gate, use deploy mode {mode}, "
        "and preserve existing azd/Bicep/project deploy hooks instead of inventing a parallel deployment path."
        + network_note
    )


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
