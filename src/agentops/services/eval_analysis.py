"""Read-only evaluation setup analysis for `agentops eval analyze`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from agentops.core.agentops_config import classify_agent
from agentops.utils.yaml import load_yaml

_TEXT_LIMIT = 200_000
_SCAN_LIMIT = 80
_DATASET_ROW_LIMIT = 20
_TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".bicep", ".yaml", ".yml"}
_WALK_FILE_LIMIT = 2_000
_IGNORE_PARTS = {
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
_IGNORE_PREFIXES = {".agentops/results"}


@dataclass(frozen=True)
class EvalSignal:
    """A local file-system signal used to classify evaluation setup shape."""

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
class EvalAnalysis:
    """Stable result contract for `agentops eval analyze`."""

    version: int
    directory: str
    classification: str
    config_status: str
    dataset_status: str
    target_kind: Optional[str]
    scenario_hint: str
    complexity: str
    requires_copilot_adaptation: bool
    copilot_skills_installed: bool
    copilot_prompt: Optional[str] = None
    signals: List[EvalSignal] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommended_skills: List[str] = field(default_factory=list)
    recommended_commands: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "directory": self.directory,
            "classification": self.classification,
            "config_status": self.config_status,
            "dataset_status": self.dataset_status,
            "target_kind": self.target_kind,
            "scenario_hint": self.scenario_hint,
            "complexity": self.complexity,
            "requires_copilot_adaptation": self.requires_copilot_adaptation,
            "copilot_skills_installed": self.copilot_skills_installed,
            "copilot_prompt": self.copilot_prompt,
            "signals": [signal.to_dict() for signal in self.signals],
            "warnings": list(self.warnings),
            "recommended_skills": list(self.recommended_skills),
            "recommended_commands": list(self.recommended_commands),
            "next_steps": list(self.next_steps),
        }


def analyze_eval_project(directory: Path) -> EvalAnalysis:
    """Analyze local project shape before running an evaluation.

    This is intentionally local-only: it does not call Azure, Foundry, Copilot,
    or any model. It tells users whether `agentops eval run` is ready or
    whether evaluation setup should be adapted with AgentOps skills first.
    """

    root = directory.resolve()
    signals: List[EvalSignal] = []
    warnings: List[str] = []

    config_info = _agentops_config_info(root)
    signals.extend(config_info.signals)
    warnings.extend(config_info.warnings)

    repo_text = _repo_text(root)
    readme_text = _read_text(root / "README.md")
    text_for_hints = "\n".join((readme_text, repo_text)).lower()

    structural_signals = _structural_signals(root, text_for_hints)
    signals.extend(structural_signals)

    scenario_hint = _scenario_hint(config_info.dataset_columns, text_for_hints)
    if scenario_hint != "unknown":
        signals.append(
            EvalSignal(
                "scenario_hint",
                "Evaluation scenario hint",
                f"Likely scenario: {scenario_hint}.",
                confidence="medium" if not config_info.dataset_columns else "high",
            )
        )

    if not config_info.has_config:
        warnings.append(
            "No agentops.yaml found. Use `agentops init` for the base file, "
            "then use the agentops-config skill if target or scenario inference is not obvious."
        )
    if config_info.has_config and not config_info.dataset_exists:
        warnings.append(
            "The configured dataset was not found. Use the agentops-dataset skill "
            "to create or map realistic JSONL rows before `agentops eval run`."
        )
    if scenario_hint in {"rag", "agent_workflow"} and not _dataset_supports_scenario(
        scenario_hint, config_info.dataset_columns
    ):
        warnings.append(
            f"The repo looks like {scenario_hint}, but the dataset columns do not "
            "fully support that scenario yet."
        )

    complex_reasons = _complexity_reasons(
        config_info=config_info,
        signals=signals,
        scenario_hint=scenario_hint,
    )
    complexity = _complexity_label(complex_reasons, config_info)
    requires_copilot = bool(complex_reasons) or not config_info.ready
    recommended_skills = _recommended_skills(config_info, scenario_hint, complex_reasons)
    skills_installed = _skills_installed(root)
    copilot_prompt = _copilot_prompt(recommended_skills, scenario_hint)
    recommended_commands = _recommended_commands(root, config_info, recommended_skills, skills_installed)
    next_steps = _next_steps(config_info, recommended_skills, complex_reasons, skills_installed)

    return EvalAnalysis(
        version=1,
        directory=str(root),
        classification=_classification(config_info, scenario_hint),
        config_status=config_info.status,
        dataset_status=config_info.dataset_status,
        target_kind=config_info.target_kind,
        scenario_hint=scenario_hint,
        complexity=complexity,
        requires_copilot_adaptation=requires_copilot,
        copilot_skills_installed=skills_installed,
        copilot_prompt=copilot_prompt,
        signals=signals,
        warnings=warnings,
        recommended_skills=recommended_skills,
        recommended_commands=recommended_commands,
        next_steps=next_steps,
    )


def render_eval_analysis(analysis: EvalAnalysis, output_format: str = "text") -> str:
    """Render analysis as text, Markdown, or JSON."""
    if output_format == "json":
        return json.dumps(analysis.to_dict(), indent=2) + "\n"
    if output_format == "markdown":
        return _render_markdown(analysis)
    if output_format == "text":
        return _render_text(analysis)
    raise ValueError("output_format must be text, markdown, or json")


@dataclass(frozen=True)
class _ConfigInfo:
    has_config: bool
    ready: bool
    status: str
    dataset_status: str
    target_kind: Optional[str]
    dataset_exists: bool
    dataset_columns: Set[str]
    signals: List[EvalSignal] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _agentops_config_info(root: Path) -> _ConfigInfo:
    path = root / "agentops.yaml"
    if not path.exists():
        return _ConfigInfo(
            has_config=False,
            ready=False,
            status="missing",
            dataset_status="missing",
            target_kind=None,
            dataset_exists=False,
            dataset_columns=set(),
        )
    try:
        data = load_yaml(path)
        if not isinstance(data, dict):
            raise ValueError("expected a mapping")
        agent = str(data.get("agent", "") or "")
        dataset_value = data.get("dataset")
        target = classify_agent(agent, data.get("protocol"))
        dataset_path = _resolve_dataset_path(path.parent, dataset_value)
        dataset_exists = dataset_path.exists() if dataset_path is not None else False
        dataset_columns = _dataset_columns(dataset_path) if dataset_path is not None else set()
        dataset_status = _dataset_status(dataset_value, dataset_exists, dataset_columns)
        signals = [
            EvalSignal(
                "agentops_config",
                "AgentOps config",
                f"agentops.yaml targets {target.kind}.",
                "agentops.yaml",
            )
        ]
        if dataset_value is not None:
            signals.append(
                EvalSignal(
                    "dataset_ref",
                    "Evaluation dataset reference",
                    f"Dataset path is {dataset_value}.",
                    _rel(root, dataset_path) if dataset_path is not None else None,
                    confidence="high" if dataset_exists else "medium",
                )
            )
        if dataset_columns:
            signals.append(
                EvalSignal(
                    "dataset_columns",
                    "Dataset row columns",
                    "Found columns: " + ", ".join(sorted(dataset_columns)) + ".",
                    _rel(root, dataset_path) if dataset_path is not None else None,
                )
            )
        ready = bool(agent and dataset_value and dataset_exists and "input" in dataset_columns)
        status = "ready" if ready else "incomplete"
        return _ConfigInfo(
            has_config=True,
            ready=ready,
            status=status,
            dataset_status=dataset_status,
            target_kind=target.kind,
            dataset_exists=dataset_exists,
            dataset_columns=dataset_columns,
            signals=signals,
        )
    except Exception as exc:
        return _ConfigInfo(
            has_config=True,
            ready=False,
            status="invalid",
            dataset_status="unknown",
            target_kind=None,
            dataset_exists=False,
            dataset_columns=set(),
            signals=[
                EvalSignal(
                    "agentops_config",
                    "AgentOps config",
                    f"agentops.yaml exists but could not be analyzed: {exc}",
                    "agentops.yaml",
                    confidence="medium",
                )
            ],
            warnings=[f"agentops.yaml could not be analyzed: {exc}"],
        )


def _resolve_dataset_path(config_dir: Path, dataset_value: Any) -> Optional[Path]:
    if dataset_value is None:
        return None
    path = Path(str(dataset_value))
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()


def _dataset_status(dataset_value: Any, exists: bool, columns: Set[str]) -> str:
    if dataset_value is None:
        return "missing"
    if not exists:
        return "not_found"
    if "input" not in columns:
        return "missing_input_column"
    if not columns:
        return "empty_or_unreadable"
    return "ready"


def _dataset_columns(path: Path) -> Set[str]:
    columns: Set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= _DATASET_ROW_LIMIT:
                    break
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    columns.update(str(key) for key in row)
    except (OSError, json.JSONDecodeError):
        return set()
    return columns


def _repo_text(root: Path) -> str:
    parts: List[str] = []
    total_chars = 0
    for path in _walk_project_files(root):
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = _read_text(path)
        if not text:
            continue
        parts.append(text)
        total_chars += len(text) + 1
        if total_chars >= _TEXT_LIMIT or len(parts) >= _SCAN_LIMIT:
            break
    return "\n".join(parts)[:_TEXT_LIMIT]


def _structural_signals(root: Path, text: str) -> List[EvalSignal]:
    signals: List[EvalSignal] = []
    if (root / "azure.yaml").exists():
        signals.append(
            EvalSignal(
                "azd_project",
                "Azure Developer CLI project",
                "azure.yaml found; eval config may need azd outputs/env values.",
                "azure.yaml",
            )
        )
    if _find_files(root, "Dockerfile") or "container app" in text or "containerapps" in text:
        signals.append(
            EvalSignal(
                "container_or_http_app",
                "HTTP/containerized app",
                "Container or HTTP app signals found; eval may need endpoint and response-field mapping.",
                confidence="medium",
            )
        )
    if "azure.search" in text or "ai search" in text or "retrieval" in text or "vector" in text:
        signals.append(
            EvalSignal(
                "rag_signal",
                "RAG/retrieval signal",
                "Search, retrieval, vector, or context terms found.",
                confidence="medium",
            )
        )
    if "tool_calls" in text or "function_call" in text or "@tool" in text or "tools=" in text:
        signals.append(
            EvalSignal(
                "tool_signal",
                "Tool-calling signal",
                "Tool-call/function-call terms found.",
                confidence="medium",
            )
        )
    if "aiprojectclient" in text or "azure-ai-projects" in text or "services.ai.azure.com" in text:
        signals.append(
            EvalSignal(
                "foundry_signal",
                "Foundry project signal",
                "Foundry SDK or Foundry project endpoint terms found.",
                confidence="medium",
            )
        )
    if "openai" in text and ("chat.completions" in text or "responses.create" in text):
        signals.append(
            EvalSignal(
                "model_signal",
                "Model API signal",
                "Direct model API usage found.",
                confidence="medium",
            )
        )
    return signals


def _scenario_hint(dataset_columns: Set[str], text: str) -> str:
    if {"tool_calls", "tool_definitions"} & dataset_columns:
        return "agent_workflow"
    if "context" in dataset_columns:
        return "rag"
    if "conversation" in dataset_columns or "turns" in dataset_columns:
        return "conversational"
    if "expected" in dataset_columns:
        return "model_quality"
    if "tool_calls" in text or "function_call" in text or "@tool" in text:
        return "agent_workflow"
    if "azure.search" in text or "ai search" in text or "retrieval" in text or "rag" in text:
        return "rag"
    if "chatbot" in text or "conversation" in text or "assistant" in text:
        return "conversational"
    return "unknown"


def _dataset_supports_scenario(scenario: str, columns: Set[str]) -> bool:
    if not columns:
        return False
    if scenario == "rag":
        return {"input", "expected", "context"}.issubset(columns)
    if scenario == "agent_workflow":
        return {"input", "expected"}.issubset(columns) and bool(
            {"tool_calls", "tool_definitions"} & columns
        )
    return "input" in columns


def _complexity_reasons(
    *,
    config_info: _ConfigInfo,
    signals: List[EvalSignal],
    scenario_hint: str,
) -> List[str]:
    reasons: List[str] = []
    signal_keys = {signal.key for signal in signals}
    if not config_info.has_config:
        reasons.append("missing agentops.yaml")
    elif config_info.status != "ready":
        reasons.append("agentops.yaml or dataset is incomplete")
    if "container_or_http_app" in signal_keys and config_info.target_kind in {None, "http_json"}:
        reasons.append("HTTP response contract may need mapping")
    if scenario_hint in {"rag", "agent_workflow"} and not _dataset_supports_scenario(
        scenario_hint, config_info.dataset_columns
    ):
        reasons.append(f"{scenario_hint} dataset columns are not complete")
    if len({"rag_signal", "tool_signal", "container_or_http_app"} & signal_keys) >= 2:
        reasons.append("multiple project-specific evaluation signals")
    return sorted(set(reasons))


def _complexity_label(reasons: List[str], config_info: _ConfigInfo) -> str:
    if len(reasons) >= 2:
        return "high - skill-assisted evaluation setup recommended"
    if reasons:
        return "medium - review setup before running eval"
    if config_info.ready:
        return "low - ready to run eval"
    return "medium - setup required"


def _recommended_skills(
    config_info: _ConfigInfo,
    scenario_hint: str,
    complex_reasons: List[str],
) -> List[str]:
    skills: List[str] = []
    if not config_info.has_config or config_info.status == "invalid":
        skills.append("agentops-config")
    if config_info.dataset_status != "ready":
        skills.append("agentops-dataset")
    if complex_reasons:
        skills.append("agentops-eval")
    return list(dict.fromkeys(skills))


def _recommended_commands(
    root: Path,
    config_info: _ConfigInfo,
    skills: List[str],
    skills_installed: bool,
) -> List[str]:
    commands = ["agentops eval analyze --format markdown"]
    if skills and not skills_installed:
        commands.append("agentops skills install --platform copilot")
    if not config_info.has_config:
        commands.append("agentops init")
    if config_info.ready:
        commands.append("agentops eval run")
    return commands


def _next_steps(
    config_info: _ConfigInfo,
    skills: List[str],
    complex_reasons: List[str],
    skills_installed: bool,
) -> List[str]:
    if config_info.ready and not complex_reasons:
        return [
            "Run `agentops eval run` to produce results.json and report.md.",
            "Then run `agentops workflow analyze` before generating CI/CD workflows.",
        ]
    steps = [
        "Use this analysis as the triage output before `agentops eval run`.",
    ]
    if skills:
        if not skills_installed:
            steps.append("Install the AgentOps Copilot skills first: `agentops skills install --platform copilot`.")
        steps.append(
            "Copy/paste the Copilot handoff prompt shown below; it uses "
            + ", ".join(f"/{skill}" for skill in skills)
            + " to adapt agentops.yaml, dataset rows, and evaluator expectations."
        )
    if config_info.has_config and config_info.dataset_status != "ready":
        steps.append("Create or fix the dataset JSONL referenced by agentops.yaml.")
    steps.append("Re-run `agentops eval analyze`, then run `agentops eval run` once setup is ready.")
    return steps


def _copilot_prompt(skills: List[str], scenario_hint: str) -> Optional[str]:
    if not skills:
        return None
    if "agentops-config" in skills:
        return (
            "/agentops-config Use the AgentOps eval analysis above to inspect this repo, "
            "configure agentops.yaml for the correct target/protocol, and tell me what remains before I run eval."
        )
    if "agentops-dataset" in skills:
        return (
            "/agentops-dataset Use the AgentOps eval analysis above to create or fix the JSONL dataset "
            f"for the {scenario_hint} scenario, then summarize the exact rows and columns."
        )
    return (
        "/agentops-eval Use the AgentOps eval analysis above to verify the target, dataset, evaluator "
        "scenario, and next command before running agentops eval run."
    )


def _classification(config_info: _ConfigInfo, scenario_hint: str) -> str:
    if not config_info.has_config:
        return "unconfigured AI project"
    if config_info.target_kind:
        return f"{config_info.target_kind} evaluation setup ({scenario_hint})"
    return f"AgentOps evaluation setup ({scenario_hint})"


def _render_text(analysis: EvalAnalysis) -> str:
    lines = [
        "AgentOps eval analysis",
        f"Directory: {analysis.directory}",
        f"Classification: {analysis.classification}",
        f"Config status: {analysis.config_status}",
        f"Dataset status: {analysis.dataset_status}",
        f"Target kind: {analysis.target_kind or 'unknown'}",
        f"Scenario hint: {analysis.scenario_hint}",
        f"Complexity: {analysis.complexity}",
        f"Skill-assisted setup: {'yes' if analysis.requires_copilot_adaptation else 'no'}",
        f"Copilot skills installed: {'yes' if analysis.copilot_skills_installed else 'no'}",
        "",
        "Detected signals:",
    ]
    if analysis.signals:
        lines.extend(
            f"- {s.label}: {s.detail}" + (f" ({s.path})" if s.path else "")
            for s in analysis.signals
        )
    else:
        lines.append("- No strong evaluation setup signals detected.")
    if analysis.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in analysis.warnings)
    if analysis.recommended_skills:
        lines.append("")
        lines.append("Recommended skills:")
        lines.extend(f"- /{skill}" for skill in analysis.recommended_skills)
    if analysis.copilot_prompt:
        lines.append("")
        lines.append("Copilot handoff:")
        lines.append(f"- Copy/paste: {analysis.copilot_prompt}")
    lines.append("")
    lines.append("Recommended commands:")
    lines.extend(f"- {command}" for command in analysis.recommended_commands)
    lines.append("")
    lines.append("Next steps:")
    lines.extend(f"- {step}" for step in analysis.next_steps)
    return "\n".join(lines) + "\n"


def _render_markdown(analysis: EvalAnalysis) -> str:
    lines = [
        "# AgentOps eval analysis",
        "",
        f"- **Directory:** `{analysis.directory}`",
        f"- **Classification:** {analysis.classification}",
        f"- **Config status:** `{analysis.config_status}`",
        f"- **Dataset status:** `{analysis.dataset_status}`",
        f"- **Target kind:** `{analysis.target_kind or 'unknown'}`",
        f"- **Scenario hint:** `{analysis.scenario_hint}`",
        f"- **Complexity:** {analysis.complexity}",
        f"- **Skill-assisted setup:** {'yes' if analysis.requires_copilot_adaptation else 'no'}",
        f"- **Copilot skills installed:** {'yes' if analysis.copilot_skills_installed else 'no'}",
        "",
        "## Detected signals",
        "",
    ]
    if analysis.signals:
        lines.extend(
            f"- **{s.label}** ({s.confidence}): {s.detail}"
            + (f" - `{s.path}`" if s.path else "")
            for s in analysis.signals
        )
    else:
        lines.append("- No strong evaluation setup signals detected.")
    if analysis.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in analysis.warnings)
    if analysis.recommended_skills:
        lines.extend(["", "## Recommended skills", ""])
        lines.extend(f"- `/{skill}`" for skill in analysis.recommended_skills)
    if analysis.copilot_prompt:
        lines.extend(["", "## Copilot handoff", ""])
        lines.extend(["Copy/paste this into Copilot:", "", "```text", analysis.copilot_prompt, "```"])
    lines.extend(["", "## Recommended commands", ""])
    lines.extend(f"```bash\n{command}\n```" for command in analysis.recommended_commands)
    lines.extend(["", "## Next steps", ""])
    lines.extend(f"- {step}" for step in analysis.next_steps)
    return "\n".join(lines).rstrip() + "\n"


def _find_files(root: Path, pattern: str) -> List[Path]:
    found: List[Path] = []
    for path in _walk_project_files(root):
        rel_text = _rel_text(root, path)
        if not (fnmatch(path.name, pattern) or fnmatch(rel_text, pattern)):
            continue
        found.append(path)
        if len(found) >= _SCAN_LIMIT:
            break
    return found


def _walk_project_files(root: Path) -> Iterable[Path]:
    root_text = str(root)
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root_text):
        rel_dir = os.path.relpath(dirpath, root_text)
        rel_prefix = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if not _ignored_rel(f"{rel_prefix}/{dirname}" if rel_prefix else dirname)
        )
        for filename in sorted(filenames):
            rel_file = f"{rel_prefix}/{filename}" if rel_prefix else filename
            if _ignored_rel(rel_file):
                continue
            yield Path(dirpath) / filename
            seen += 1
            if seen >= _WALK_FILE_LIMIT:
                return


def _ignored(path: Path, root: Path) -> bool:
    rel_text = _rel_text(root, path)
    if rel_text == "":
        return True
    return _ignored_rel(rel_text)


def _rel_text(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    return str(rel).replace("\\", "/")


def _ignored_rel(rel_text: str) -> bool:
    rel_text = rel_text.replace("\\", "/").strip("/")
    if not rel_text or rel_text == ".":
        return False
    parts = rel_text.split("/")
    return any(part in _IGNORE_PARTS for part in parts) or any(
        rel_text == prefix or rel_text.startswith(f"{prefix}/") for prefix in _IGNORE_PREFIXES
    )


def _read_text(path: Path) -> str:
    try:
        if not path.exists() or path.stat().st_size > _TEXT_LIMIT:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _skills_installed(root: Path) -> bool:
    return (
        (root / ".github" / "skills" / "agentops-config" / "SKILL.md").exists()
        or (root / ".claude" / "commands" / "agentops-config.md").exists()
    )


def _rel(root: Path, path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

