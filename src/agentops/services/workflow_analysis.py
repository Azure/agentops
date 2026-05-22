"""Read-only CI/CD analysis for Azure AI accelerator repositories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agentops.core.agentops_config import classify_agent
from agentops.utils.yaml import load_yaml

_TEXT_LIMIT = 200_000
_SCAN_LIMIT = 80
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
    deployment_strategy: str
    complexity: str
    requires_copilot_adaptation: bool
    copilot_skills_installed: bool
    copilot_prompt: Optional[str] = None
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
            "deployment_strategy": self.deployment_strategy,
            "complexity": self.complexity,
            "requires_copilot_adaptation": self.requires_copilot_adaptation,
            "copilot_skills_installed": self.copilot_skills_installed,
            "copilot_prompt": self.copilot_prompt,
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

    recommended_deploy_mode = _recommended_deploy_mode(has_azd, prompt_agent)
    classification = _classification(has_azd, prompt_agent, network_isolated, ailz_manifest, accelerator_hint)
    complexity = _complexity(network_isolated, has_azd, bicep_files, accelerator_hint, ailz_manifest)
    deployment_strategy = _deployment_strategy(recommended_deploy_mode, network_isolated, ailz_preflight)
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

    stages = _stages(recommended_deploy_mode, network_isolated, prompt_agent, ailz_preflight)
    next_steps = _next_steps(recommended_deploy_mode, requires_copilot, network_isolated, skills_installed, ailz_preflight)

    return WorkflowAnalysis(
        version=1,
        directory=str(root),
        classification=classification,
        recommended_deploy_mode=recommended_deploy_mode,
        deployment_strategy=deployment_strategy,
        complexity=complexity,
        requires_copilot_adaptation=requires_copilot,
        copilot_skills_installed=skills_installed,
        copilot_prompt=copilot_prompt,
        signals=signals,
        warnings=warnings,
        recommended_commands=recommended_commands,
        stages=stages,
        next_steps=next_steps,
    )


def recommended_deploy_mode(directory: Path) -> str:
    """Return the same deploy-mode decision used by workflow generation."""
    return analyze_workflow_project(directory).recommended_deploy_mode


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
        f"Directory: {analysis.directory}",
        f"Classification: {analysis.classification}",
        f"Recommended deploy mode: {analysis.recommended_deploy_mode}",
        f"Strategy: {analysis.deployment_strategy}",
        f"Complexity: {analysis.complexity}",
        f"Copilot adaptation: {'yes' if analysis.requires_copilot_adaptation else 'no'}",
        f"Copilot skills installed: {'yes' if analysis.copilot_skills_installed else 'no'}",
        "",
        "Detected signals:",
    ]
    lines.extend(
        f"- {s.label}: {s.detail}" + (f" ({s.path})" if s.path else "")
        for s in analysis.signals
    )
    if analysis.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in analysis.warnings)
    if analysis.copilot_prompt:
        lines.append("")
        lines.append("Copilot handoff:")
        lines.append(f"- Copy/paste: {analysis.copilot_prompt}")
    lines.append("")
    lines.append("Recommended commands:")
    lines.extend(f"- {command}" for command in analysis.recommended_commands)
    lines.append("")
    lines.append("Pipeline stages:")
    for stage in analysis.stages:
        lines.append(f"- {stage.name} [{stage.owner}]: {stage.purpose}")
    lines.append("")
    lines.append("Next steps:")
    lines.extend(f"- {step}" for step in analysis.next_steps)
    return "\n".join(lines) + "\n"


def _render_markdown(analysis: WorkflowAnalysis) -> str:
    lines = [
        "# AgentOps workflow analysis",
        "",
        f"- **Directory:** `{analysis.directory}`",
        f"- **Classification:** {analysis.classification}",
        f"- **Recommended deploy mode:** `{analysis.recommended_deploy_mode}`",
        f"- **Strategy:** {analysis.deployment_strategy}",
        f"- **Complexity:** {analysis.complexity}",
        f"- **Copilot adaptation:** {'yes' if analysis.requires_copilot_adaptation else 'no'}",
        f"- **Copilot skills installed:** {'yes' if analysis.copilot_skills_installed else 'no'}",
        "",
        "## Detected signals",
        "",
    ]
    if analysis.signals:
        lines.extend(
            f"- **{s.label}** ({s.confidence}): {s.detail}"
            + (f" — `{s.path}`" if s.path else "")
            for s in analysis.signals
        )
    else:
        lines.append("- No strong accelerator or deployment signals detected.")
    if analysis.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in analysis.warnings)
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
    return {
        "prompt_agent": target.kind == "foundry_prompt",
        "prompt_file": prompt_file,
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


def _stages(mode: str, network_isolated: bool, prompt_agent: bool, ailz_preflight: bool) -> List[WorkflowStage]:
    stages = [
        WorkflowStage(
            "PR evaluation gate",
            "AgentOps",
            "Run repeatable evals before merge and publish report artifacts.",
            ["agentops eval run"],
        ),
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
                ["python -m agentops.pipeline.prompt_deploy stage", "agentops eval run"],
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
    requires_copilot: bool,
    network_isolated: bool,
    skills_installed: bool,
    ailz_preflight: bool,
) -> List[str]:
    steps = [
        "Run `agentops eval run` locally and commit agentops.yaml plus datasets.",
        f"Generate workflows with `agentops workflow generate --deploy-mode {mode}`.",
    ]
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
