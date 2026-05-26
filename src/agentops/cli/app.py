from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from textwrap import wrap
from typing import Annotated, Optional

import typer

from agentops.utils.colors import style
from agentops.utils.logging import get_logger, setup_logging

app = typer.Typer(
    name="agentops",
    help="AgentOps - standardized evaluation workflows for AI projects.",
    add_completion=False,
)
eval_app = typer.Typer(
    help=(
        "Evaluation sub-commands. "
        "Use `agentops eval run --help` to see run options like "
        "`--config` (`-c`) and `--output` (`-o`)."
    )
)
report_app = typer.Typer(help="Reporting commands.")
workflow_app = typer.Typer(help="CI/CD workflow commands.")
skills_app = typer.Typer(help="Coding agent skills management.")
mcp_app = typer.Typer(help="MCP (Model Context Protocol) server commands.")
agent_app = typer.Typer(
    help=(
        "Agent server commands (host AgentOps as a Copilot SDK agent). "
        "Use `agentops doctor` for the local diagnostic analyzer."
    )
)
doctor_app = typer.Typer(
    help=(
        "Diagnose MLOps / security / responsible-AI gaps in this workspace. "
        "Use `agentops doctor explain` for the long-form manual."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
)
init_app = typer.Typer(
    help=(
        "Initialise an AgentOps workspace and configure endpoints. "
        "Use `agentops init show` to inspect the current configuration. "
        "Use `agentops init explain` for the long-form manual."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(eval_app, name="eval")
app.add_typer(report_app, name="report")
app.add_typer(workflow_app, name="workflow")
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(agent_app, name="agent")
app.add_typer(doctor_app, name="doctor")
app.add_typer(init_app, name="init")

log = get_logger(__name__)
DEFAULT_REPORT_INPUT = Path(".agentops/results/latest/results.json")
DOCTOR_EXPLAIN_WRAP_WIDTH = 88


def _cli_heading(text: str) -> str:
    return style(text, "bold", "cyan")


def _cli_label(text: str) -> str:
    return style(text, "bold", "cyan")


def _cli_path(path: Path | str) -> str:
    return style(str(path), "cyan")


def _cli_command(command: str) -> str:
    return style(command, "bold")


def _cli_ok(text: str) -> str:
    return style(text, "green")


def _cli_warn(text: str) -> str:
    return style(text, "yellow")


def _cli_error(text: str) -> str:
    return style(text, "red")


def _cli_created(path: Path | str) -> str:
    return f" {_cli_ok('+')} {_cli_ok('created')} {_cli_path(path)}"


def _cli_updated(path: Path | str) -> str:
    return f" {_cli_ok('✓')} {_cli_ok('updated')} {_cli_path(path)}"


def _cli_overwritten(path: Path | str) -> str:
    return f" {_cli_warn('~')} {_cli_warn('overwritten')} {_cli_path(path)}"


def _cli_skipped(path: Path | str, suffix: str = "") -> str:
    return f" {style('-', 'dim')} {style('skipped', 'dim')} {_cli_path(path)}{suffix}"


def _cli_value(text: str) -> str:
    lowered = text.lower()
    if lowered in {"ready", "no"} or lowered.startswith("low -") or "ready to run" in lowered:
        return _cli_ok(text)
    if lowered in {"invalid", "not_found", "missing", "missing_input_column"}:
        return _cli_error(text)
    if lowered.startswith("high -") or "needs skill" in lowered:
        return _cli_warn(text)
    if lowered.startswith("medium -") or lowered in {"yes", "unknown", "incomplete"}:
        return _cli_warn(text)
    if lowered in {"azd", "prompt-agent", "placeholder", "auto"} or "(auto default)" in lowered:
        return style(text, "bold")
    return text


def _workflow_eval_runner_label(eval_runner: str) -> str:
    if eval_runner == "official-ai-agent-evaluation":
        return "Microsoft Foundry AI Agent Evaluation"
    if eval_runner == "agentops-local":
        return "AgentOps local eval"
    return eval_runner


def _workflow_environment_names(kinds: list[str]) -> list[str]:
    environments: list[str] = []
    if any(kind in kinds for kind in ("pr", "watchdog", "dev")):
        environments.append("dev")
    if "qa" in kinds:
        environments.append("qa")
    if "prod" in kinds:
        environments.append("production")
    return environments


def _colorize_analysis_text(text: str) -> str:
    """Apply restrained terminal color to text analysis output only."""
    lines: list[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped in {"Warnings", "Warnings:"}:
            section = "Warnings"
            lines.append(_cli_warn(line))
            continue
        if stripped in {
            "AgentOps eval analysis",
            "AgentOps trace-to-dataset preview",
            "AgentOps workflow analysis",
            "Workflow decision checklist:",
            "Recommendation",
            "Readiness",
            "Detected signals:",
            "Signals",
            "Foundry eval checks:",
            "Foundry eval",
            "Recommended skills:",
            "Recommended skills",
            "Copilot handoff:",
            "Copilot handoff",
            "Recommended commands:",
            "Commands",
            "Pipeline stages:",
            "Pipeline plan",
            "Next steps:",
            "Next",
            "Sample rows:",
            "Sample rows",
            "Summary",
        }:
            section = stripped.rstrip(":")
            lines.append(_cli_heading(line))
            continue
        if section == "Commands" and stripped.startswith("agentops "):
            prefix = line[: len(line) - len(line.lstrip())]
            lines.append(f"{prefix}{_cli_command(stripped)}")
            continue
        status = stripped.split(maxsplit=1)[0].lower() if stripped else ""
        if status in {"ok", "hint", "todo", "warn"}:
            prefix = line[: len(line) - len(line.lstrip())]
            body = line[len(prefix) :]
            rest = body[len(status) :]
            if status == "ok":
                rendered_status = _cli_ok(body[: len(status)])
            elif status == "warn":
                rendered_status = _cli_warn(body[: len(status)])
            else:
                rendered_status = style(body[: len(status)], "bold", "yellow")
            lines.append(f"{prefix}{rendered_status}{rest}")
            continue
        if stripped.startswith("- "):
            bullet = style("-", "dim")
            body = stripped[2:]
            prefix = line[: len(line) - len(line.lstrip())]
            if section == "Warnings":
                body = _cli_warn(body)
            elif section == "Recommended commands" and body.startswith("agentops "):
                body = _cli_command(body)
            elif section == "Recommended skills" and body.startswith("/"):
                body = style(body, "bold", "cyan")
            lines.append(f"{prefix}{bullet} {body}")
            continue
        if ": " in stripped:
            label, value = stripped.split(": ", 1)
            prefix = line[: len(line) - len(line.lstrip())]
            if label == "Copilot skills installed" and value.lower() == "no":
                rendered_value = _cli_warn(value)
            elif label == "Skill-assisted setup" and value.lower() == "no":
                rendered_value = _cli_ok(value)
            else:
                rendered_value = _cli_value(value)
            lines.append(f"{prefix}{_cli_label(label)}: {rendered_value}")
            continue
        lines.append(line)
    return "\n".join(lines)


# Auto-load .agentops/.env at import time so downstream code (Foundry
# discovery, telemetry, doctor checks) can rely on the values the user
# captured via `agentops init` without having to `export` them in every
# shell session. The loader never overrides variables already present in
# the process environment, matching dotenv/direnv/azd semantics.
try:
    from agentops.utils.dotenv_loader import load_workspace_dotenv

    load_workspace_dotenv(Path.cwd())
except Exception:  # noqa: BLE001
    # Loading is best-effort; never crash the CLI on a malformed .env.
    pass


@dataclass(frozen=True)
class ExplainPage:
    title: str
    command: str
    synopsis: tuple[str, ...]
    summary: tuple[str, ...]
    how_it_works: tuple[str, ...] = ()
    architecture: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    children: tuple[str, ...] = field(default_factory=tuple)


EXPLAIN_PAGES: dict[tuple[str, ...], ExplainPage] = {
    (): ExplainPage(
        title="AgentOps CLI",
        command="agentops",
        synopsis=(
            "agentops [OPTIONS] COMMAND [ARGS]...",
            "agentops explain [COMMAND...] [--format text|markdown|html] [--out PATH] [--open]",
        ),
        summary=(
            "AgentOps Toolkit — a CLI, local Cockpit, and agent skills "
            "that help teams answer two release questions for Microsoft "
            "Foundry agents: can we ship it, and where is the proof?",
            "The CLI runs reproducible release gates for agents and models, "
            "writes stable artifacts, regenerates reports, and installs "
            "AgentOps skills into GitHub Copilot or Claude Code. The Cockpit "
            "brings your project, Foundry, and Azure Monitor together in a "
            "local browser view. Doctor adds readiness analysis grouped by "
            "the Microsoft AI Well-Architected Framework.",
            "Foundry runs the agent. AgentOps proves the release is ready: "
            "CLI configuration, CI gates, normalized artifacts, Doctor "
            "diagnostics, release evidence, and links to the right Foundry "
            "or Azure Monitor surface for runtime drilldown.",
            "Use `--help` for the terse syntax. Use `explain` for the "
            "bigger picture — what each command does, what it reads, what "
            "it writes, and where it takes you.",
        ),
        how_it_works=(
            "`init` creates a reproducible workspace layout: config, starter data, skills, and result folders.",
            "`eval run` executes local or Foundry-backed evaluation workflows and writes stable `results.json` plus `report.md` artifacts.",
            "`doctor` collects local history, workspace configuration, Foundry control-plane metadata, Azure telemetry, and Azure resource posture, then emits actionable readiness findings grouped by WAF-AI pillar.",
            "`cockpit` opens a local browser cockpit: Foundry connection, one-click links to Foundry Monitor, Evaluations, Traces, Red Teaming, and App Insights, an observability readiness checklist, Doctor findings, local eval history, and recommended next actions.",
            "`workflow analyze`, `workflow generate`, `skills install`, `report generate`, and `mcp serve` wire the same workflow into CI, coding agents, reports, and MCP clients.",
        ),
        inputs=(
            "Command path after `explain`, for example `eval run`, `doctor`, or `cockpit`.",
            "`--format text|markdown|html` to choose terminal text, README-style Markdown, or printable HTML.",
            "`--out PATH` to write the selected format to disk.",
            "`--open` to generate a temporary HTML page and open it in the default browser.",
        ),
        outputs=(
            "Terminal manual by default.",
            "Markdown or HTML file when `--out` is provided.",
            "Temporary browser copy when `--open` is used without an explicit HTML output path.",
        ),
        examples=(
            "agentops --help",
            "agentops explain",
            "agentops explain eval run --open",
            "agentops explain cockpit --format markdown --out cockpit.md",
        ),
        children=("init", "eval", "report", "workflow", "skills", "mcp", "agent", "doctor", "cockpit"),
    ),
    ("init",): ExplainPage(
        title="Initialize workspace and configure endpoints",
        command="agentops init",
        synopsis=(
            "agentops init [--force] [--dir PATH]",
            "agentops init [--no-prompt] [--reconfigure] [--no-appinsights] [--azd-env NAME]",
            "agentops init [--project-endpoint URL] [--agent REF] [--dataset PATH] [--appinsights-connection-string STR]",
            "agentops init show [--reveal-secrets]",
            "agentops init explain",
        ),
        summary=(
            "Bootstraps an AgentOps workspace and walks the user through the "
            "values needed to evaluate, observe, and analyze a Foundry agent.",
            "It is the single entrypoint for setting up a project: it "
            "scaffolds `agentops.yaml` plus the `.agentops/` starter files, "
            "ensures a `.azure/` directory shaped the way `azd` expects, and "
            "runs an azd-style question loop that fills in project endpoint, "
            "agent, and dataset.",
            "Every answer is persisted as soon as it is validated, so a "
            "Ctrl+C mid-wizard never loses values that were already entered. "
            "Re-running `agentops init` is idempotent: questions whose values "
            "are already configured are skipped with a one-line confirmation. "
            "Pass `--reconfigure` to re-ask every question.",
        ),
        how_it_works=(
            "Scaffolds the minimal workspace files via packaged templates: "
            "`agentops.yaml`, `.agentops/data/smoke.jsonl`, and a starter "
            "project `.gitignore` (when one does not already exist). Existing "
            "files are preserved unless `--force` is provided.",
            "Bootstraps `.azure/<env>/` directly on the filesystem when no "
            "azd environment exists yet — creating `.azure/<env>/.env`, "
            "`.azure/.gitignore`, and `.azure/config.json`. The `azd` CLI is "
            "not required; AgentOps writes the same shape `azd` expects, so "
            "the two tools coexist cleanly.",
            "Reads current effective values from `agentops.yaml`, the active "
            "`.azure/<env>/.env`, and the process environment. Each question "
            "shows the current value as its default; pressing Enter keeps it.",
            "Persists `agent` and `dataset` to `agentops.yaml` (declarative, "
            "version-controlled). Persists the Foundry project endpoint to "
            "`.azure/<env>/.env` (git-ignored). App Insights is not asked in "
            "the wizard; runtime commands try to discover the Foundry project's "
            "attached resource through the Azure AI Projects SDK, and "
            "`--appinsights-connection-string` remains available when you need "
            "to force a value explicitly. Canonical Azure variable names "
            "(`AZURE_*`, `APPLICATIONINSIGHTS_*`) are preserved so Azure SDKs "
            "and azd templates read them directly. Only AgentOps-specific "
            "knobs use the `AGENTOPS_` prefix.",
            "Supports a fully scripted mode through `--project-endpoint`, "
            "`--agent`, `--dataset`, `--appinsights-connection-string`, and "
            "`--azd-env` flags. The wizard is skipped automatically when any "
            "of those flags is provided, or when `--no-prompt` is passed.",
            "`agentops init show` prints the active configuration: azd "
            "environment, agentops.yaml fields, and each managed variable "
            "with its source and whether it is set.",
        ),
        inputs=(
            "Workspace directory (defaults to the current directory).",
            "User answers entered interactively, or values supplied via flags.",
        ),
        outputs=(
            "`agentops.yaml` — version, agent, dataset.",
            "`.agentops/` — starter data and asset folders.",
            "`.azure/<env>/.env` — AZURE_AI_FOUNDRY_PROJECT_ENDPOINT, APPLICATIONINSIGHTS_CONNECTION_STRING.",
            "`.azure/.gitignore` — ensures per-environment `.env` files stay out of git.",
            "`.azure/config.json` — `defaultEnvironment` is set when missing.",
        ),
        examples=(
            "agentops init",
            "agentops init --no-prompt",
            "agentops init --reconfigure",
            "agentops init show",
            "agentops init --no-appinsights",
            "agentops init --azd-env dev --project-endpoint https://acct.services.ai.azure.com/api/projects/p --agent my-bot:2 --dataset .agentops/data/smoke.jsonl",
        ),
        see_also=(
            "agentops explain eval run",
            "agentops explain doctor",
            "agentops explain cockpit",
            "agentops explain skills install",
        ),
    ),
    ("eval",): ExplainPage(
        title="Evaluation commands",
        command="agentops eval",
        synopsis=("agentops eval COMMAND [ARGS]...", "agentops eval explain"),
        summary=(
            "Contains commands that analyze and execute standardized evaluation runs from AgentOps configuration.",
            "`analyze` is the read-only setup triage; `run` is the deterministic executor that loads config, invokes the target, evaluates rows, and writes normalized outputs. `promote-traces` turns reviewed production trace exports into regression dataset candidates.",
        ),
        children=("analyze", "run", "promote-traces"),
        examples=("agentops eval analyze", "agentops eval run --config agentops.yaml", "agentops eval promote-traces --source traces.jsonl --apply", "agentops explain eval run --open"),
    ),
    ("eval", "analyze"): ExplainPage(
        title="Analyze evaluation setup",
        command="agentops eval analyze",
        synopsis=("agentops eval analyze [--dir PATH] [--format text|markdown|json] [--out PATH]", "agentops eval analyze explain"),
        summary=(
            "Inspects the local repository and explains whether evaluation setup is ready for `agentops eval run`.",
            "Use it after `agentops init` and before the first run, especially for copied accelerators or apps where target, dataset, or evaluator scenario is not obvious.",
        ),
        how_it_works=(
            "Scans local files only; it does not call Azure, Foundry, Copilot, or any model.",
            "Reads `agentops.yaml` when present, classifies the target kind, checks the dataset reference, and samples JSONL columns.",
            "Looks for structural hints such as Foundry SDK usage, HTTP/containerized apps, RAG/retrieval code, tool calls, direct model APIs, and azd projects.",
            "Reports a scenario hint and complexity level. If deterministic inference is not enough, it recommends the AgentOps skills to use with Copilot, such as `agentops-config`, `agentops-dataset`, and `agentops-eval`.",
            "The boundary is intentional: `eval analyze` is read-only triage, `agentops init` writes the base config, `agentops eval run` executes a configured eval, and Doctor checks readiness after runs/config exist.",
        ),
        outputs=("Human-readable eval setup analysis or stable JSON with `version: 1`",),
        examples=(
            "agentops eval analyze",
            "agentops eval analyze --format markdown --out agentops-eval-plan.md",
            "agentops eval analyze --format json",
        ),
        see_also=("agentops explain eval run", "agentops explain workflow analyze", "agentops explain skills install"),
    ),
    ("eval", "run"): ExplainPage(
        title="Run evaluation",
        command="agentops eval run",
        synopsis=("agentops eval run [--config PATH] [--output DIR] [--baseline PATH] [--format md|html|all]", "agentops eval run explain"),
        summary=(
            "Runs the evaluation workflow described by `agentops.yaml` and writes stable outputs for humans and CI.",
            "This command is the core quality gate: it produces machine-readable results, a Markdown report, and an exit code that pipelines can enforce.",
        ),
        how_it_works=(
            "Loads and validates the flat AgentOps config.",
            "Resolves the target backend and dataset from the config.",
            "Runs evaluation rows, collects metric scores, and applies thresholds.",
            "Writes outputs to a timestamped `.agentops/results/<timestamp>/` folder unless `--output` is set.",
            "Mirrors the run to `.agentops/results/latest/` for reports, cockpit, and Doctor history.",
            "Returns exit code `0` when all thresholds pass, `2` when a threshold fails, or `1` for runtime or configuration errors — so CI pipelines can gate on the result.",
        ),
        inputs=("`agentops.yaml`", "Dataset rows referenced by the config", "Optional baseline `results.json`"),
        outputs=("`results.json`", "`report.md`", "Optional latest mirror under `.agentops/results/latest/`"),
        examples=("agentops eval run", "agentops eval run -c agentops.yaml -o .agentops/results/manual"),
        see_also=("agentops explain report generate", "agentops explain cockpit"),
    ),
    ("eval", "promote-traces"): ExplainPage(
        title="Promote traces into dataset candidates",
        command="agentops eval promote-traces",
        synopsis=("agentops eval promote-traces --source traces.jsonl [--out .agentops/data/trace-regression.jsonl] [--max-rows N] [--label-mode self-similarity|pending] [--apply]", "agentops eval promote-traces explain"),
        summary=(
            "Converts an exported Foundry/App Insights trace JSON or JSONL file into reviewable AgentOps regression dataset rows.",
            "By default it only previews the candidate rows. Use `--apply` to write the dataset and provenance manifest under `.agentops/data/`.",
        ),
        how_it_works=(
            "Reads local trace exports only; it does not query Azure or Foundry.",
            "Extracts common input/response fields from each trace and writes AgentOps JSONL rows.",
            "`--label-mode self-similarity` stores the production response as `expected` for drift detection; this is not human-verified truth.",
            "`--label-mode pending` leaves expected values blank and marks rows for human labeling.",
            "Writes `trace-regression-manifest.json` beside the dataset when `--apply` is used so Doctor and evidence packs can show trace-to-dataset readiness.",
        ),
        outputs=("Preview text by default", "JSONL dataset plus `trace-regression-manifest.json` when `--apply` is used"),
        examples=(
            "agentops eval promote-traces --source traces.jsonl",
            "agentops eval promote-traces --source traces.jsonl --label-mode pending --apply",
        ),
        see_also=("agentops explain eval run", "agentops explain doctor"),
    ),
    ("report",): ExplainPage(
        title="Reporting commands",
        command="agentops report",
        synopsis=("agentops report COMMAND [ARGS]...", "agentops report explain"),
        summary=("Contains commands that turn existing evaluation outputs back into human-readable reports.",),
        children=("generate",),
        examples=("agentops report generate --in .agentops/results/latest/results.json",),
    ),
    ("report", "generate"): ExplainPage(
        title="Generate report",
        command="agentops report generate",
        synopsis=("agentops report generate [--in results.json] [--out report.md] [--format md]", "agentops report generate explain"),
        summary=(
            "Regenerates `report.md` from an existing AgentOps `results.json` without re-running the target or evaluators.",
            "Use it when you changed report rendering or need a report copy in a different location.",
        ),
        how_it_works=(
            "Loads `.agentops/results/latest/results.json` by default, or the path passed with `--in`.",
            "Validates that the file is an AgentOps 1.0 results payload.",
            "Renders the report through the flat pipeline reporter and writes it next to results unless `--out` is set.",
        ),
        inputs=("`results.json`",),
        outputs=("`report.md`",),
        examples=("agentops report generate", "agentops report generate --in .agentops/results/latest/results.json --out report.md"),
    ),
    ("workflow",): ExplainPage(
        title="Workflow commands",
        command="agentops workflow",
        synopsis=("agentops workflow COMMAND [ARGS]...", "agentops workflow explain"),
        summary=("Contains commands that analyze and generate CI/CD workflow files for AgentOps evaluation gates and deployment stages.",),
        children=("analyze", "generate"),
    ),
    ("workflow", "analyze"): ExplainPage(
        title="Analyze CI/CD workflow shape",
        command="agentops workflow analyze",
        synopsis=("agentops workflow analyze [--dir PATH] [--format text|markdown|json] [--out PATH]", "agentops workflow analyze explain"),
        summary=(
            "Inspects the local repository and recommends how AgentOps should fit into CI/CD without replacing Foundry, azd, or landing-zone deployment.",
            "Use it before generating workflows for copied accelerators, azd projects, AI Landing Zone topologies, or repos with existing build/deploy pipelines.",
        ),
        how_it_works=(
            "Scans local files only; it does not call Azure, Foundry, GitHub, Azure DevOps, or azd.",
            "Treats structural signals such as `azure.yaml`, Bicep files, AgentOps prompt-agent config, landing-zone manifests, private-network terms, Dockerfiles, and existing CI folders as the main evidence.",
            "Treats README accelerator matches as hints, not hard truth.",
            "Returns the same deploy-mode recommendation used by `workflow generate --deploy-mode auto` so analysis and generation stay aligned.",
            "Explains the recommended pipeline stages: AgentOps eval/Doctor gates, azd app/infra deployment when present, Foundry prompt-agent candidate deployment when applicable, or project-specific placeholders when adaptation is required.",
        ),
        outputs=("Human-readable workflow analysis or stable JSON with `version: 1`",),
        examples=(
            "agentops workflow analyze",
            "agentops workflow analyze --format markdown --out agentops-workflow-plan.md",
            "agentops workflow analyze --format json",
        ),
        see_also=("agentops explain workflow generate", "agentops explain doctor", "agentops explain cockpit"),
    ),
    ("workflow", "generate"): ExplainPage(
        title="Generate CI/CD workflows",
        command="agentops workflow generate",
        synopsis=("agentops workflow generate [--force] [--dir PATH] [--kinds pr,dev,qa,prod,watchdog] [--platform github|azure-devops] [--deploy-mode auto|placeholder|azd|prompt-agent]", "agentops workflow generate explain"),
        summary=(
            "Writes CI/CD workflow templates that run AgentOps gates in pull requests and environment deployments.",
            "Deployment mode defaults to `auto`. Deployment is azd-first when the repo already has `azure.yaml`: generated deploy workflows call `azd provision` / `azd deploy` instead of asking AgentOps to own infrastructure. Repos without `azure.yaml` can use prompt-agent mode when `agentops.yaml` targets a Foundry prompt agent, or placeholders for custom stacks.",
        ),
        how_it_works=(
            "Selects the target platform and workflow kinds.",
            "When `--deploy-mode` is omitted, auto-detects `azure.yaml` first, then Foundry prompt-agent configs, and picks azd, prompt-agent, or placeholder deploy templates. Override with `--deploy-mode`.",
            "Copies packaged templates into `.github/workflows/` or `.azuredevops/pipelines/`.",
            "Skips existing files unless `--force` is set.",
            "Prints required identity, environment, and branch-protection next steps.",
        ),
        outputs=("CI/CD YAML workflow files",),
        examples=("agentops workflow generate", "agentops workflow generate --kinds pr,dev --platform github --deploy-mode prompt-agent --force"),
    ),
    ("skills",): ExplainPage(
        title="Coding agent skills",
        command="agentops skills",
        synopsis=("agentops skills COMMAND [ARGS]...", "agentops skills explain"),
        summary=("Contains commands that install workflow-oriented AgentOps skills for coding agents such as GitHub Copilot and Claude Code.",),
        children=("install",),
    ),
    ("skills", "install"): ExplainPage(
        title="Install coding-agent skills",
        command="agentops skills install",
        synopsis=("agentops skills install [--platform copilot|claude] [--from SOURCE] [--prompt] [--force] [--dir PATH]", "agentops skills install explain"),
        summary=(
            "Installs AgentOps skill files into the current repository so coding agents can guide users through eval setup, dataset creation, reporting, regressions, tracing, monitoring, and workflows.",
            "By default it auto-detects the coding-agent platform and falls back to GitHub Copilot when nothing is detected.",
        ),
        how_it_works=(
            "Resolves target platforms from `--platform`, workspace detection, or the default.",
            "Copies bundled skills or installs a community skill from GitHub when `--from` is used.",
            "Registers skills in platform-specific instruction files when supported.",
        ),
        outputs=("`.github/skills/agentops-*` for Copilot", "`.claude/commands/agentops-*` for Claude Code"),
        examples=("agentops skills install", "agentops skills install --platform copilot", "agentops skills install --from github:org/repo@v1"),
    ),
    ("mcp",): ExplainPage(
        title="MCP commands",
        command="agentops mcp",
        synopsis=("agentops mcp COMMAND [ARGS]...", "agentops mcp explain"),
        summary=("Contains commands that expose AgentOps workflows over the Model Context Protocol for MCP-aware coding agents.",),
        children=("serve",),
    ),
    ("mcp", "serve"): ExplainPage(
        title="Serve MCP tools",
        command="agentops mcp serve",
        synopsis=("agentops mcp serve", "agentops mcp serve explain"),
        summary=(
            "Starts the AgentOps MCP server on stdio so an MCP client can call AgentOps tools directly.",
            "This is intended for coding-agent integrations, not for an HTTP browser workflow.",
        ),
        how_it_works=(
            "Imports the optional MCP server package.",
            "Registers AgentOps workflow tools such as init, eval run, reporting, results summaries, dataset operations, and workflow generation.",
            "Serves over stdin/stdout until the MCP client exits.",
        ),
        inputs=("MCP client stdio messages",),
        examples=("agentops mcp serve",),
    ),
    ("agent",): ExplainPage(
        title="Agent server commands",
        command="agentops agent",
        synopsis=("agentops agent COMMAND [ARGS]...", "agentops agent explain"),
        summary=("Contains commands that host AgentOps Doctor as an HTTP agent/Copilot Extension surface.",),
        children=("serve",),
    ),
    ("agent", "serve"): ExplainPage(
        title="Serve AgentOps as an HTTP agent",
        command="agentops agent serve",
        synopsis=("agentops agent serve [--host HOST] [--port PORT] [--workspace PATH] [--config PATH] [--no-verify] [--workers N]", "agentops agent serve explain"),
        summary=(
            "Hosts AgentOps Doctor behind an HTTP API compatible with Copilot Extensions.",
            "It exposes message handling and health endpoints so AgentOps diagnostics can be used from a chat-based agent surface.",
        ),
        how_it_works=(
            "Loads `.agentops/agent.yaml` or the explicit `--config` path.",
            "Creates the FastAPI app from the agent server module.",
            "Runs Uvicorn with signature verification enabled by default.",
        ),
        inputs=("`.agentops/agent.yaml`", "Copilot Extensions HTTP requests"),
        outputs=("HTTP endpoints: `POST /agents/messages`, `GET /healthz`, `GET /`",),
        examples=("agentops agent serve", "agentops agent serve --host 127.0.0.1 --port 8080 --no-verify"),
        see_also=("agentops explain doctor",),
    ),
    ("doctor",): ExplainPage(
        title="Doctor diagnostics",
        command="agentops doctor",
        synopsis=("agentops doctor [OPTIONS] [--evidence-pack] [--evidence-out PATH]", "agentops doctor explain [--format text|markdown|html] [--out PATH] [--open]"),
        summary=(
            "Runs the local diagnostic analyzer for AgentOps workspaces, Foundry, Azure telemetry, and WAF-AI gaps.",
            "With `--evidence-pack`, Doctor also writes a production-readiness evidence pack that summarizes eval, workflow, Foundry, monitoring, AI Landing Zone, and trace-regression signals for release review.",
        ),
        see_also=("agentops doctor explain", "agentops explain cockpit"),
    ),
    ("cockpit",): ExplainPage(
        title="AgentOps Cockpit",
        command="agentops cockpit",
        synopsis=("agentops cockpit [--host HOST] [--port PORT] [--workspace PATH] [--no-preflight]", "agentops cockpit explain"),
        summary=(
            "The local browser Cockpit. Shows the Foundry connection, "
            "one-click jumps to Monitor, Evaluations, Traces, Red Teaming, "
            "and App Insights, an observability readiness checklist, Doctor "
            "findings, local eval history, and the next things to do.",
            "It brings your project, Foundry, and Azure Monitor into one "
            "view — so you can see what's wired up, what's missing, and "
            "where to click when you need to dig deeper.",
            "Read-only and local. Binds to `127.0.0.1` by default so you "
            "can keep it open during reviews and pipeline work without "
            "touching anything in the cloud.",
        ),
        how_it_works=(
            "Runs pre-flight checks unless `--no-preflight` is used.",
            "Reads eval results, Doctor history, reports, workflows, and telemetry metadata.",
            "Resolves Foundry, App Insights, tenant, and RBAC context when configured.",
            "Renders focused sections: connection, launchpad, observability, Doctor, eval gates, quality gates, production signal, CI/CD, and next actions.",
            "Starts a localhost Uvicorn server and opens a browser tab.",
        ),
        inputs=(
            "`.agentops/results/` evaluation history and latest report",
            "`.agentops/agent/history.jsonl` Doctor history",
            "GitHub Actions or Azure DevOps workflow files when present",
            "`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` and Application Insights connection metadata when configured",
        ),
        outputs=(
            "Local web server, default `http://127.0.0.1:8090`",
            "Cockpit page with connection, launchpad, readiness, Doctor, eval, telemetry, CI/CD, and next actions",
        ),
        examples=("agentops cockpit", "agentops cockpit --port 8091", "agentops cockpit --no-preflight", "agentops cockpit explain"),
        see_also=("agentops explain doctor", "agentops explain eval run", "agentops explain workflow generate"),
    ),
}


def _resolve_platforms(
    directory: Path,
    explicit: list[str] | None,
    prompt: bool,
) -> list[str]:
    """Resolve target platforms: explicit > auto-detect > fallback."""
    from agentops.services.skills import detect_platforms

    if explicit:
        return explicit

    detected = detect_platforms(directory)
    if detected:
        typer.echo(
            f"{_cli_label('Detected coding agent platform(s)')}: {', '.join(detected)}"
        )
        return detected

    if prompt:
        install = typer.confirm(
            "No coding agent platform detected. Install skills for GitHub Copilot?",
            default=True,
        )
        return ["copilot"] if install else []

    return ["copilot"]


def _print_skills_result(result: object) -> None:
    """Print skills installation summary."""
    platforms = getattr(result, "platforms", [])
    if platforms:
        typer.echo(f"{_cli_label('Skills platforms')}: {', '.join(platforms)}")
    for created in result.created_files:  # type: ignore[attr-defined]
        typer.echo(_cli_created(created))
    for overwritten in result.overwritten_files:  # type: ignore[attr-defined]
        typer.echo(_cli_overwritten(overwritten))
    for skipped in result.skipped_files:  # type: ignore[attr-defined]
        typer.echo(_cli_skipped(skipped, " (use --force to overwrite)"))


def _print_registration_result(result: object) -> None:
    """Print skill registration summary."""
    registered = getattr(result, "registered_files", [])
    for path in registered:
        typer.echo(f" {_cli_ok('*')} {_cli_ok('registered skills in')} {_cli_path(path)}")


# ---------------------------------------------------------------------------
# Global callback - configures logging before any command runs
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        from agentops import __version__

        typer.echo(f"agentops {__version__}")
        raise typer.Exit()


def _normalize_explain_path(parts: list[str] | None) -> tuple[str, ...]:
    if not parts:
        return ()
    normalized = tuple(part.strip().lower() for part in parts if part.strip())
    if normalized and normalized[-1] == "explain":
        normalized = normalized[:-1]
    return normalized


def _build_registered_explain_markdown(path: tuple[str, ...]) -> str:
    page = EXPLAIN_PAGES.get(path)
    if page is None:
        known = ", ".join(
            " ".join(key) if key else "agentops" for key in sorted(EXPLAIN_PAGES)
        )
        raise ValueError(
            f"unknown command path: {' '.join(path) or 'agentops'}. Known: {known}"
        )

    lines: list[str] = [
        f"# {page.title}",
        "",
        "## NAME",
        "",
        f"`{page.command}` - {page.summary[0]}",
        "",
        "## SYNOPSIS",
        "",
        "```text",
        *page.synopsis,
        "```",
        "",
        "## DESCRIPTION",
        "",
        *page.summary,
    ]
    if page.children:
        lines.extend(
            [
                "",
                "## COMMANDS",
                "",
                "| Command | Detailed docs |",
                "|---|---|",
            ]
        )
        for child in page.children:
            child_path = (*path, child) if path else (child,)
            child_page = EXPLAIN_PAGES.get(child_path)
            label = child_page.command if child_page else f"{page.command} {child}"
            lines.append(f"| `{label}` | `agentops explain {' '.join(child_path)}` |")
    _extend_explain_section(lines, "HOW IT WORKS", page.how_it_works, numbered=True)
    _extend_explain_section(lines, "ARCHITECTURE", page.architecture)
    _extend_explain_section(lines, "INPUTS", page.inputs)
    _extend_explain_section(lines, "OUTPUTS", page.outputs)
    if page.examples:
        lines.extend(["", "## EXAMPLES", "", "```text", *page.examples, "```"])
    if page.see_also:
        lines.extend(["", "## SEE ALSO", ""])
        lines.extend(f"- `{item}`" if item.startswith("agentops") else f"- {item}" for item in page.see_also)
    lines.append("")
    return "\n".join(lines)


def _extend_explain_section(
    lines: list[str],
    title: str,
    items: tuple[str, ...],
    *,
    numbered: bool = False,
) -> None:
    if not items:
        return
    lines.extend(["", f"## {title}", ""])
    for index, item in enumerate(items, start=1):
        marker = f"{index}." if numbered else "-"
        lines.append(f"{marker} {item}")


def _registered_explain_text(path: tuple[str, ...]) -> str:
    page = EXPLAIN_PAGES.get(path)
    if page is None:
        raise ValueError(f"unknown command path: {' '.join(path) or 'agentops'}")
    lines: list[str] = _manual_banner(page.title, page.summary[0])

    def section(title: str) -> None:
        _manual_section(lines, title)

    section("NAME")
    _emit_name_line(lines, page.command, page.summary[0])
    section("SYNOPSIS")
    lines.extend(f"  {style('$', 'dim')} {style(entry, 'bold')}" for entry in page.synopsis)
    section("DESCRIPTION")
    description_paragraphs = page.summary[1:] if len(page.summary) > 1 else page.summary
    lines.extend(_manual_paragraphs(*description_paragraphs))
    if page.children:
        section("COMMANDS")
        rows: list[tuple[str, str]] = []
        for child in page.children:
            child_path = (*path, child) if path else (child,)
            child_page = EXPLAIN_PAGES.get(child_path)
            label = child_page.command if child_page else f"{page.command} {child}"
            if child_page:
                rows.append((label, child_page.summary[0]))
            else:
                rows.append((label, ""))
        lines.extend(_manual_command_rows(rows))
    _extend_text_section(lines, "HOW IT WORKS", page.how_it_works, numbered=True)
    _extend_text_section(lines, "ARCHITECTURE", page.architecture)
    _extend_text_section(lines, "INPUTS", page.inputs)
    _extend_text_section(lines, "OUTPUTS", page.outputs)
    if page.examples:
        section("EXAMPLES")
        lines.extend(f"  {style('$', 'dim')} {style(entry, 'bold')}" for entry in page.examples)
    if page.see_also:
        section("SEE ALSO")
        lines.extend(f"  {entry}" for entry in page.see_also)
    return "\n".join(lines) + "\n"


def _extend_text_section(
    lines: list[str],
    title: str,
    items: tuple[str, ...],
    *,
    numbered: bool = False,
) -> None:
    if not items:
        return
    _manual_section(lines, title)
    for index, item in enumerate(items, start=1):
        prefix = f"{index}. " if numbered else "- "
        lines.extend(_manual_item_lines(prefix, item))


_ASCII_TRANSLITERATION: dict[int, str] = {
    ord("\u2014"): "-",   # em dash
    ord("\u2013"): "-",   # en dash
    ord("\u2212"): "-",   # minus sign
    ord("\u2018"): "'",   # left single quote
    ord("\u2019"): "'",   # right single quote
    ord("\u201c"): '"',   # left double quote
    ord("\u201d"): '"',   # right double quote
    ord("\u2026"): "...", # horizontal ellipsis
    ord("\u00a0"): " ",   # non-breaking space
    ord("\u2192"): "->",  # rightwards arrow
    ord("\u2197"): "^",   # north-east arrow
    ord("\u2022"): "*",   # bullet
    ord("\u00b7"): "*",   # middle dot
}


def _downgrade_to_ascii(text: str) -> str:
    """Replace common typographic Unicode with ASCII equivalents.

    Used when the terminal cannot render UTF-8 (e.g. legacy Windows
    code pages) to avoid mojibake like ``ù`` in the place of an em dash.
    Only typographic punctuation/arrows are downgraded - box-drawing
    characters were already gated by ``_terminal_unicode_enabled()``.
    """
    if not text:
        return text
    return text.translate(_ASCII_TRANSLITERATION)


def _emit_manual_output(
    *,
    text: str,
    markdown: str,
    title: str,
    no_pager: bool,
    format_: str,
    out: Path | None,
    open_browser: bool,
) -> None:
    import click

    format_ = format_.lower()
    if format_ not in {"text", "markdown", "html"}:
        typer.echo(
            f"{_cli_error('Invalid --format')}. Use one of: text, markdown, html.",
            err=True,
        )
        raise typer.Exit(code=1)

    html = _build_explain_html(markdown, title=title)
    output = {"text": text, "markdown": markdown, "html": html}[format_]
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
        typer.echo(f"{_cli_label('Wrote')}: {_cli_path(out)}")

    if open_browser:
        browser_path = out if out is not None and format_ == "html" else None
        if browser_path is None:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".html",
                prefix="agentops-explain-",
                delete=False,
            ) as temp:
                temp.write(html)
                browser_path = Path(temp.name)
            typer.echo(f"{_cli_label('Opened browser copy')}: {_cli_path(browser_path)}")
        webbrowser.open(browser_path.resolve().as_uri())

    if out is not None or open_browser:
        return

    if format_ == "text" and not _terminal_unicode_enabled():
        output = _downgrade_to_ascii(output)

    if format_ != "text":
        if no_pager:
            typer.echo(output, color=True)
            return
        click.echo_via_pager(output, color=True)
        return

    if no_pager:
        _emit_manual_to_terminal(output)
        return
    if _useful_pager_available():
        click.echo_via_pager(output, color=True)
        return
    _emit_manual_with_internal_pager(output)


def _emit_registered_explain(
    path: tuple[str, ...],
    *,
    no_pager: bool,
    format_: str,
    out: Path | None,
    open_browser: bool,
) -> None:
    try:
        text = _registered_explain_text(path)
        markdown = _build_registered_explain_markdown(path)
    except ValueError as exc:
        typer.echo(f"{_cli_error('Error')}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    page = EXPLAIN_PAGES[path]
    _emit_manual_output(
        text=text,
        markdown=markdown,
        title=page.title,
        no_pager=no_pager,
        format_=format_,
        out=out,
        open_browser=open_browser,
    )


def _maybe_explain_leaf(
    path: tuple[str, ...],
    explain: str | None,
) -> bool:
    if explain is None:
        return False
    if explain.lower() != "explain":
        typer.echo(f"{_cli_error('Error')}: unexpected argument {explain!r}.", err=True)
        raise typer.Exit(code=1)
    _emit_registered_explain(
        path,
        no_pager=True,
        format_="text",
        out=None,
        open_browser=False,
    )
    return True


@app.callback()
def _main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable DEBUG logging."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    setup_logging(verbose=verbose)


@app.command("explain", context_settings={"allow_extra_args": True})
def cmd_explain(
    ctx: typer.Context,
    command_path: Annotated[
        list[str] | None,
        typer.Argument(help="Optional command path, for example: eval run."),
    ] = None,
    no_pager: Annotated[
        bool,
        typer.Option("--no-pager", help="Print directly instead of opening the pager."),
    ] = False,
    format_: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, markdown, or html."),
    ] = "text",
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Write the manual to a file."),
    ] = None,
    open_browser: Annotated[
        bool,
        typer.Option("--open", help="Open a browser-friendly HTML copy."),
    ] = False,
) -> None:
    """Open detailed documentation for AgentOps or one command."""
    parts = list(command_path or [])
    parts.extend(ctx.args)
    _emit_registered_explain(
        _normalize_explain_path(parts),
        no_pager=no_pager,
        format_=format_,
        out=out,
        open_browser=open_browser,
    )


def _make_group_explain(path: tuple[str, ...]):
    def _cmd(
        no_pager: Annotated[
            bool,
            typer.Option("--no-pager", help="Print directly instead of opening the pager."),
        ] = False,
        format_: Annotated[
            str,
            typer.Option("--format", "-f", help="Output format: text, markdown, or html."),
        ] = "text",
        out: Annotated[
            Path | None,
            typer.Option("--out", "-o", help="Write the manual to a file."),
        ] = None,
        open_browser: Annotated[
            bool,
            typer.Option("--open", help="Open a browser-friendly HTML copy."),
        ] = False,
    ) -> None:
        _emit_registered_explain(
            path,
            no_pager=no_pager,
            format_=format_,
            out=out,
            open_browser=open_browser,
        )

    _cmd.__name__ = "cmd_" + "_".join((*path, "explain"))
    _cmd.__doc__ = "Open detailed documentation for this command group."
    return _cmd


eval_app.command("explain")(_make_group_explain(("eval",)))
report_app.command("explain")(_make_group_explain(("report",)))
workflow_app.command("explain")(_make_group_explain(("workflow",)))
skills_app.command("explain")(_make_group_explain(("skills",)))
mcp_app.command("explain")(_make_group_explain(("mcp",)))
agent_app.command("explain")(_make_group_explain(("agent",)))


# ---------------------------------------------------------------------------
# agentops init  (group: callback = scaffold + bootstrap + wizard)
# ---------------------------------------------------------------------------


@init_app.callback(invoke_without_command=True)
def cmd_init(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite starter files if they exist.",
        ),
    ] = False,
    directory: Annotated[
        Path,
        typer.Option(
            "--dir",
            "--path",
            help="Workspace directory to initialise (defaults to the current directory).",
        ),
    ] = Path("."),
    no_prompt: Annotated[
        bool,
        typer.Option(
            "--no-prompt",
            help="Skip the interactive question loop (scaffold-only mode).",
        ),
    ] = False,
    reconfigure: Annotated[
        bool,
        typer.Option(
            "--reconfigure",
            help="Re-ask every wizard question, even when a value is already set.",
        ),
    ] = False,
    no_appinsights: Annotated[
        bool,
        typer.Option(
            "--no-appinsights",
            help=(
                "Deprecated no-op; App Insights is no longer asked in the "
                "interactive wizard."
            ),
        ),
    ] = False,
    project_endpoint: Annotated[
        Optional[str],
        typer.Option(
            "--project-endpoint",
            help="Set the Foundry project endpoint non-interactively.",
        ),
    ] = None,
    agent: Annotated[
        Optional[str],
        typer.Option(
            "--agent",
            help="Set the agent identifier non-interactively (name:version, model:deployment, or URL).",
        ),
    ] = None,
    dataset: Annotated[
        Optional[str],
        typer.Option(
            "--dataset",
            help="Set the dataset path non-interactively.",
        ),
    ] = None,
    appinsights_connection_string: Annotated[
        Optional[str],
        typer.Option(
            "--appinsights-connection-string",
            help="Set the App Insights connection string non-interactively (saved to .azure/<env>/.env).",
        ),
    ] = None,
    azd_env_name: Annotated[
        Optional[str],
        typer.Option(
            "--azd-env",
            help=(
                "Name of the azd environment to write into. Defaults to "
                "the active azd environment, or 'dev' when bootstrapping."
            ),
        ),
    ] = None,
) -> None:
    """Initialise an AgentOps workspace and configure endpoints.

    Scaffolds the minimal workspace layout (``agentops.yaml`` plus a tiny
    seed dataset under ``.agentops/data/``), prepares a ``.azure/``
    directory shaped the way ``azd`` expects, and walks the user through
    an azd-style question loop to fill in the values AgentOps needs to
    evaluate, observe, and analyze a Foundry agent.

    ``agent`` and ``dataset`` land in ``agentops.yaml`` (version-
    controlled). The Foundry project endpoint lands in the active
    ``.azure/<env>/.env`` file (git-ignored) — the same file ``azd``
    already manages — so a single source of truth feeds Doctor, the
    Cockpit, and ``agentops eval run``. App Insights can be supplied
    explicitly with ``--appinsights-connection-string`` if runtime discovery
    is not enough.

    The wizard persists each answer immediately as it is validated, so a
    Ctrl+C mid-wizard never discards what the user already entered.
    """
    if ctx.invoked_subcommand is not None:
        return

    from agentops.services.initializer import initialize_flat_workspace
    from agentops.services.setup_wizard import (
        AGENT_TITLE,
        DATASET_TITLE,
        PROJECT_ENDPOINT_TITLE,
        WizardAnswers,
        apply_answers,
        discover_defaults,
        run_wizard,
        validate_agent,
        validate_dataset,
        validate_project_endpoint,
    )
    from agentops.utils.azd_env import (
        discover_azd_env,
        ensure_azd_env,
        set_default_azd_env,
    )

    workspace = directory.resolve()
    if not workspace.exists():
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            typer.echo(
                f"{_cli_error('Error')}: could not create workspace directory "
                f"{_cli_path(workspace)}: {exc}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    log.debug(
        "cmd_init called force=%s dir=%s no_prompt=%s no_appinsights=%s any_flag=%s",
        force,
        workspace,
        no_prompt,
        no_appinsights,
        any(
            v is not None
            for v in (project_endpoint, agent, dataset, appinsights_connection_string)
        ),
    )

    # The cherry on top: greet the user with the AgentOps brand banner
    # so `agentops init` feels like a first-class onboarding moment
    # rather than a silent scaffolding step.
    #
    # Emit via :func:`_emit_manual_to_terminal` (the same helper the
    # explain pages use when no real pager is available). It writes the
    # raw UTF-8 banner bytes straight to ``sys.stdout.buffer``, bypassing
    # Click's Windows console writer (``click._winconsole.ConsoleStream``
    # → ``WriteConsoleW``), which on TTY stdouts is the path that
    # mangles the 24-bit RGB gradient (``ESC[38;2;R;G;Bm``) into the
    # scrambled colored-block rendering seen in earlier builds. We also
    # gate on :func:`_terminal_color_enabled` so colorless CI runs and
    # ``CliRunner``-based tests still flow through ``typer.echo`` and
    # remain easy to assert on.
    _emit_init_banner()

    # ----- Phase 1: scaffold the .agentops/ workspace ---------------------
    try:
        result = initialize_flat_workspace(directory=workspace, force=force)
    except Exception as exc:
        typer.echo(
            f"{_cli_error('Error')}: failed to initialize workspace: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(_cli_ok("Initialized AgentOps workspace."))
    for created in result.created_files:
        typer.echo(_cli_created(created))
    for overwritten in result.overwritten_files:
        typer.echo(_cli_overwritten(overwritten))
    for skipped in result.skipped_files:
        typer.echo(_cli_skipped(skipped))

    config_path = workspace / "agentops.yaml"
    config_seeded_this_run = (
        config_path in result.created_files
        or config_path in result.overwritten_files
    )

    # ----- Phase 2: ensure a .azure/<env>/ baseline exists ----------------
    target_env_name = azd_env_name or "dev"
    if azd_env_name:
        # User explicitly named an azd env — create it if missing AND
        # promote it to the active default so subsequent reads/writes
        # target the right directory. This is the persistent equivalent
        # of `azd env select <name>` and does not pollute the process
        # environment (so test isolation remains clean).
        try:
            location = ensure_azd_env(workspace, azd_env_name)
            set_default_azd_env(workspace, azd_env_name)
        except Exception as exc:  # noqa: BLE001
            typer.echo(
                f" {_cli_warn('!')} {_cli_warn('could not prepare')} "
                f"{_cli_path(f'.azure/{azd_env_name}')}: {exc}",
                err=True,
            )
            location = discover_azd_env(workspace)
    else:
        location = discover_azd_env(workspace)
        if not location.found and location.status != "ambiguous":
            env_name = location.name or target_env_name
            try:
                location = ensure_azd_env(workspace, env_name)
                typer.echo(
                    f" {_cli_ok('+')} {_cli_ok('bootstrapped azd environment')} "
                    f"'{env_name}' at {_cli_path(location.env_path.parent)}"
                    if location.env_path is not None
                    else f" {_cli_ok('+')} {_cli_ok('bootstrapped azd environment')} "
                    f"'{env_name}'"
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(
                    f" {_cli_warn('!')} {_cli_warn('could not bootstrap')} "
                    f"{_cli_path(f'.azure/{env_name}')}: {exc}",
                    err=True,
                )

    # ----- Phase 3: collect values (scripted, interactive, or skip) ------
    flag_values = {
        "project_endpoint": project_endpoint,
        "agent": agent,
        "dataset": dataset,
        "appinsights_connection_string": appinsights_connection_string,
    }
    any_flag = any(v is not None for v in flag_values.values())

    answers: Optional[WizardAnswers] = None

    if any_flag:
        # Scripted mode — validate then apply.
        if project_endpoint is not None:
            err = validate_project_endpoint(project_endpoint)
            if err:
                typer.echo(f"{_cli_error('Error')}: --project-endpoint: {err}", err=True)
                raise typer.Exit(code=1)
        if agent is not None:
            err = validate_agent(agent)
            if err:
                typer.echo(f"{_cli_error('Error')}: --agent: {err}", err=True)
                raise typer.Exit(code=1)
        if dataset is not None:
            err = validate_dataset(dataset, workspace)
            if err:
                typer.echo(f"{_cli_error('Error')}: --dataset: {err}", err=True)
                raise typer.Exit(code=1)
        answers = WizardAnswers(
            project_endpoint=project_endpoint,
            agent=agent,
            dataset=dataset,
            appinsights_connection_string=appinsights_connection_string,
        )
    elif no_prompt or not sys.stdin.isatty():
        # Scaffold-only mode: either the user explicitly asked for it via
        # --no-prompt, or stdin is not a TTY (CI, piped stdin, etc.) and
        # the interactive wizard cannot run. Print the next-step hints and
        # exit cleanly.
        typer.echo("")
        typer.echo(_cli_heading("Workspace ready. Next steps:"))
        typer.echo(f"  {_cli_command('agentops init')}                      # interactive wizard to set endpoints")
        typer.echo(f"  {_cli_command('agentops init show')}                 # inspect the active configuration")
        typer.echo(f"  {_cli_command('agentops eval analyze')}              # inspect eval setup before running")
        typer.echo(f"  {_cli_command('agentops eval run')}                  # run a configured evaluation")
        typer.echo(f"  {_cli_command('agentops skills install')}            # install coding agent skills")
        if not no_prompt and not sys.stdin.isatty():
            typer.echo("")
            typer.echo(
                f"{_cli_warn('Tip')}: stdin is not a TTY, so the interactive wizard was skipped. "
                "Re-run with --project-endpoint, --agent, --dataset and/or "
                "--appinsights-connection-string to script the configuration."
            )
        return
    else:
        # Interactive mode — TTY confirmed.
        typer.echo("")
        unicode_ok = _terminal_unicode_enabled()
        typer.echo(_cli_heading("AgentOps configuration"))
        typer.echo(style("──────────────────────" if unicode_ok else "----------------------", "dim"))

        defaults = discover_defaults(workspace)

        # Only show the prompt hint when at least one question will actually
        # be asked. When everything is already configured (idempotent re-run),
        # the wizard emits compact confirmation lines instead.
        force_prompt_fields = {"agent", "dataset"} if config_seeded_this_run else set()
        prompt_values = [
            defaults.project_endpoint,
            defaults.agent,
            defaults.dataset,
        ]
        will_prompt = reconfigure or bool(force_prompt_fields) or any(
            v is None or not str(v).strip()
            for v in prompt_values
        )
        if will_prompt:
            typer.echo(style("Press Enter to accept the value in brackets.", "dim"))

        def _prompt(question: str, default: Optional[str]) -> str:
            return typer.prompt(question, default=default or "", show_default=bool(default))

        # Incremental persistence: each answer is written as soon as the
        # wizard validates it, so Ctrl+C never throws away progress.
        bullet = "·" if unicode_ok else "-"
        question_titles = {
            PROJECT_ENDPOINT_TITLE,
            AGENT_TITLE,
            DATASET_TITLE,
        }

        def _on_answer(field_name: str, value: str) -> None:
            partial = WizardAnswers(**{field_name: value})
            try:
                partial_result = apply_answers(
                    workspace,
                    partial,
                    default_env_name=target_env_name,
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(
                    f"  {_cli_warn('!')} {_cli_warn('could not persist')} "
                    f"{field_name}: {exc}",
                    err=True,
                )
                return
            if partial_result.yaml_updated and field_name in partial_result.yaml_fields:
                typer.echo(f"  {bullet} {_cli_ok('saved to')} {_cli_path(partial_result.yaml_path)}")
            if partial_result.env_updated and partial_result.env_path is not None:
                typer.echo(f"  {bullet} {_cli_ok('saved to')} {_cli_path(partial_result.env_path)}")

        def _wizard_echo(msg: str) -> None:
            if msg in question_titles:
                typer.echo(style(msg, "bold", "cyan"))
            else:
                typer.echo(msg)

        answers = run_wizard(
            workspace,
            prompt=_prompt,
            echo=_wizard_echo,
            on_answer=_on_answer,
            reconfigure=reconfigure,
            force_prompt_fields=force_prompt_fields,
        )

    # ----- Phase 4: apply (idempotent — covers scripted mode and any
    # residual fields the wizard returned). The wizard's own on_answer
    # already wrote each value, but applying the full set again is safe
    # and ensures scripted callers (which skip on_answer) still persist.
    if answers is None:
        return

    try:
        final_result = apply_answers(
            workspace,
            answers,
            default_env_name=target_env_name,
        )
    except RuntimeError as exc:
        typer.echo(f"{_cli_error('Error')}: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    unicode_ok = _terminal_unicode_enabled()
    ok = "✓" if unicode_ok else "*"
    bullet = "·" if unicode_ok else "-"
    if final_result.azd_env_created and final_result.env_path is not None:
        typer.echo(
            f"  {ok} {_cli_ok('created azd environment')} "
            f"'{final_result.azd_env_name}' at {_cli_path(final_result.env_path.parent)}"
        )
    if final_result.yaml_updated:
        typer.echo(_cli_updated(final_result.yaml_path))
        for name in final_result.yaml_fields:
            typer.echo(f"     {bullet} {_cli_label(name)}")
    if final_result.env_updated and final_result.env_path is not None:
        typer.echo(_cli_updated(final_result.env_path))
        for name in final_result.env_keys:
            typer.echo(f"     {bullet} {_cli_label(name)}")
    if (
        not final_result.yaml_updated
        and not final_result.env_updated
        and not final_result.azd_env_created
    ):
        typer.echo(_cli_ok("No configuration changes — every value was already up to date."))

    typer.echo("")
    typer.echo(_cli_heading("Next steps:"))
    typer.echo(f"  {_cli_command('agentops init show')}       # inspect the active configuration")
    typer.echo(f"  {_cli_command('agentops eval analyze')}    # inspect eval setup before running")
    typer.echo(f"  {_cli_command('agentops doctor')}          # validate the workspace")
    typer.echo(f"  {_cli_command('agentops eval run')}        # run a configured evaluation")
    typer.echo(f"  {_cli_command('agentops cockpit')}         # open the local cockpit")
    typer.echo(f"  {_cli_command('agentops skills install')}  # install coding agent skills")


@init_app.command("show")
def cmd_init_show(
    directory: Annotated[
        Path,
        typer.Option(
            "--dir",
            "--path",
            help="Workspace directory to inspect (defaults to the current directory).",
        ),
    ] = Path("."),
    reveal_secrets: Annotated[
        bool,
        typer.Option(
            "--reveal-secrets",
            help="Print secrets in full instead of masking them. Use with care.",
        ),
    ] = False,
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Show the active AgentOps configuration and where each value comes from."""
    if _maybe_explain_leaf(("init", "show"), explain):
        return

    from agentops.services.setup_wizard import collect_snapshot, mask_secret

    workspace = directory.resolve()
    if not workspace.exists():
        typer.echo(
            f"{_cli_error('Error')}: workspace directory does not exist: "
            f"{_cli_path(workspace)}",
            err=True,
        )
        raise typer.Exit(code=1)

    snapshot = collect_snapshot(workspace)
    unicode_ok = _terminal_unicode_enabled()
    ok = "✓" if unicode_ok else "*"
    miss = "✗" if unicode_ok else "x"
    warn = "!" if unicode_ok else "!"

    def _config_label(text: str) -> str:
        return _cli_label(text)

    typer.echo("")
    typer.echo(_config_label("AgentOps configuration"))
    typer.echo(style("──────────────────────" if unicode_ok else "----------------------", "dim"))
    typer.echo(f"{_config_label('Workspace')}: {snapshot.workspace}")

    # azd environment block ------------------------------------------------
    typer.echo("")
    typer.echo(_config_label("azd environment"))
    if snapshot.azd_env_name and snapshot.azd_env_path is not None:
        marker = ok if snapshot.azd_status == "ok" else warn
        typer.echo(f"  {marker} {_config_label('name')}: {snapshot.azd_env_name}")
        typer.echo(f"    {_config_label('path')}: {snapshot.azd_env_path}")
        typer.echo(f"    {_config_label('status')}: {snapshot.azd_status}")
    else:
        typer.echo(f"  {warn} no azd environment found")
        if snapshot.azd_reason:
            typer.echo(f"    {_config_label('reason')}: {snapshot.azd_reason}")
        typer.echo("    run `agentops init` to bootstrap one")

    # agentops.yaml block --------------------------------------------------
    typer.echo("")
    typer.echo(_config_label("agentops.yaml"))
    if snapshot.yaml_present:
        typer.echo(f"  {ok} {snapshot.yaml_path}")
        typer.echo(f"    {_config_label('agent')}:   {snapshot.yaml_agent or '(not set)'}")
        typer.echo(f"    {_config_label('dataset')}: {snapshot.yaml_dataset or '(not set)'}")
        if snapshot.yaml_project_endpoint:
            typer.echo(
                f"    {_config_label('project_endpoint')} (legacy yaml override): "
                f"{snapshot.yaml_project_endpoint}"
            )
    else:
        typer.echo(f"  {warn} {snapshot.yaml_path} does not exist")
        typer.echo("    run `agentops init` to scaffold the workspace")

    # environment variables block -----------------------------------------
    typer.echo("")
    typer.echo(_config_label("Environment variables"))
    for var in snapshot.variables:
        if var.value:
            display = var.value if (not var.secret or reveal_secrets) else mask_secret(var.value)
            marker = ok
        else:
            display = "(not set)"
            marker = miss if var.required else warn
        required_label = style(" [required]", "yellow") if var.required else ""
        typer.echo(f"  {marker} {_config_label(var.key)}{required_label}")
        typer.echo(f"    {_config_label('value')}:  {display}")
        typer.echo(f"    {_config_label('source')}: {var.source}")
        if var.description:
            typer.echo(f"    {_config_label('info')}:   {var.description}")

    # Migration notice -----------------------------------------------------
    if snapshot.legacy_env_path is not None:
        typer.echo("")
        typer.echo(
            f"{_cli_warn('Note')}: legacy {_cli_path(snapshot.legacy_env_path)} "
            "is still being read for "
            "backward compatibility. The wizard now writes to the active "
            "azd environment instead — re-run `agentops init` to migrate."
        )

    # Exit code: 1 if a required variable is missing.
    if snapshot.missing_required:
        typer.echo("")
        typer.echo(
            f"{_cli_error('Missing required values')}: "
            f"{', '.join(snapshot.missing_required)}. "
            "Run `agentops init` to provide them.",
            err=True,
        )
        raise typer.Exit(code=1)


@init_app.command("explain")
def cmd_init_explain(
    no_pager: Annotated[
        bool, typer.Option("--no-pager", help="Print directly to stdout.")
    ] = False,
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output format: text (default), markdown, or html.",
        ),
    ] = "text",
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Write the explanation to PATH instead of stdout."),
    ] = None,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open",
            help="When --format html, open the rendered file in the default browser.",
        ),
    ] = False,
) -> None:
    """Long-form manual for `agentops init` and `agentops init show`."""
    _emit_registered_explain(
        ("init",),
        no_pager=no_pager,
        format_=fmt,
        out=out,
        open_browser=open_browser,
    )


# ---------------------------------------------------------------------------
# agentops eval analyze / run
# ---------------------------------------------------------------------------


@eval_app.command("analyze")
def cmd_eval_analyze(
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text, markdown, or json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Write the analysis to a file instead of stdout.",
    ),
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Analyze this repo's evaluation setup before running eval."""
    if _maybe_explain_leaf(("eval", "analyze"), explain):
        return

    from agentops.services.eval_analysis import analyze_eval_project, render_eval_analysis

    normalized_format = output_format.lower()
    if normalized_format not in {"text", "markdown", "json"}:
        typer.echo(
            f"{_cli_error('Error')}: --format must be text, markdown, or json.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        analysis = analyze_eval_project(directory)
        rendered = render_eval_analysis(analysis, normalized_format)
    except Exception as exc:
        typer.echo(
            f"{_cli_error('Error')}: failed to analyze evaluation setup: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        typer.echo(f"{_cli_label('Wrote')}: {_cli_path(out)}")
        return

    if normalized_format == "text":
        rendered = _colorize_analysis_text(rendered)
    typer.echo(rendered, color=True)


@eval_app.command("run")
def cmd_eval_run(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to agentops.yaml. Defaults to ./agentops.yaml.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for results."),
    ] = None,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help="Path to a previous results.json to compare this run against.",
        ),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md, html, or all.")
    ] = "md",
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Run an evaluation defined in agentops.yaml."""
    if _maybe_explain_leaf(("eval", "run"), explain):
        return

    if report_format not in ("md", "html", "all"):
        typer.echo(
            f"{_cli_error('Error')}: --format must be md, html, or all.",
            err=True,
        )
        raise typer.Exit(code=1)

    config_path = _resolve_eval_config_path(config)
    log.debug(
        "cmd_eval_run called config=%s output=%s format=%s baseline=%s",
        config_path,
        output,
        report_format,
        baseline,
    )

    if not config_path.exists():
        typer.echo(
            f"{_cli_error('Error')}: config not found at {_cli_path(config_path)}. "
            "Run `agentops init` to scaffold a starter agentops.yaml.",
            err=True,
        )
        raise typer.Exit(code=1)

    _run_flat_schema_eval(
        config_path=config_path,
        output=output,
        baseline=baseline,
    )


@eval_app.command("promote-traces")
def cmd_eval_promote_traces(
    source: Annotated[
        Path,
        typer.Option("--source", "-s", help="JSON or JSONL trace export to convert."),
    ],
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Dataset JSONL path to write when --apply is used.",
        ),
    ] = Path(".agentops/data/trace-regression.jsonl"),
    max_rows: Annotated[
        int,
        typer.Option("--max-rows", help="Maximum candidate rows to keep."),
    ] = 50,
    label_mode: Annotated[
        str,
        typer.Option(
            "--label-mode",
            help="How to label expected values: self-similarity or pending.",
        ),
    ] = "self-similarity",
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the dataset and manifest instead of previewing only."),
    ] = False,
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Promote trace exports into reviewable regression dataset rows."""
    if _maybe_explain_leaf(("eval", "promote-traces"), explain):
        return

    from agentops.services.trace_promotion import (
        promote_traces,
        render_trace_promotion_preview,
    )

    normalized_mode = label_mode.lower()
    if normalized_mode not in {"self-similarity", "pending"}:
        typer.echo(
            f"{_cli_error('Error')}: --label-mode must be self-similarity or pending.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        preview = promote_traces(
            source=source,
            output_path=out,
            max_rows=max_rows,
            label_mode=normalized_mode,  # type: ignore[arg-type]
            apply=apply,
        )
    except Exception as exc:
        typer.echo(f"{_cli_error('Error')}: trace promotion failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(_colorize_analysis_text(render_trace_promotion_preview(preview)), color=True)
    if apply:
        typer.echo(f"{_cli_label('Wrote dataset')}: {_cli_path(preview.output_path)}")
        typer.echo(f"{_cli_label('Wrote manifest')}: {_cli_path(preview.manifest_path)}")
    else:
        typer.echo(
            f"{_cli_warn('Preview only')}: re-run with `{_cli_command('--apply')}` to write files."
        )


def _resolve_eval_config_path(config: Path | None) -> Path:
    if config is not None:
        return config
    return Path("agentops.yaml")


def _run_flat_schema_eval(
    *,
    config_path: Path,
    output: Path | None,
    baseline: Path | None,
) -> None:
    from agentops.core.config_loader import load_agentops_config
    from agentops.pipeline.orchestrator import (
        RunOptions,
        exit_code_from,
        run_evaluation,
    )

    try:
        config_obj = load_agentops_config(config_path)
    except Exception as exc:
        typer.echo(
            f"{_cli_error('Error')}: failed to load {_cli_path(config_path)}: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    use_default_layout = output is None
    if use_default_layout:
        output_dir: Path = _default_flat_output_dir(config_path)
    else:
        assert output is not None
        output_dir = output

    options = RunOptions(
        config_path=config_path.resolve(),
        output_dir=output_dir,
        baseline_path=baseline.resolve() if baseline else None,
        progress=lambda msg: typer.echo(msg),
    )

    try:
        result = run_evaluation(config_obj, options=options)
    except Exception as exc:
        typer.echo(f"{_cli_error('Error')}: evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    latest_dir = config_path.parent / ".agentops" / "results" / "latest"
    if output_dir.resolve() != latest_dir.resolve():
        try:
            _mirror_to_latest(output_dir, latest_dir)
        except Exception as exc:  # pragma: no cover - mirror failures shouldn't fail the run
            typer.echo(
                f"{_cli_warn('Warning')}: failed to update {_cli_path(latest_dir)}: {exc}",
                err=True,
            )
            latest_dir = None  # type: ignore[assignment]
    else:
        latest_dir = None  # type: ignore[assignment]

    typer.echo(f"{_cli_label('Evaluation output directory')}: {_cli_path(output_dir)}")
    typer.echo(f"{_cli_label('results.json')}: {_cli_path(output_dir / 'results.json')}")
    typer.echo(f"{_cli_label('report.md')}:    {_cli_path(output_dir / 'report.md')}")
    if latest_dir is not None:
        typer.echo(f"{_cli_label('latest/')}:      {_cli_path(latest_dir)}")
    if result.summary.overall_passed:
        typer.echo(f"{_cli_label('Threshold status')}: {style('PASSED', 'bold', 'green')}")
        return
    typer.echo(f"{_cli_label('Threshold status')}: {style('FAILED', 'bold', 'red')}")
    raise typer.Exit(code=exit_code_from(result))


def _default_flat_output_dir(config_path: Path) -> Path:
    base = config_path.parent / ".agentops" / "results"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return base / timestamp


def _mirror_to_latest(source: Path, latest: Path) -> None:
    """Replace ``latest`` with a copy of ``source``."""
    if latest.exists():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        else:
            shutil.rmtree(latest)
    shutil.copytree(source, latest)


def _is_flat_results(results_path: Path) -> bool:
    """Return True when results.json was produced by the flat pipeline."""
    if not results_path.exists():
        return False
    try:
        import json as _json
        data = _json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    target = data.get("target")
    return (
        data.get("version") == 1
        and isinstance(target, dict)
        and "kind" in target
        and "bundle" not in data
    )


def _regenerate_flat_report(
    *,
    results_path: Path,
    output_path: Path | None,
    report_format: str,
) -> Path:
    """Render report.md from a flat-pipeline results.json."""
    import json as _json

    from agentops.core.results import RunResult
    from agentops.pipeline import reporter as flat_reporter

    if report_format not in ("md", "all"):
        raise ValueError(
            "Only --format md is supported (got %r)" % report_format
        )
    payload = _json.loads(results_path.read_text(encoding="utf-8"))
    result = RunResult.model_validate(payload)
    target = output_path or (results_path.parent / "report.md")
    target.write_text(flat_reporter.render(result), encoding="utf-8")
    return target



# ---------------------------------------------------------------------------
# agentops report generate
# ---------------------------------------------------------------------------


@report_app.command("generate")
def cmd_report_generate(
    results_in: Annotated[
        Path | None,
        typer.Option(
            "--in",
            help=(
                "Path to results.json. "
                "If omitted, uses .agentops/results/latest/results.json"
            ),
        ),
    ] = None,
    report_out: Annotated[
        Path | None,
        typer.Option("--out", help="Output path for report."),
    ] = None,
    report_format: Annotated[
        str, typer.Option("--format", "-f", help="Report format: md (default).")
    ] = "md",
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Regenerate report.md from a results.json file."""
    if _maybe_explain_leaf(("report", "generate"), explain):
        return

    if report_format not in ("md", "all"):
        typer.echo(f"{_cli_error('Error')}: --format must be md or all.", err=True)
        raise typer.Exit(code=1)

    resolved_results_in = results_in or DEFAULT_REPORT_INPUT
    log.debug(
        "cmd_report_generate called in=%s out=%s format=%s",
        resolved_results_in,
        report_out,
        report_format,
    )

    if not resolved_results_in.exists():
        typer.echo(
            f"{_cli_error('Error')}: results not found at "
            f"{_cli_path(resolved_results_in)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not _is_flat_results(resolved_results_in):
        typer.echo(
            f"{_cli_error('Error')}: {_cli_path(resolved_results_in)} is not "
            "an AgentOps 1.0 results.json. "
            "Re-run `agentops eval run` to regenerate it.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        output_path = _regenerate_flat_report(
            results_path=resolved_results_in,
            output_path=report_out,
            report_format=report_format,
        )
    except Exception as exc:
        typer.echo(f"{_cli_error('Error')}: report generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{_cli_label('Loaded results')}: {_cli_path(resolved_results_in)}")
    typer.echo(f"{_cli_label('Generated report')}: {_cli_path(output_path)}")


# ---------------------------------------------------------------------------
# agentops workflow analyze / generate
# ---------------------------------------------------------------------------


@workflow_app.command("analyze")
def cmd_workflow_analyze(
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text, markdown, or json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Write the analysis to a file instead of stdout.",
    ),
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Analyze this repo's CI/CD shape before generating workflows."""
    if _maybe_explain_leaf(("workflow", "analyze"), explain):
        return

    from agentops.services.workflow_analysis import (
        analyze_workflow_project,
        render_workflow_analysis,
    )

    normalized_format = output_format.lower()
    if normalized_format not in {"text", "markdown", "json"}:
        typer.echo(
            f"{_cli_error('Error')}: --format must be text, markdown, or json.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        analysis = analyze_workflow_project(directory)
        rendered = render_workflow_analysis(analysis, normalized_format)
    except Exception as exc:
        typer.echo(
            f"{_cli_error('Error')}: failed to analyze CI/CD workflow shape: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        typer.echo(f"{_cli_label('Wrote')}: {_cli_path(out)}")
        return

    if normalized_format == "text":
        rendered = _colorize_analysis_text(rendered)
    typer.echo(rendered, color=True)


@workflow_app.command("generate")
def cmd_workflow_generate(
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing workflow files."
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
    kinds: str = typer.Option(
        "",
        "--kinds",
        help=(
            "Comma-separated subset of workflow kinds to generate. "
            "Valid values: pr, dev, qa, prod, watchdog. "
            "Default (empty) generates all five."
        ),
    ),
    platform: str = typer.Option(
        "github",
        "--platform",
        "-p",
        help=(
            "CI/CD platform. 'github' (default) writes "
            "`.github/workflows/*.yml`; 'azure-devops' writes "
            "`.azuredevops/pipelines/*.yml`."
        ),
    ),
    deploy_mode: str = typer.Option(
        "auto",
        "--deploy-mode",
        help=(
            "Deployment template mode. Default is 'auto': uses azd when azure.yaml exists "
            "or prompt-agent when agentops.yaml targets a Foundry prompt agent; "
            "'azd' forces azd provision/deploy templates, 'prompt-agent' "
            "creates/evaluates a Foundry prompt candidate, and 'placeholder' "
            "keeps stack-agnostic placeholders."
        ),
    ),
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Generate the AgentOps GitFlow CI/CD workflows.

    By default writes the five templates that map to a classic GitFlow
    setup with three deploy environments (dev, qa, production) plus a
    scheduled watchdog:

      - agentops-pr           (PR gate; PRs to develop, release/**, main)
      - agentops-deploy-dev   (push to develop  -> environment: dev)
      - agentops-deploy-qa    (push to release/** -> environment: qa)
      - agentops-deploy-prod  (push to main      -> environment: production)
      - agentops-watchdog      (scheduled Doctor + eval health check)

    Use --kinds to opt into a subset (e.g. --kinds pr,dev), and
    --platform to target either GitHub Actions or Azure DevOps Pipelines.
    The conceptual workflows are identical across platforms.
    """
    if _maybe_explain_leaf(("workflow", "generate"), explain):
        return

    from agentops.services.cicd import (
        ALL_KINDS,
        DEPLOY_MODES,
        PLATFORMS,
        generate_cicd_workflows,
    )

    log.debug(
        "cmd_workflow_generate called force=%s dir=%s kinds=%r platform=%s",
        force, directory, kinds, platform,
    )

    if platform not in PLATFORMS:
        typer.echo(
            f"{_cli_error('Error')}: unknown --platform value {platform!r}. "
            f"Valid: {', '.join(PLATFORMS)}.",
            err=True,
        )
        raise typer.Exit(code=1)
    if deploy_mode not in DEPLOY_MODES:
        typer.echo(
            f"{_cli_error('Error')}: unknown --deploy-mode value {deploy_mode!r}. "
            f"Valid: {', '.join(DEPLOY_MODES)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    selected: list[str] | None = None
    if kinds.strip():
        selected = [k.strip() for k in kinds.split(",") if k.strip()]
        invalid = [k for k in selected if k not in ALL_KINDS]
        if invalid:
            typer.echo(
                f"{_cli_error('Error')}: unknown --kinds value(s): "
                f"{', '.join(invalid)}. "
                f"Valid: {', '.join(ALL_KINDS)}.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        result = generate_cicd_workflows(
            directory=directory,
            force=force,
            kinds=selected,
            platform=platform,
            deploy_mode=deploy_mode,
        )
    except Exception as exc:
        typer.echo(
            f"{_cli_error('Error')}: failed to generate CI/CD workflows: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"{_cli_label('Platform')}: {result.platform}")
    deploy_kinds = [kind for kind in result.kinds if kind in {"dev", "qa", "prod"}]
    deploy_mode_note = result.deploy_mode
    if deploy_mode == "auto":
        deploy_mode_note = f"{deploy_mode_note} (auto default)"
    if not deploy_kinds:
        deploy_mode_note = f"{deploy_mode_note}; used only by deploy workflows"
    typer.echo(f"{_cli_label('Deploy mode')}: {_cli_value(deploy_mode_note)}")
    typer.echo(
        f"{_cli_label('Eval runner')}: "
        f"{_cli_value(_workflow_eval_runner_label(result.eval_runner))}"
    )
    for created in result.created_files:
        typer.echo(_cli_created(created))
    for overwritten in result.overwritten_files:
        typer.echo(_cli_overwritten(overwritten))
    for skipped in result.skipped_files:
        typer.echo(_cli_skipped(skipped, " (use --force to overwrite)"))

    if result.created_files or result.overwritten_files:
        typer.echo("")
        typer.echo(_cli_heading("Next"))
        if result.platform == "github":
            environments = _workflow_environment_names(result.kinds)
            typer.echo("  repo      publish this folder before CI can run")
            typer.echo("            If this is not a GitHub repo yet:")
            typer.echo(f"            {_cli_command('git init')}")
            typer.echo(f"            {_cli_command('git add .')}")
            typer.echo(
                "            "
                + _cli_command('git commit -m "Add AgentOps workflows"')
            )
            typer.echo(
                f"            {_cli_command('gh repo create <repo-name> --source . --private --push')}"
            )
            typer.echo("  Copilot   smoother path: use the AgentOps workflow skill")
            typer.echo(
                f"            {_cli_command('agentops skills install --platform copilot')}"
            )
            typer.echo("            In Copilot, run /skills and confirm agentops-workflow loaded.")
            typer.echo(
                "            Ask it to wire GitHub, Azure OIDC, variables, "
                "environments, and branch rules."
            )
            typer.echo(
                "  CI vars   AZURE_CLIENT_ID, AZURE_TENANT_ID, "
                "AZURE_SUBSCRIPTION_ID"
            )
            typer.echo(
                "            AZURE_AI_FOUNDRY_PROJECT_ENDPOINT, "
                "AZURE_OPENAI_DEPLOYMENT"
            )
            if environments:
                typer.echo(
                    "  envs      create GitHub environment"
                    f"{'' if len(environments) == 1 else 's'}: "
                    f"{', '.join(environments)}"
                )
                if "production" in environments:
                    typer.echo(
                        "            add required reviewers to production before "
                        "enabling prod deploys"
                    )
        else:
            environments = _workflow_environment_names(result.kinds)
            typer.echo("  repo      publish this folder before pipelines can run")
            typer.echo("  service   create service connection: agentops-azure")
            typer.echo("  vars      create variable group: agentops")
            if environments:
                typer.echo(
                    "  envs      create Azure DevOps environment"
                    f"{'' if len(environments) == 1 else 's'}: "
                    f"{', '.join(environments)}"
                )
                if "production" in environments:
                    typer.echo("            add approval checks to production")
        if result.deploy_mode == "azd":
            if deploy_kinds:
                typer.echo("  azd       commit azure.yaml, infra/, and azd hooks")
                typer.echo(
                    "            set AZURE_ENV_NAME/AZURE_LOCATION if env names differ"
                )
        elif result.deploy_mode == "prompt-agent":
            if deploy_kinds:
                typer.echo("  prompt    commit a prompt/instructions file")
                typer.echo(
                    "            set prompt_file or AGENTOPS_AGENT_PROMPT_FILE in CI"
                )
                typer.echo(
                    "            deploy evaluates that exact candidate version first"
                )
            else:
                typer.echo("  deploy    not needed yet; PR/watchdog can run first")
                typer.echo(
                    "            add deploy workflows when you are ready to deploy"
                )
        else:
            if deploy_kinds:
                typer.echo("  deploy    placeholder workflows need project-specific edits")
                typer.echo(
                    "            ask your coding agent to wire azd or prompt-agent deploy"
                )
        if "pr" in result.kinds:
            typer.echo("  gate      after the first run, require the AgentOps PR check")
        typer.echo("  guide     docs/ci-github-actions.md")
    elif result.skipped_files:
        typer.echo(_cli_warn("No files written. Use --force to overwrite existing workflows."))


# ---------------------------------------------------------------------------
# agentops skills install
# ---------------------------------------------------------------------------


@skills_app.command("install")
def cmd_skills_install(
    platform: Annotated[
        list[str] | None,
        typer.Option(
            "--platform",
            "-p",
            help="Target platform(s): copilot, claude.",
        ),
    ] = None,
    from_github: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Install a community skill from GitHub. "
                "Format: org/repo or github:org/repo[@ref]. "
                "Example: --from donlee/pptx-designer"
            ),
        ),
    ] = None,
    force: bool = typer.Option(
        False,
        "--force",
        help="Deprecated - skills are always overwritten with the latest version.",
    ),
    prompt: bool = typer.Option(
        False,
        "--prompt",
        help="Ask before installing skills when no coding agent platform is detected.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        help="Target repository root directory.",
    ),
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Install AgentOps coding agent skills into the target project.

    Use --from to install a community skill from GitHub:

        agentops skills install --from donlee/pptx-designer

        agentops skills install --from github:org/repo@v1.0
    """
    if _maybe_explain_leaf(("skills", "install"), explain):
        return

    log.debug(
        "cmd_skills_install called platform=%s from=%s force=%s prompt=%s dir=%s",
        platform,
        from_github,
        force,
        prompt,
        directory,
    )
    resolved_platforms = _resolve_platforms(
        directory=directory, explicit=platform, prompt=prompt
    )
    if not resolved_platforms:
        typer.echo(_cli_warn("No platforms selected. Skipping skill installation."))
        return

    if from_github:
        # GitHub-based skill installation
        from agentops.services.skills import install_github_skill

        typer.echo(f"{_cli_label('Installing skill from GitHub')}: {from_github}")
        try:
            result = install_github_skill(
                source=from_github,
                directory=directory,
                platforms=resolved_platforms,
                force=True,
            )
        except ValueError as exc:
            typer.echo(f"{_cli_error('Error')}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            typer.echo(f"{_cli_error('Error')}: failed to install skill: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        _print_skills_result(result)
        return

    # Bundled skills installation
    from agentops.services.skills import install_skills

    try:
        result = install_skills(
            directory=directory, platforms=resolved_platforms, force=True
        )
    except Exception as exc:
        typer.echo(f"{_cli_error('Error')}: failed to install skills: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _print_skills_result(result)

    from agentops.services.skills import register_skills

    try:
        reg_result = register_skills(directory=directory, platforms=resolved_platforms)
    except Exception as exc:
        typer.echo(f"{_cli_warn('Warning')}: failed to register skills: {exc}", err=True)
    else:
        _print_registration_result(reg_result)


# ---------------------------------------------------------------------------
# agentops mcp serve
# ---------------------------------------------------------------------------


@mcp_app.command("serve")
def cmd_mcp_serve(
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Start the AgentOps MCP server on stdio.

    Exposes the AgentOps workflow (init, eval run, report show, results
    summary, dataset add, list runs, workflow init) as MCP tools so that
    MCP-aware coding agents can drive AgentOps directly.

    Requires the optional ``mcp`` extra:

        pip install agentops-toolkit[mcp]
    """
    if _maybe_explain_leaf(("mcp", "serve"), explain):
        return

    try:
        from agentops.mcp.server import serve_stdio
    except RuntimeError as exc:
        typer.echo(f"{_cli_error('Error')}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        serve_stdio()
    except RuntimeError as exc:
        typer.echo(f"{_cli_error('Error')}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# `agentops agent` commands
# ---------------------------------------------------------------------------


def _resolve_agent_config_path(workspace: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    candidate = workspace / ".agentops" / "agent.yaml"
    return candidate if candidate.exists() else None


def _port_in_use(host: str, port: int) -> bool:
    """Return True when ``(host, port)`` is already accepting connections."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _existing_agentops_cockpit(host: str, port: int) -> bool:
    """Heuristic: hit ``/healthz`` and verify it looks like our cockpit.

    The cockpit exposes ``GET /healthz`` returning ``{"status": "ok"}``.
    Any non-200 / non-matching body means a different process owns the
    port and we should not assume it's safe to point the browser at it.
    """
    import json
    from urllib import error, request

    try:
        req = request.Request(
            f"http://{host}:{port}/healthz",
            headers={"Accept": "application/json"},
        )
        with request.urlopen(req, timeout=1.0) as resp:  # noqa: S310
            if resp.status != 200:
                return False
            body = resp.read(256)
        parsed = json.loads(body)
        return isinstance(parsed, dict) and parsed.get("status") == "ok"
    except (error.URLError, ValueError, OSError, TimeoutError):
        return False


def _summarize_cockpit_connection(workspace: Path) -> list[tuple[str, str]]:
    """Return label/value pairs describing where the cockpit is pointed.

    Surfaces the resolved Foundry project endpoint and agent identifier so
    the operator knows which project they are analyzing before the browser
    opens.

    Best-effort: any read failure (missing file, malformed YAML, no env
    var) yields a "not configured" line with a single concrete next step
    instead of raising.
    """

    project_endpoint: str | None = None
    agent_id: str | None = None

    def _read_yaml(path: Path) -> dict:
        try:
            import yaml  # type: ignore[import-not-found]

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    # 1) Flat schema: agentops.yaml at the project root (1.0 layout) or
    # the legacy location under .agentops/.
    candidates = [workspace / "agentops.yaml", workspace / ".agentops" / "agentops.yaml"]
    for path in candidates:
        if not path.exists():
            continue
        data = _read_yaml(path)
        if not data:
            continue
        if not agent_id:
            value = data.get("agent")
            if isinstance(value, str) and value.strip():
                agent_id = value.strip()
        if not project_endpoint:
            value = data.get("project_endpoint")
            if isinstance(value, str) and value.strip():
                project_endpoint = value.strip()
        if agent_id and project_endpoint:
            break

    # 2) Layered schema: .agentops/run.yaml or run.yaml at the root.
    if not agent_id or not project_endpoint:
        for path in (workspace / ".agentops" / "run.yaml", workspace / "run.yaml"):
            if not path.exists():
                continue
            data = _read_yaml(path)
            target = data.get("target") if isinstance(data, dict) else None
            endpoint = (target or {}).get("endpoint") if isinstance(target, dict) else None
            if isinstance(endpoint, dict):
                if not agent_id:
                    value = endpoint.get("agent_id")
                    if isinstance(value, str) and value.strip():
                        agent_id = value.strip()
                if not project_endpoint:
                    value = endpoint.get("project_endpoint")
                    if isinstance(value, str) and value.strip():
                        project_endpoint = value.strip()
            if agent_id and project_endpoint:
                break

    # 3) Env-var fallback for the project endpoint.
    if not project_endpoint:
        env_value = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        if env_value and env_value.strip():
            project_endpoint = env_value.strip()

    rows: list[tuple[str, str]] = []

    if project_endpoint:
        rows.append(("Foundry project", project_endpoint))
    else:
        rows.append(
            (
                "Foundry project",
                "not configured — set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT or "
                "`project_endpoint` in agentops.yaml",
            )
        )

    if agent_id:
        rows.append(("agent", agent_id))
    else:
        rows.append(("agent", "not configured — set `agent` in agentops.yaml"))

    return rows


@doctor_app.callback(invoke_without_command=True)
def cmd_doctor(
    ctx: typer.Context,
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            "-w",
            help="Project root containing `.agentops/`.",
        ),
    ] = Path("."),
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to `agent.yaml` (default: `.agentops/agent.yaml`).",
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Where to write the Markdown report.",
        ),
    ] = Path(".agentops/agent/report.md"),
    lookback_days: Annotated[
        int | None,
        typer.Option(
            "--lookback-days",
            help="Override the lookback window for production telemetry.",
        ),
    ] = None,
    severity_fail: Annotated[
        str,
        typer.Option(
            "--severity-fail",
            help="Exit 2 when a finding at or above this severity is produced.",
        ),
    ] = "critical",
    categories: Annotated[
        str | None,
        typer.Option(
            "--categories",
            help=(
                "Comma-separated list of categories to include "
                "(quality, performance, reliability, "
                "operational_excellence, security, responsible_ai). "
                "Default: include all."
            ),
        ),
    ] = None,
    exclude_rules: Annotated[
        str | None,
        typer.Option(
            "--exclude-rules",
            help=(
                "Comma-separated list of posture rule ids to skip "
                "(for example `waf.security.diagnostic_settings`)."
            ),
        ),
    ] = None,
    no_preflight: Annotated[
        bool,
        typer.Option(
            "--no-preflight",
            help="Skip the pre-flight connectivity checks.",
        ),
    ] = False,
    strict_preflight: Annotated[
        bool,
        typer.Option(
            "--strict-preflight",
            help="Exit non-zero if any pre-flight check fails or warns.",
        ),
    ] = False,
    evidence_pack: Annotated[
        bool,
        typer.Option(
            "--evidence-pack",
            help="Write `.agentops/release/latest/evidence.json` and `evidence.md`.",
        ),
    ] = False,
    evidence_out: Annotated[
        Path | None,
        typer.Option(
            "--evidence-out",
            help="Directory for release evidence artifacts (default: `.agentops/release/latest`).",
        ),
    ] = None,
) -> None:
    """Diagnose local AgentOps, Foundry, Azure telemetry, and WAF-AI gaps."""
    # When a subcommand was provided (e.g. `doctor explain`), defer to it
    # and don't run the analyzer. Group-level options on the callback
    # are still parsed (Typer requires this) but are ignored here -
    # subcommands declare their own option surface.
    if ctx.invoked_subcommand is not None:
        return

    _run_doctor_analyze(
        workspace=workspace,
        config_path=config_path,
        out=out,
        lookback_days=lookback_days,
        severity_fail=severity_fail,
        categories=categories,
        exclude_rules=exclude_rules,
        no_preflight=no_preflight,
        strict_preflight=strict_preflight,
        evidence_pack=evidence_pack,
        evidence_out=evidence_out,
    )


def _run_doctor_analyze(
    *,
    workspace: Path,
    config_path: Path | None,
    out: Path,
    lookback_days: int | None,
    severity_fail: str,
    categories: str | None,
    exclude_rules: str | None,
    no_preflight: bool,
    strict_preflight: bool,
    evidence_pack: bool,
    evidence_out: Path | None,
) -> None:
    """Run the doctor analyzer pipeline.

    Extracted from the Typer callback so behavior is identical whether
    the user invokes ``agentops doctor`` directly or any future entry
    point that wants to reuse the same orchestration.
    """
    from agentops.agent.analyzer import analyze
    from agentops.agent.config import load_agent_config
    from agentops.agent.findings import Severity
    from agentops.agent.history import append_analysis, build_record
    from agentops.agent.report import render_report
    from agentops.services.preflight import (
        format_report,
        run_preflight,
    )
    from agentops.utils import telemetry
    import time as _time

    workspace = workspace.resolve()
    resolved_config = _resolve_agent_config_path(workspace, config_path)

    if evidence_pack and categories:
        typer.echo(
            f"{_cli_error('Error')}: --evidence-pack requires a full doctor run; omit --categories.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not no_preflight:
        typer.echo(
            "doctor: running pre-flight checks (workspace, Azure auth, Foundry discovery)",
            err=True,
        )
        report = run_preflight(workspace, scope="doctor")
        typer.echo(format_report(report), err=True)
        if report.has_failures or (strict_preflight and report.has_warnings):
            typer.echo(
                f"{_cli_error('Pre-flight failed')}. Resolve the issues above or re-run "
                "with `--no-preflight` to bypass.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        config = load_agent_config(resolved_config)
    except Exception as exc:
        typer.echo(f"{_cli_error('Error loading agent config')}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if lookback_days is not None:
        config = config.model_copy(update={"lookback_days": lookback_days})

    try:
        severity_floor = Severity(severity_fail.lower())
    except ValueError as exc:
        typer.echo(
            f"{_cli_error('Error')}: invalid --severity-fail '{severity_fail}'. "
            "Use one of: info, warning, critical.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    telemetry.init_tracing()
    started_perf = _time.perf_counter()
    try:
        with telemetry.agent_analyze_span(
            workspace=str(workspace),
            lookback_days=config.lookback_days,
        ) as analyze_span:
            try:
                result = analyze(
                    workspace,
                    config,
                    categories=(
                        [c for c in categories.split(",") if c.strip()]
                        if categories
                        else None
                    ),
                    exclude_rules=(
                        [r for r in exclude_rules.split(",") if r.strip()]
                        if exclude_rules
                        else None
                    ),
                    progress=lambda msg: typer.echo(msg, err=True),
                )
            except Exception as exc:  # pragma: no cover
                typer.echo(f"{_cli_error('Error running analyzer')}: {exc}", err=True)
                raise typer.Exit(code=1) from exc

            duration_seconds = _time.perf_counter() - started_perf

            # Persist the analysis history (always - works without Azure).
            sources_enabled = _sources_enabled(config)
            record = build_record(
                result.findings,
                sources_enabled=sources_enabled,
                lookback_days=config.lookback_days,
                duration_seconds=duration_seconds,
            )
            try:
                history_file = append_analysis(workspace, record)
            except OSError as exc:  # pragma: no cover - best effort
                history_file = None
                log.debug("could not append agent history: %s", exc)

            telemetry.set_agent_analyze_result(
                analyze_span,
                findings_total=record.findings_total,
                by_severity=record.findings_by_severity,
                by_category=record.findings_by_category,
                max_severity=record.max_severity,
                sources_enabled=sources_enabled,
            )
            for finding in result.findings:
                telemetry.record_agent_finding_span(finding)
    finally:
        telemetry.shutdown()

    out_path = out if out.is_absolute() else workspace / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(result), encoding="utf-8")

    typer.echo(f"{_cli_label('Wrote')}: {_cli_path(out_path)}")
    if evidence_pack:
        from agentops.services.evidence_pack import write_release_evidence

        try:
            evidence_result = write_release_evidence(
                workspace=workspace,
                analysis=result,
                out_dir=evidence_out,
            )
        except Exception as exc:
            typer.echo(
                f"{_cli_error('Error writing evidence pack')}: {exc}",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        evidence = evidence_result.evidence
        status_tone = (
            "green"
            if evidence.status == "ready"
            else "yellow"
            if evidence.status == "ready_with_warnings"
            else "red"
        )
        typer.echo(
            f"{_cli_label('Release readiness')}: {style(evidence.status, 'bold', status_tone)}"
        )
        typer.echo(
            f"{_cli_label('Evidence pack')}: {_cli_path(evidence_result.json_path)}"
        )
        typer.echo(
            f"{_cli_label('Evidence report')}: {_cli_path(evidence_result.markdown_path)}"
        )
    if history_file is not None:
        typer.echo(f"{_cli_label('Appended history')}: {_cli_path(history_file)}")
    typer.echo(f"{_cli_label('Findings')}: {len(result.findings)}")
    if result.max_severity is not None:
        severity = result.max_severity.value
        tone = "red" if severity == "critical" else "yellow" if severity == "warning" else "green"
        typer.echo(f"{_cli_label('Max severity')}: {style(severity, 'bold', tone)}")

    if result.max_severity is not None and result.max_severity >= severity_floor:
        raise typer.Exit(code=2)


@doctor_app.command("explain")
def cmd_doctor_explain(
    no_pager: Annotated[
        bool,
        typer.Option(
            "--no-pager",
            help="Print directly instead of opening the pager.",
        ),
    ] = False,
    format_: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output format: text, markdown, or html.",
        ),
    ] = "text",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Write the manual to a file instead of only printing it.",
        ),
    ] = None,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open",
            help="Open a browser-friendly HTML copy for reading or printing.",
        ),
    ] = False,
) -> None:
    """Open a paged manual explaining what Doctor does and how it works."""
    _emit_doctor_explain(
        no_pager=no_pager,
        format_=format_,
        out=out,
        open_browser=open_browser,
    )


def _emit_doctor_explain(
    *,
    no_pager: bool,
    format_: str,
    out: Path | None,
    open_browser: bool,
) -> None:
    import click

    from agentops.agent.checks.catalog import (
        CATEGORY_DESCRIPTIONS,
        CATEGORY_ORDER,
        SOURCE_DESCRIPTIONS,
        SOURCE_LABELS,
        by_category,
        all_checks,
        reference_url_for,
    )

    format_ = format_.lower()
    if format_ not in {"text", "markdown", "html"}:
        typer.echo(
            f"{_cli_error('Invalid --format')}. Use one of: text, markdown, html.",
            err=True,
        )
        raise typer.Exit(code=1)

    text = _build_doctor_explain_text(
        category_descriptions=CATEGORY_DESCRIPTIONS,
        category_order=CATEGORY_ORDER,
        source_labels=SOURCE_LABELS,
        source_descriptions=SOURCE_DESCRIPTIONS,
        grouped_checks=by_category(all_checks()),
        reference_url_for=reference_url_for,
    )
    markdown = _build_doctor_explain_markdown(
        category_descriptions=CATEGORY_DESCRIPTIONS,
        category_order=CATEGORY_ORDER,
        source_labels=SOURCE_LABELS,
        source_descriptions=SOURCE_DESCRIPTIONS,
        grouped_checks=by_category(all_checks()),
        reference_url_for=reference_url_for,
    )
    html = _build_explain_html(markdown, title="AgentOps Doctor manual")

    output = {"text": text, "markdown": markdown, "html": html}[format_]
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
        typer.echo(f"{_cli_label('Wrote')}: {_cli_path(out)}")

    if open_browser:
        browser_path = out if out is not None and format_ == "html" else None
        if browser_path is None:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".html",
                prefix="agentops-doctor-",
                delete=False,
            ) as temp:
                temp.write(html)
                browser_path = Path(temp.name)
            typer.echo(f"{_cli_label('Opened browser copy')}: {_cli_path(browser_path)}")
        webbrowser.open(browser_path.resolve().as_uri())

    if out is not None or open_browser:
        return

    if format_ == "text" and not _terminal_unicode_enabled():
        output = _downgrade_to_ascii(output)

    if format_ != "text":
        if no_pager:
            typer.echo(output, color=True)
            return
        click.echo_via_pager(output, color=True)
        return

    if no_pager:
        _emit_manual_to_terminal(output)
        return
    if _useful_pager_available():
        click.echo_via_pager(output, color=True)
        return
    _emit_manual_with_internal_pager(output)


def _build_doctor_explain_text(
    *,
    category_descriptions: dict,
    category_order: tuple,
    source_labels: dict,
    source_descriptions: dict,
    grouped_checks: dict,
    reference_url_for,
) -> str:
    """Build the Linux-style manual shown by `agentops doctor explain`."""
    source_order = [
        "workspace",
        "spec_workspace",
        "results_history",
        "foundry_control",
        "azure_monitor",
        "azure_resources",
        "judge_model",
    ]
    lines: list[str] = _manual_banner(
        "AgentOps Doctor",
        "Diagnose AgentOps workspaces, Foundry projects, Azure telemetry, and WAF-AI gaps.",
    )

    def section(title: str) -> None:
        _manual_section(lines, title)

    section("NAME")
    _emit_name_line(
        lines,
        "agentops doctor",
        "Diagnose AgentOps workspaces, Foundry projects, Azure telemetry, and WAF-AI gaps.",
    )

    section("SYNOPSIS")
    lines.extend(
        [
            f"  {style('$', 'dim')} {style('agentops doctor [OPTIONS]', 'bold')}",
            f"  {style('$', 'dim')} {style('agentops doctor explain [--no-pager]', 'bold')}",
        ]
    )

    section("DESCRIPTION")
    lines.extend(
        _manual_paragraphs(
            "Doctor is the local readiness analyzer in AgentOps. It "
            "collects the sources you've configured — workspace files, "
            "Foundry control plane, Azure telemetry, Azure resources — "
            "runs deterministic and optional LLM-judged checks, groups "
            "findings by Microsoft AI Well-Architected Framework pillars, "
            "writes a Markdown report, and returns CI-friendly exit codes "
            "so quality gates fail fast.",
            "It surfaces what's missing across the project, the CI, and "
            "the Foundry wiring: workspace and config drift, missing CI "
            "gates, telemetry wiring, RBAC posture, and readiness for the "
            "practices described in the Microsoft AI Well-Architected "
            "Framework — with concrete next actions for each finding.",
            "Use `agentops doctor --help` for the terse syntax. Use "
            "`agentops doctor explain` for this longer manual-style overview.",
        )
    )

    section("DATA SOURCES")
    lines.extend(
        _manual_paragraphs(
            "Doctor is source-driven. Local sources work without Azure access. "
            "The main Azure sources are enabled by default and fail-open: if "
            "Doctor cannot authenticate, infer the deployed environment, or "
            "read a resource, it records a source diagnostic with the reason "
            "and the next configuration step instead of crashing the run."
        )
    )
    for source in source_order:
        label = source_labels.get(source, source)
        description = source_descriptions.get(source, "")
        lines.append("")
        lines.append(f"  {source} ({label})")
        lines.extend(_manual_paragraphs(description, indent="    "))

    section("AZD AND DEPLOYED ENVIRONMENTS")
    lines.extend(
        _manual_paragraphs(
            "For ARM posture checks, Doctor first looks for the active AZD "
            "environment under `.azure/<env>/.env`. It uses `AZURE_ENV_NAME`, "
            "then `.azure/config.json` `defaultEnvironment`, then the only "
            "environment folder when there is exactly one. From that file it "
            "uses `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and common "
            "AI account variables when present.",
            "If AZD metadata is not enough, Doctor uses the Foundry project "
            "endpoint to identify the backing Azure AI / Cognitive Services "
            "account. It scans the subscription for matching account endpoints "
            "and only proceeds automatically when the match is unambiguous. "
            "Explicit `sources.azure_resources` values in `.agentops/agent.yaml` "
            "still work and take precedence.",
            "For production telemetry, configure `sources.azure_monitor` with "
            "`app_insights_resource_id` or `log_analytics_workspace_id`. For "
            "Foundry control-plane checks, configure "
            "`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` or "
            "`sources.foundry_control.project_endpoint`.",
        )
    )

    section("HOW IT WORKS")
    lines.extend(
        [
            *_manual_item_lines("1. ", "Resolve `.agentops/agent.yaml`."),
            *_manual_item_lines("2. ", "Run pre-flight checks unless `--no-preflight` is used."),
            *_manual_item_lines(
                "3. ",
                "Collect enabled sources: local files, eval history, Foundry, Azure Monitor, ARM resources.",
            ),
            *_manual_item_lines("4. ", "Run Doctor checks and optional LLM-judged rules."),
            *_manual_item_lines("5. ", "Sort findings by severity and WAF-AI pillar."),
            *_manual_item_lines("6. ", "Write `.agentops/agent/report.md` (or `--out <path>`)."),
            *_manual_item_lines("7. ", "Append analysis history for cockpit / trend views."),
            *_manual_item_lines("8. ", "Exit with 0, 1, or 2."),
        ]
    )

    section("CHECK CATEGORIES")
    lines.extend(
        [
            "  quality                 Eval metric regressions.",
            "  performance             Eval and production latency.",
            "  reliability             Error rates, failed runs, rate limits, missing telemetry.",
            "  operational_excellence  CI hygiene, config drift, stale tasks, spec conformance.",
            "  security                Azure AI account auth, identity, and diagnostics posture.",
            "  responsible_ai          Content safety, prompt guardrails, dataset risk, continuous eval.",
        ]
    )

    section("FINDING QUALITY BAR")
    lines.extend(
        _manual_paragraphs(
            "Doctor findings are intentionally high-signal. The default catalog "
            "focuses on quality regressions, stale or flaky evaluations, "
            "production latency/errors, runtime safety signals, security posture, "
            "CI gates, and spec/config drift that changes what is actually "
            "evaluated.",
            "Process-only or backend-assumption checks stay out of the default "
            "finding list. For example, a reachable Foundry project with zero "
            "agents is source context, not a warning, because the agent may run "
            "on HTTP, Container Apps, AKS, or another runtime. Tiny specs, "
            "missing changelogs, and Copilot-instructions housekeeping are also "
            "not emitted as default findings.",
        )
    )

    section("CHECK CATALOG")
    lines.extend(
        _manual_paragraphs(
            "This is the catalog of findings Doctor can emit. Each entry "
            "shows the finding id, whether it is LLM-judged or source-based, "
            "the short purpose, required sources, and a public reference URL.",
            "`[LLM Judge]` means the check calls the configured judge model "
            "(opt-in and may use tokens). `[Source-based]` means no judge "
            "model is called; Doctor evaluates local files, eval history, "
            "Foundry, App Insights, or Azure resource metadata.",
        )
    )
    for category in category_order:
        checks = grouped_checks.get(category, [])
        if not checks:
            continue
        lines.append("")
        heading = category.value.replace("_", " ").title()
        description = category_descriptions.get(category)
        lines.append(f"  {heading}")
        if description:
            lines.extend(_manual_paragraphs(description, indent="    "))
        for spec in checks:
            requires = ", ".join(
                source_labels.get(source, source) for source in spec.requires
            )
            severities = ", ".join(severity.value for severity in spec.severities)
            if spec.is_llm_judged:
                mode_badge = style("[LLM Judge]", "bold", "magenta")
                mode_text = "LLM Judge (opt-in; uses configured judge model)"
            else:
                mode_badge = style("[Source-based]", "dim")
                mode_text = "Source-based (no judge model call)"
            lines.append("")
            lines.append(f"    - {mode_badge} {spec.id}")
            lines.extend(_manual_paragraphs(spec.title, indent="        "))
            lines.extend(_manual_paragraphs(spec.summary, indent="        "))
            lines.append(f"        mode: {mode_text}")
            lines.append(f"        severity: {severities}")
            if requires:
                lines.extend(_manual_paragraphs(f"requires: {requires}", indent="        "))
            docs_url = reference_url_for(spec)
            if docs_url:
                lines.append(f"        learn more: {docs_url}")

    section("EXIT CODES")
    lines.extend(
        [
            "  0  Analyzer ran successfully and no finding met `--severity-fail`.",
            "  1  Runtime or configuration error.",
            "  2  Analyzer ran successfully, but at least one finding met `--severity-fail`.",
        ]
    )

    section("EXAMPLES")
    lines.extend(
        [
            f"  {style('$', 'dim')} {style('agentops doctor', 'bold')}",
            f"  {style('$', 'dim')} {style('agentops doctor --categories security,responsible_ai', 'bold')}",
            f"  {style('$', 'dim')} {style('agentops doctor --severity-fail warning', 'bold')}",
            f"  {style('$', 'dim')} {style('agentops doctor explain --no-pager', 'bold')}",
        ]
    )

    section("SEE ALSO")
    lines.extend(
        [
            "  docs/doctor-checks.md",
            "  docs/doctor-explained.md",
            "  https://learn.microsoft.com/azure/well-architected/ai/",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_doctor_explain_markdown(
    *,
    category_descriptions: dict,
    category_order: tuple,
    source_labels: dict,
    source_descriptions: dict,
    grouped_checks: dict,
    reference_url_for,
) -> str:
    """Build a Markdown manual for browser/print workflows."""
    source_order = [
        "workspace",
        "spec_workspace",
        "results_history",
        "foundry_control",
        "azure_monitor",
        "azure_resources",
        "judge_model",
    ]
    lines: list[str] = [
        "# AgentOps Doctor manual",
        "",
        "## NAME",
        "",
        "`agentops doctor` - diagnose AgentOps, Foundry, Azure telemetry, and WAF-AI gaps.",
        "",
        "## SYNOPSIS",
        "",
        "```text",
        "agentops doctor [OPTIONS]",
        "agentops doctor explain [--no-pager] [--format text|markdown|html] [--out PATH] [--open]",
        "```",
        "",
        "## DESCRIPTION",
        "",
        "Doctor is the local diagnostic analyzer for AgentOps workspaces. It collects configured sources, runs deterministic and optional LLM-judged checks, groups findings by Microsoft WAF-AI pillars, writes a Markdown report, and returns CI-friendly exit codes.",
        "",
        "Use `agentops doctor --help` for terse syntax and options. Use `agentops doctor explain` for the terminal manual, `--format markdown --out doctor.md` for a document, or `--open` for a browser-friendly copy you can print.",
        "",
        "## DATA SOURCES",
        "",
        "Doctor is source-driven. Local sources work without Azure access. The main Azure sources are enabled by default and fail open: if Doctor cannot authenticate, infer the deployed environment, or read a resource, it records a source diagnostic with the reason and next setup step instead of crashing the run.",
        "",
        "| Source | Label | What it reads |",
        "|---|---|---|",
    ]
    for source in source_order:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{source}`",
                    source_labels.get(source, source),
                    source_descriptions.get(source, "").replace("|", "\\|"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## AZD AND DEPLOYED ENVIRONMENTS",
            "",
            "For ARM posture checks, Doctor first looks for the active AZD environment under `.azure/<env>/.env`. It uses `AZURE_ENV_NAME`, then `.azure/config.json` `defaultEnvironment`, then the only environment folder when there is exactly one. From that file it uses `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and common AI account variables when present.",
            "",
            "If AZD metadata is not enough, Doctor uses the Foundry project endpoint to identify the backing Azure AI / Cognitive Services account. It scans the subscription for matching account endpoints and only proceeds automatically when the match is unambiguous. Explicit `sources.azure_resources` values in `.agentops/agent.yaml` still work and take precedence.",
            "",
            "For production telemetry, configure `sources.azure_monitor` with `app_insights_resource_id` or `log_analytics_workspace_id`. For Foundry control-plane checks, configure `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` or `sources.foundry_control.project_endpoint`.",
            "",
            "## HOW IT WORKS",
            "",
            "1. Resolve `.agentops/agent.yaml`.",
            "2. Run pre-flight checks unless `--no-preflight` is used.",
            "3. Collect enabled sources: local files, eval history, Foundry, Azure Monitor, ARM resources.",
            "4. Run Doctor checks and optional LLM-judged rules.",
            "5. Sort findings by severity and WAF-AI pillar.",
            "6. Write `.agentops/agent/report.md` or `--out <path>`.",
            "7. Append analysis history for cockpit / trend views.",
            "8. Exit with 0, 1, or 2.",
            "",
            "## CHECK CATEGORIES",
            "",
            "| Category | Purpose |",
            "|---|---|",
            "| `quality` | Eval metric regressions. |",
            "| `performance` | Eval and production latency. |",
            "| `reliability` | Error rates, failed runs, rate limits, missing telemetry. |",
            "| `operational_excellence` | CI hygiene, config drift, stale tasks, spec conformance. |",
            "| `security` | Azure AI account auth, identity, and diagnostics posture. |",
            "| `responsible_ai` | Content safety, prompt guardrails, dataset risk, continuous eval. |",
            "",
            "## FINDING QUALITY BAR",
            "",
            "Doctor findings are intentionally high-signal. The default catalog focuses on quality regressions, stale or flaky evaluations, production latency/errors, runtime safety signals, security posture, CI gates, and spec/config drift that changes what is actually evaluated.",
            "",
            "Process-only or backend-assumption checks stay out of the default finding list. For example, a reachable Foundry project with zero agents is source context, not a warning, because the agent may run on HTTP, Container Apps, AKS, or another runtime. Tiny specs, missing changelogs, and Copilot-instructions housekeeping are also not emitted as default findings.",
            "",
            "## CHECK CATALOG",
            "",
            "This is the catalog of findings Doctor can emit. Each entry shows the finding id, whether it is LLM-judged or source-based, the short purpose, required sources, and a public reference URL.",
            "",
            "**Legend:** `LLM Judge` calls the configured judge model (opt-in and may use tokens). `Source-based` does not call a judge model; it evaluates local files, eval history, Foundry, App Insights, or Azure resource metadata.",
        ]
    )

    for category in category_order:
        checks = grouped_checks.get(category, [])
        if not checks:
            continue
        heading = category.value.replace("_", " ").title()
        lines.extend(["", f"### {heading}", ""])
        description = category_descriptions.get(category)
        if description:
            lines.extend([description, ""])
        for spec in checks:
            requires = ", ".join(
                source_labels.get(source, source) for source in spec.requires
            )
            severities = ", ".join(severity.value for severity in spec.severities)
            docs_url = reference_url_for(spec)
            mode = (
                "LLM Judge (opt-in; uses configured judge model)"
                if spec.is_llm_judged
                else "Source-based (no judge model call)"
            )
            lines.extend(
                [
                    f"#### {'[LLM Judge] ' if spec.is_llm_judged else '[Source-based] '}`{spec.id}`",
                    "",
                    spec.title,
                    "",
                    spec.summary,
                    "",
                    f"**Mode:** {mode}",
                    "",
                    f"**Severity:** {severities}",
                ]
            )
            if requires:
                lines.append(f"**Requires:** {requires}")
            if docs_url:
                lines.append(f"**Learn more:** [{docs_url}]({docs_url})")
            lines.append("")

    lines.extend(
        [
            "## EXIT CODES",
            "",
            "| Code | Meaning |",
            "|---|---|",
            "| `0` | Analyzer ran successfully and no finding met `--severity-fail`. |",
            "| `1` | Runtime or configuration error. |",
            "| `2` | Analyzer ran successfully, but at least one finding met `--severity-fail`. |",
            "",
            "## EXAMPLES",
            "",
            "```text",
            "agentops doctor",
            "agentops doctor --categories security,responsible_ai",
            "agentops doctor --severity-fail warning",
            "agentops doctor explain --no-pager",
            "agentops doctor explain --format markdown --out doctor.md",
            "agentops doctor explain --open",
            "```",
            "",
            "## SEE ALSO",
            "",
            "- `docs/doctor-checks.md`",
            "- `docs/doctor-explained.md`",
            "- https://learn.microsoft.com/azure/well-architected/ai/",
            "",
        ]
    )
    return "\n".join(lines)


def _build_explain_html(markdown: str, *, title: str) -> str:
    """Render the generated Markdown subset as self-contained printable HTML."""
    lines = markdown.splitlines()
    hero_title = _extract_markdown_title(lines) or title
    hero_subtitle = _extract_first_paragraph(lines)
    body: list[str] = []
    in_ul = False
    in_ol = False
    in_table = False
    in_code = False
    in_card = False
    code_lines: list[str] = []
    table_header = True

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            body.append("</ul>")
            in_ul = False
        if in_ol:
            body.append("</ol>")
            in_ol = False

    def close_table() -> None:
        nonlocal in_table, table_header
        if in_table:
            body.append("</tbody></table>")
            in_table = False
            table_header = True

    def close_card() -> None:
        nonlocal in_card
        if in_card:
            body.append("</article>")
            in_card = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            close_lists()
            close_table()
            if in_code:
                body.append("<pre><code>" + html_escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            close_lists()
            close_table()
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            close_lists()
            if not in_table:
                body.append("<table>")
                in_table = True
                table_header = True
            if table_header:
                body.append(
                    "<thead><tr>"
                    + "".join(f"<th>{_inline_markdown(cell)}</th>" for cell in cells)
                    + "</tr></thead><tbody>"
                )
                table_header = False
            else:
                body.append(
                    "<tr>"
                    + "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in cells)
                    + "</tr>"
                )
            continue
        close_table()
        if stripped.startswith("#"):
            close_lists()
            level = min(len(stripped) - len(stripped.lstrip("#")), 4)
            heading_title = stripped[level:].strip()
            if level <= 3:
                close_card()
            elif level == 4:
                close_card()
                body.append('<article class="check-card">')
                in_card = True
            body.append(f"<h{level}>{_inline_markdown(heading_title)}</h{level}>")
            continue
        if stripped.startswith("- "):
            if in_ol:
                body.append("</ol>")
                in_ol = False
            if not in_ul:
                body.append("<ul>")
                in_ul = True
            body.append(f"<li>{_inline_markdown(stripped[2:])}</li>")
            continue
        if re.match(r"^\d+\. ", stripped):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            if not in_ol:
                body.append("<ol>")
                in_ol = True
            body.append(f"<li>{_inline_markdown(stripped.split('. ', 1)[1])}</li>")
            continue
        close_lists()
        body.append(f"<p>{_inline_markdown(stripped)}</p>")

    close_lists()
    close_table()
    close_card()
    if in_code:
        body.append("<pre><code>" + html_escape("\n".join(code_lines)) + "</code></pre>")

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>""" + html_escape(title) + """</title>
<style>
  :root { color-scheme: light; font-family: "Segoe UI", Arial, sans-serif; }
  body { margin: 0; background: #f6f8fa; color: #24292f; line-height: 1.55; }
  main { max-width: 1040px; margin: 32px auto; padding: 44px; background: #fff; border: 1px solid #d8dee4; border-radius: 16px; box-shadow: 0 14px 40px rgba(27, 31, 36, 0.045); }
  .hero { position: relative; overflow: hidden; margin: -12px -12px 34px; padding: 30px 34px 26px; color: #fff; border-radius: 18px; background: radial-gradient(circle at 8% 20%, rgba(87, 200, 255, 0.95), transparent 24%), radial-gradient(circle at 78% 22%, rgba(255, 43, 170, 0.82), transparent 23%), linear-gradient(120deg, #0a66c2 0%, #7c3aed 48%, #ff7a00 100%); box-shadow: 0 18px 45px rgba(9, 105, 218, 0.18); }
  .hero::after { content: ""; position: absolute; inset: auto -8% -48% 36%; height: 150px; background: rgba(255,255,255,0.16); filter: blur(22px); transform: rotate(-8deg); }
  .hero-kicker { position: relative; z-index: 1; display: inline-flex; align-items: center; gap: 0.45rem; margin-bottom: 0.45rem; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.11em; text-transform: uppercase; opacity: 0.9; }
  .hero-mark { display: inline-grid; place-items: center; width: 1.45rem; height: 1.45rem; border-radius: 0.45rem; background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.28); }
  .hero h1 { position: relative; z-index: 1; margin: 0; color: #fff; font-size: clamp(2rem, 4vw, 3.25rem); line-height: 1; letter-spacing: -0.055em; }
  .hero p { position: relative; z-index: 1; max-width: 760px; margin: 0.8rem 0 0; color: rgba(255,255,255,0.92); font-size: 1.03rem; }
  h1 { margin-top: 0; font-size: 2.1rem; letter-spacing: -0.03em; }
  h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 0.45rem; margin-top: 2.4rem; font-size: 1.45rem; letter-spacing: -0.02em; }
  h3 { margin: 2.1rem 0 0.85rem; padding: 0.35rem 0 0.4rem; border-bottom: 1px solid #eaeef2; color: #1f2328; font-size: 1.18rem; }
  h4 { margin: 0 0 0.85rem; font-size: 1rem; }
  h4 code { display: inline-flex; align-items: center; background: #eef6ff; color: #0550ae; border: 1px solid #b6d7ff; border-radius: 999px; padding: 0.22rem 0.58rem; font-size: 0.84rem; font-weight: 650; box-shadow: none; }
  code { background: #f6f8fa; padding: 0.12rem 0.3rem; border-radius: 4px; }
  pre { background: #f6f8fa; padding: 1rem; overflow-x: auto; border-radius: 8px; }
  .check-card { margin: 1rem 0 1.25rem; padding: 0.95rem 1.05rem 0.9rem; border: 1px solid #d8e3f0; border-left: 3px solid #8cbeff; border-radius: 12px; background: linear-gradient(180deg, #ffffff, #fbfdff); box-shadow: 0 4px 14px rgba(31, 35, 40, 0.035); }
  .check-card p { margin: 0.62rem 0; }
  .check-card p:first-of-type { font-size: 1.03rem; font-weight: 600; color: #1f2328; }
  .check-card strong { color: #1f2328; }
  .check-card a { overflow-wrap: anywhere; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { border: 1px solid #d0d7de; padding: 0.55rem 0.7rem; vertical-align: top; }
  th { background: #f6f8fa; text-align: left; }
  @media print {
    body { background: #fff; }
    main { margin: 0; padding: 0; border: 0; max-width: none; }
    a { color: #24292f; }
  }
</style>
</head>
<body>
<main>
<section class="hero">
  <div class="hero-kicker"><span class="hero-mark">A</span> AgentOps explain</div>
  <h1>""" + html_escape(hero_title) + """</h1>
  <p>""" + html_escape(hero_subtitle) + """</p>
</section>
""" + "\n".join(body) + """
</main>
</body>
</html>
"""


def _inline_markdown(text: str) -> str:
    text = html_escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _extract_markdown_title(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _extract_first_paragraph(lines: list[str]) -> str:
    after_h1 = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            after_h1 = True
            continue
        if not after_h1:
            continue
        if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("|"):
            continue
        if stripped.startswith("- ") or re.match(r"^\d+\. ", stripped):
            continue
        return re.sub(r"[`*_]", "", stripped)
    return "Detailed local command documentation, architecture, workflow, and examples."


_ANSI_RESET = "\x1b[0m"

# Synthwave-inspired horizontal palette used by the AGENTOPS block banner.
# Sweeps teal -> blue -> violet -> hot pink -> sunset orange to give the
# 80s neon feel while keeping every stop high-contrast on a dark terminal.
_AGENTOPS_GRADIENT = (
    (0, 245, 212),
    (18, 183, 255),
    (168, 85, 247),
    (236, 72, 153),
    (255, 159, 67),
)

# Fixed-width (8 cols x 5 rows) block glyphs for "AGENTOPS".
# Concatenated with a single-space separator the full banner is 71 cols wide,
# which centers comfortably inside DOCTOR_EXPLAIN_WRAP_WIDTH (88).
_AGENTOPS_GLYPHS: dict[str, tuple[str, ...]] = {
    "A": (
        " █████  ",
        "██   ██ ",
        "███████ ",
        "██   ██ ",
        "██   ██ ",
    ),
    "G": (
        " ██████ ",
        "██      ",
        "██  ███ ",
        "██   ██ ",
        " ██████ ",
    ),
    "E": (
        "███████ ",
        "██      ",
        "█████   ",
        "██      ",
        "███████ ",
    ),
    "N": (
        "███   ██",
        "████  ██",
        "██ █  ██",
        "██  █ ██",
        "██   ███",
    ),
    "T": (
        "████████",
        "   ██   ",
        "   ██   ",
        "   ██   ",
        "   ██   ",
    ),
    "O": (
        " ██████ ",
        "██    ██",
        "██    ██",
        "██    ██",
        " ██████ ",
    ),
    "P": (
        "███████ ",
        "██   ██ ",
        "███████ ",
        "██      ",
        "██      ",
    ),
    "S": (
        " ██████ ",
        "██      ",
        " █████  ",
        "     ██ ",
        "██████  ",
    ),
}

# ASCII (figlet "Standard") fallback for terminals without UTF-8 support.
_AGENTOPS_PLAIN_BANNER: tuple[str, ...] = (
    "    _    ____ _____ _   _ _____ ___  ____  ____  ",
    "   / \\  / ___| ____| \\ | |_   _/ _ \\|  _ \\/ ___| ",
    "  / _ \\| |  _|  _| |  \\| | | || | | | |_) \\___ \\ ",
    " / ___ \\ |_| | |___| |\\  | | || |_| |  __/ ___) |",
    "/_/   \\_\\____|_____|_| \\_| |_| \\___/|_|   |____/ ",
)


def _terminal_unicode_enabled() -> bool:
    if os.environ.get("AGENTOPS_NO_UNICODE_BANNER"):
        return False
    if os.environ.get("AGENTOPS_UNICODE_BANNER"):
        return True
    try:
        encoding = (sys.stdout.encoding or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not ("utf" in encoding or "65001" in encoding):
        return False
    # On Windows, sys.stdout.encoding may report utf-8 (e.g. when
    # PYTHONIOENCODING is set) while the underlying console host is
    # still on a legacy OEM code page such as cp1252 or cp850. In that
    # case the bytes for `█` and `━` are decoded as multiple Latin
    # glyphs and the banner renders as garbled mojibake. Detect the
    # real console output code page and only enable the unicode banner
    # when it is set to 65001 (UTF-8).
    if sys.platform == "win32":
        try:
            import ctypes  # noqa: PLC0415

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            if kernel32.GetConsoleOutputCP() != 65001:
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def _terminal_color_enabled() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("AGENTOPS_NO_COLOR"):
        return False
    if (
        os.environ.get("WT_SESSION")
        or os.environ.get("TERM_PROGRAM")
        or os.environ.get("ANSICON")
        or os.environ.get("ConEmuANSI", "").upper() == "ON"
    ):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _useful_pager_available() -> bool:
    """Return True when launching the pager produces a good experience.

    The Windows default pager (``more.com``) paginates one screen at a time
    and prompts the user on every page, which is hostile for long manuals.
    We only opt into the pager when there is a "real" pager available
    (``less``, ``bat``, ...) selected explicitly via ``MANPAGER``/``PAGER``
    environment variables, or when running on a non-Windows OS where the
    default pager is typically ``less``.
    """

    pager = os.environ.get("MANPAGER") or os.environ.get("PAGER")
    if pager:
        executable = pager.strip().split()[0] if pager.strip() else ""
        name = os.path.basename(executable).lower()
        if name in {"more", "more.com", "more.exe", ""}:
            return False
        return True
    if os.name == "nt":
        return False
    return True


def _enable_windows_vt_mode() -> None:
    """Enable Virtual Terminal Processing on Windows stdout if available.

    Modern Windows 10/11 conhost and Windows Terminal interpret ANSI VT
    escapes natively, but the flag is off by default on legacy consoles.
    Without VT mode, raw ANSI bytes would be printed as ``ESC[...m`` text.
    Failures are silently ignored — the helper is best-effort.
    """

    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle in (0, -1, None):
            return
        current = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(current)):
            return
        kernel32.SetConsoleMode(
            handle, current.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
    except Exception:  # noqa: BLE001
        return


def _emit_manual_to_terminal(output: str) -> None:
    """Write a manual page directly to stdout, preserving raw ANSI bytes.

    ``click.echo`` and ``typer.echo`` on Windows wrap stdout with a
    colorama-compatible filter that does not understand 24-bit truecolor
    escapes (``ESC[38;2;R;G;Bm``); the filter splits the sequence and
    leaks the RGB digits as stray SGR codes, painting random background
    blocks onto the banner. Writing the bytes straight to the underlying
    binary buffer bypasses that filter and lets the terminal (Windows
    Terminal / VT-enabled conhost) render the escapes correctly.
    """

    _enable_windows_vt_mode()
    payload = output if output.endswith("\n") else output + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        try:
            sys.stdout.write(payload)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            typer.echo(output, color=True)
        return
    try:
        buffer.write(payload.encode("utf-8", errors="replace"))
        buffer.flush()
    except Exception:  # noqa: BLE001
        typer.echo(output, color=True)


def _read_single_key() -> str:
    """Read one keypress from stdin without requiring Enter.

    Returns the character pressed, or an empty string on extended/unknown
    keys (arrow keys, function keys) and on any I/O error. Used by the
    built-in pager to advance pages on SPACE and quit on ``q``/Esc.
    """

    if os.name == "nt":
        try:
            import msvcrt  # type: ignore[import-not-found]

            ch_win = msvcrt.getch()  # type: ignore[attr-defined]
            if ch_win in (b"\xe0", b"\x00"):
                try:
                    msvcrt.getch()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                return ""
            try:
                return ch_win.decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                return ""
        except Exception:  # noqa: BLE001
            return ""
    try:
        import termios  # type: ignore[import-not-found]
        import tty  # type: ignore[import-not-found]

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        try:
            tty.setraw(fd)  # type: ignore[attr-defined]
            ch_posix = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]
        return ch_posix
    except Exception:  # noqa: BLE001
        return ""


def _emit_manual_with_internal_pager(output: str) -> None:
    """Paginate the manual one screenful at a time, preserving ANSI escapes.

    The Windows default pager (``more.com``) prompts on every line and the
    Click/colorama text wrapper mangles 24-bit truecolor escapes. This
    function rolls a tiny internal pager that:

    * measures the terminal height via :func:`shutil.get_terminal_size`
    * writes ``rows - 2`` lines per page as raw UTF-8 bytes to
      ``sys.stdout.buffer`` (so truecolor survives intact)
    * draws a ``-- More -- (NN%) -- SPACE next / q quit`` status line
    * waits for a single keypress (no Enter required) via
      :func:`_read_single_key`
    * stops on ``q``, ``Q``, ``Esc``, ``Ctrl-C``, or ``Ctrl-D``

    When stdin is not a TTY (CI, piped invocation, test runner) we skip
    pagination entirely and dump the manual via
    :func:`_emit_manual_to_terminal`.
    """

    try:
        stdin_is_tty = bool(sys.stdin.isatty())
    except Exception:  # noqa: BLE001
        stdin_is_tty = False
    if not stdin_is_tty:
        _emit_manual_to_terminal(output)
        return

    import shutil

    columns, rows = shutil.get_terminal_size(fallback=(80, 24))
    page_size = max(rows - 2, 5)
    lines = output.split("\n")
    if len(lines) <= page_size:
        _emit_manual_to_terminal(output)
        return

    _enable_windows_vt_mode()
    buffer = getattr(sys.stdout, "buffer", None)

    def write_raw(text: str) -> None:
        if buffer is not None:
            try:
                buffer.write(text.encode("utf-8", errors="replace"))
                buffer.flush()
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

    total = len(lines)
    idx = 0
    quit_keys = {"q", "Q", "\x03", "\x04", "\x1b"}
    try:
        while idx < total:
            end = min(idx + page_size, total)
            page = "\n".join(lines[idx:end])
            write_raw(page + "\n")
            idx = end
            if idx >= total:
                break
            percent = int(idx * 100 / total)
            prompt_text = (
                f" -- More -- ({percent}%) -- SPACE next page, q to quit "
            )
            write_raw(f"\x1b[7m{prompt_text}\x1b[0m")
            key = _read_single_key()
            write_raw("\r\x1b[2K")
            if key in quit_keys:
                break
    except KeyboardInterrupt:
        write_raw("\r\x1b[2K")
        return


def _mix_palette(
    palette: tuple[tuple[int, int, int], ...],
    position: float,
) -> tuple[int, int, int]:
    """Linear-interpolate a palette stop for a normalised position in [0, 1]."""

    if position <= 0:
        return palette[0]
    if position >= 1:
        return palette[-1]
    palette_position = position * (len(palette) - 1)
    left = int(palette_position)
    right = min(left + 1, len(palette) - 1)
    mix = palette_position - left
    return (
        round(palette[left][0] * (1 - mix) + palette[right][0] * mix),
        round(palette[left][1] * (1 - mix) + palette[right][1] * mix),
        round(palette[left][2] * (1 - mix) + palette[right][2] * mix),
    )


def _gradient_text(text: str, palette: tuple[tuple[int, int, int], ...]) -> str:
    visible_chars = [char for char in text if char != " "]
    if not visible_chars:
        return text
    colored: list[str] = []
    color_index = 0
    denominator = max(1, len(visible_chars) - 1)
    for char in text:
        if char == " ":
            colored.append(char)
            continue
        rgb = _mix_palette(palette, color_index / denominator)
        colored.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{char}")
        color_index += 1
    colored.append(_ANSI_RESET)
    return "".join(colored)


def _gradient_bar(width: int, palette: tuple[tuple[int, int, int], ...]) -> str:
    if not _terminal_color_enabled():
        return "=" * width
    chunks: list[str] = []
    denominator = max(1, width - 1)
    for index in range(width):
        rgb = _mix_palette(palette, index / denominator)
        chunks.append(f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m ")
    chunks.append(_ANSI_RESET)
    return "".join(chunks)


def _gradient_rule(width: int, palette: tuple[tuple[int, int, int], ...]) -> str:
    visible_width = min(width, 56)
    rule_char = "━" if _terminal_unicode_enabled() else "-"
    if not _terminal_color_enabled():
        return _center_text(rule_char * visible_width, width)
    denominator = max(1, visible_width - 1)
    chunks: list[str] = []
    last_color: tuple[int, int, int] | None = None
    for index in range(visible_width):
        rgb = _mix_palette(palette, index / denominator)
        if rgb != last_color:
            chunks.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m")
            last_color = rgb
        chunks.append(rule_char)
    chunks.append(_ANSI_RESET)
    return _center_text("".join(chunks), width)


def _center_text(text: str, width: int) -> str:
    visible = re.sub(r"\x1b\[[0-9;]*m", "", text)
    if len(visible) >= width:
        return text
    left = (width - len(visible)) // 2
    return " " * left + text


def _agentops_block_rows() -> list[str]:
    """Render the 8-letter AGENTOPS block banner as 5 uncolored text rows."""

    word = "AGENTOPS"
    rows = ["", "", "", "", ""]
    for index, letter in enumerate(word):
        glyph = _AGENTOPS_GLYPHS[letter]
        for row_index in range(5):
            rows[row_index] += glyph[row_index]
            if index < len(word) - 1:
                rows[row_index] += " "
    return rows


def _colorize_block(
    rows: list[str],
    palette: tuple[tuple[int, int, int], ...],
) -> list[str]:
    """Apply a horizontal gradient across a multi-line block of glyphs.

    Each non-space column gets a color picked from its X position so every row
    shares the same horizontal sweep, producing a continuous neon wash from
    left to right.
    """

    width = max((len(row) for row in rows), default=0)
    denominator = max(1, width - 1)
    colored: list[str] = []
    for row in rows:
        chunks: list[str] = []
        current_color: tuple[int, int, int] | None = None
        for col, char in enumerate(row):
            if char == " ":
                if current_color is not None:
                    chunks.append(_ANSI_RESET)
                    current_color = None
                chunks.append(char)
                continue
            rgb = _mix_palette(palette, col / denominator)
            if rgb != current_color:
                chunks.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m")
                current_color = rgb
            chunks.append(char)
        if current_color is not None:
            chunks.append(_ANSI_RESET)
        colored.append("".join(chunks))
    return colored


# Single source of truth for the AgentOps brand tagline. Used by the
# explain pages and by the `agentops init` startup banner so the slogan
# never drifts. The unicode variant uses fancy separators; the ASCII
# fallback keeps the same words on a single line.
def _agentops_tagline() -> str:
    if _terminal_unicode_enabled():
        return "Evaluate  ·  Observe  ·  Diagnose  ·  Ship  —  every Foundry agent."
    return "Evaluate :: Observe :: Diagnose :: Ship -- every Foundry agent."


def _render_brand_block(
    *,
    width: int = DOCTOR_EXPLAIN_WRAP_WIDTH,
    eyebrow: str | None = None,
) -> list[str]:
    """Render the AgentOps banner (block art + tagline) as a list of lines.

    Shared by ``agentops init`` (startup banner, no eyebrow) and every
    ``agentops explain`` page (with a centred breadcrumb eyebrow).
    """
    color_on = _terminal_color_enabled()
    unicode_on = _terminal_unicode_enabled()

    if unicode_on:
        block_rows = _agentops_block_rows()
    else:
        block_rows = list(_AGENTOPS_PLAIN_BANNER)

    if color_on:
        block_rows = _colorize_block(block_rows, _AGENTOPS_GRADIENT)

    centered_block = [_center_text(row, width) for row in block_rows]

    tagline = _agentops_tagline()
    if color_on:
        tagline_line = _center_text(
            _gradient_text(tagline, _AGENTOPS_GRADIENT), width
        )
    else:
        tagline_line = _center_text(tagline, width)

    rule_line = _gradient_rule(width, _AGENTOPS_GRADIENT)

    lines: list[str] = [""]
    lines.extend(centered_block)
    lines.append("")
    lines.append(tagline_line)
    lines.append(rule_line)
    if eyebrow:
        lines.append("")
        lines.append(_center_text(eyebrow, width))
    lines.append("")
    return lines


def _manual_banner(title: str, subtitle: str) -> list[str]:  # noqa: ARG001
    """Render the AgentOps explain banner (block art + breadcrumb).

    ``subtitle`` is accepted for backward compatibility but no longer
    rendered: explain pages now derive the one-line summary from the
    NAME section to avoid repeating the same sentence in three places
    (banner subtitle, NAME, and the first paragraph of DESCRIPTION).
    """
    if _terminal_color_enabled():
        eyebrow = (
            style("AGENTOPS EXPLAIN", "bold", "magenta")
            + style(" / detailed command guide", "cyan")
            + style(" / ", "dim")
            + style(title, "bold")
        )
    else:
        eyebrow = f"AGENTOPS EXPLAIN / detailed command guide / {title}"

    return _render_brand_block(eyebrow=eyebrow)


def render_init_banner() -> str:
    """Return the full multi-line startup banner used by ``agentops init``.

    Centered AGENTOPS block art on the brand gradient, followed by the
    one-line tagline and the gradient rule. No breadcrumb eyebrow.
    """
    return "\n".join(_render_brand_block())


def _emit_init_banner() -> None:
    """Write the ``agentops init`` startup banner to the terminal.

    When colors are enabled, the banner is emitted via
    :func:`_emit_manual_to_terminal`, which writes raw UTF-8 bytes to
    ``sys.stdout.buffer`` and enables Windows VT processing. That
    bypasses ``click._winconsole.ConsoleStream`` (the TTY path Click 8
    uses on Windows), which is what mangles the 24-bit gradient into
    the colored-block rendering observed otherwise. When colors are
    disabled (``NO_COLOR``/``AGENTOPS_NO_COLOR``, no TTY, CI capture,
    or :class:`click.testing.CliRunner` in tests) we fall back to
    :func:`typer.echo` so existing assertions on captured output stay
    stable.
    """

    banner = render_init_banner()
    if _terminal_color_enabled():
        _emit_manual_to_terminal(banner)
    else:
        typer.echo(banner)


def _manual_section(lines: list[str], title: str) -> None:
    if lines:
        lines.append("")
    lines.append(style(title, "bold", "cyan"))
    lines.append(style("-" * len(title), "dim"))


def _manual_command_rows(rows: list[tuple[str, str]]) -> list[str]:
    if not rows:
        return []
    label_width = min(max(len(label) for label, _ in rows), 28)
    header = f"  {'COMMAND'.ljust(label_width)}  WHAT IT DOES"
    lines: list[str] = [style(header, "bold"), f"  {'-' * label_width}  {'-' * 42}"]
    for label, description in rows:
        if not description:
            lines.append(f"  {style(label, 'bold', 'cyan')}")
            continue
        first_indent = f"  {label.ljust(label_width)}  "
        next_indent = " " * len(first_indent)
        wrapped = _wrap_hanging(
            description,
            first_indent=first_indent,
            next_indent=next_indent,
        )
        lines.extend(
            style(line[: 2 + label_width], "bold", "cyan") + line[2 + label_width :]
            if line.startswith("  ") and line.strip().startswith(label)
            else line
            for line in wrapped
        )
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return lines


def _manual_item_lines(marker: str, text: str, *, indent: str = "  ") -> list[str]:
    first_indent = f"{indent}{marker}"
    next_indent = " " * len(first_indent)
    return _wrap_hanging(text, first_indent=first_indent, next_indent=next_indent)


def _manual_paragraphs(*paragraphs: str, indent: str = "  ") -> list[str]:
    lines: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if index:
            lines.append("")
        lines.append(_wrap_text(paragraph, indent=indent))
    return lines


def _emit_name_line(lines: list[str], command: str, tagline: str) -> None:
    """Render the NAME line with the same wrap width as DESCRIPTION.

    The styled command and em-dash are injected into the first wrapped
    line after the fact so that ANSI escape sequences don't disturb the
    column measurement performed by :func:`textwrap.wrap`.
    """

    em_dash = "\u2014"
    plain_prefix = f"{command} {em_dash} "
    plain_text = f"{plain_prefix}{tagline}"
    wrapped = wrap(
        plain_text,
        width=DOCTOR_EXPLAIN_WRAP_WIDTH,
        initial_indent="  ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    ) or [f"  {plain_text}"]

    full_plain_prefix = f"  {plain_prefix}"
    styled_prefix = (
        f"  {style(command, 'bold', 'cyan')} {style(em_dash, 'dim')} "
    )
    first = wrapped[0]
    if first.startswith(full_plain_prefix):
        first = styled_prefix + first[len(full_plain_prefix):]
    lines.append(first)
    lines.extend(wrapped[1:])


def _wrap_hanging(
    text: str,
    *,
    first_indent: str,
    next_indent: str,
    width: int = DOCTOR_EXPLAIN_WRAP_WIDTH,
) -> list[str]:
    effective_width = max(40, width - len(first_indent))
    wrapped = wrap(
        text,
        width=effective_width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    return [
        f"{first_indent}{line}" if index == 0 else f"{next_indent}{line}"
        for index, line in enumerate(wrapped)
    ]


def _wrap_text(
    text: str,
    *,
    indent: str,
    width: int = DOCTOR_EXPLAIN_WRAP_WIDTH,
) -> str:
    """Wrap CLI prose with a stable hanging indent."""
    effective_width = max(40, width - len(indent))
    return "\n".join(
        f"{indent}{line}"
        for line in wrap(
            text,
            width=effective_width,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )

def _sources_enabled(config) -> list:
    """Return the list of source names that were enabled in agent.yaml."""
    enabled: list = []
    sources = getattr(config, "sources", None)
    if sources is None:
        return enabled
    for name in ("results_history", "azure_monitor", "foundry_control", "azure_resources"):
        source = getattr(sources, name, None)
        if source is None:
            continue
        if getattr(source, "enabled", True):
            enabled.append(name)
    return enabled


@agent_app.command("serve")
def cmd_agent_serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind host.")
    ] = "0.0.0.0",
    port: Annotated[
        int, typer.Option("--port", help="Bind port.")
    ] = 8080,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Project root for analysis."),
    ] = Path("."),
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to `agent.yaml` (default: `.agentops/agent.yaml`).",
        ),
    ] = None,
    no_verify: Annotated[
        bool,
        typer.Option(
            "--no-verify",
            help="Skip Copilot Extensions signature validation (dev only).",
        ),
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", help="Uvicorn worker count.")
    ] = 1,
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Start the AgentOps doctor as a Copilot Extension HTTP server.

    Exposes ``POST /agents/messages`` (Copilot Extensions protocol),
    ``GET /healthz`` and ``GET /``. Requires the ``[agent]`` extra:

        pip install agentops-toolkit[agent]
    """
    if _maybe_explain_leaf(("agent", "serve"), explain):
        return

    try:
        import uvicorn
    except ImportError as exc:
        typer.echo(
            f"{_cli_error('Error')}: agent extras not installed. "
            "Run `pip install agentops-toolkit[agent]`.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    from agentops.agent.config import load_agent_config
    from agentops.agent.server.app import create_app

    workspace = workspace.resolve()
    resolved_config = _resolve_agent_config_path(workspace, config_path)

    try:
        config = load_agent_config(resolved_config)
    except Exception as exc:
        typer.echo(f"{_cli_error('Error loading agent config')}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    fastapi_app = create_app(
        workspace=workspace,
        config=config,
        verify_signature=not no_verify,
    )

    if no_verify:
        typer.echo(
            f"{_cli_warn('WARNING')}: Copilot Extensions signature validation is disabled. "
            "Use only for local development."
        )

    uvicorn.run(fastapi_app, host=host, port=port, workers=workers)


@app.command("cockpit")
def cmd_cockpit(
    host: Annotated[
        str, typer.Option("--host", help="Bind host (default: 127.0.0.1).")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", help="Bind port (default: 8090).")
    ] = 8090,
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            "-w",
            help="Project root containing `.agentops/agent/history.jsonl`.",
        ),
    ] = Path("."),
    no_preflight: Annotated[
        bool,
        typer.Option(
            "--no-preflight",
            help="Skip the pre-flight connectivity checks.",
        ),
    ] = False,
    explain: Annotated[str | None, typer.Argument(hidden=True)] = None,
) -> None:
    """Open the local AgentOps cockpit."""
    if _maybe_explain_leaf(("cockpit",), explain):
        return

    try:
        import uvicorn
    except ImportError as exc:
        typer.echo(
            f"{_cli_error('Error')}: cockpit requires the [agent] extra. "
            "Run `pip install agentops-toolkit[agent]`.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    import threading
    import time as _time
    import webbrowser

    from agentops.agent.cockpit import create_app as create_cockpit_app
    from agentops.services.preflight import format_report, run_preflight

    workspace = workspace.resolve()

    if not no_preflight:
        report = run_preflight(workspace, scope="cockpit")
        typer.echo(format_report(report), err=True)
        if report.has_failures:
            typer.echo(
                f"{_cli_error('Pre-flight failed')}. Resolve the issues above or re-run "
                "with `--no-preflight` to bypass.",
                err=True,
            )
            raise typer.Exit(code=1)

    fastapi_app = create_cockpit_app(workspace=workspace)
    url = f"http://{host}:{port}"

    # Friendly port-conflict handling. Without this the user gets a raw
    # uvicorn `[Errno 10048] only one usage of each socket address
    # normally permitted` traceback when they accidentally run
    # `agentops cockpit` twice. Probe the port: if an AgentOps
    # cockpit is already serving on it, just open the browser and
    # exit cleanly; otherwise tell the user how to pick a different port.
    if _port_in_use(host, port):
        if _existing_agentops_cockpit(host, port):
            typer.echo(
                f"{_cli_warn('AgentOps cockpit is already running')} on {_cli_path(url)} - "
                "opening browser. Stop the existing cockpit "
                "(Ctrl+C in its terminal) before starting a new one.",
                err=True,
            )
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - best effort
                pass
            raise typer.Exit(code=0)
        typer.echo(
            f"{_cli_error('Port')} {port} is already in use by another process. "
            f"Pick a different port with `agentops cockpit --port <N>`.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"{_cli_heading('AgentOps cockpit')} → {_cli_path(url)}")
    connection_rows: list[tuple[str, str]] = [("workspace", str(workspace))]
    connection_rows.extend(_summarize_cockpit_connection(workspace))
    label_width = max(len(label) for label, _ in connection_rows)
    for label, value in connection_rows:
        padding = " " * (label_width - len(label))
        typer.echo(f"{_cli_label(label)}:{padding} {value}")
    typer.echo(
        f"Run {_cli_command('agentops doctor')} in another terminal to populate doctor findings."
    )
    typer.echo("")
    typer.echo(style("Press Enter (or Ctrl+C) to stop the cockpit.", "dim"))

    # Silence uvicorn's own error logger so the friendly bind-failure
    # message below is not preceded by a red traceback line. The
    # access / info loggers stay at "warning" so legitimate startup
    # warnings still surface.
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "loggers": {
            "uvicorn":        {"level": "CRITICAL", "handlers": []},
            "uvicorn.error":  {"level": "CRITICAL", "handlers": []},
            "uvicorn.access": {"level": "CRITICAL", "handlers": []},
        },
    }
    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="warning",
        log_config=log_config,
    )
    server = uvicorn.Server(config)

    # Carry exceptions from the daemon thread back to the main one so
    # we can render a friendly message instead of letting uvicorn's
    # default error path leak.
    bind_error: list[BaseException] = []

    def _serve() -> None:
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001
            bind_error.append(exc)

    server_thread = threading.Thread(target=_serve, daemon=True)
    server_thread.start()

    # Wait for uvicorn to actually bind before launching the browser so
    # the first GET does not race the server startup.
    for _ in range(40):  # up to ~2s
        if getattr(server, "started", False) or bind_error:
            break
        _time.sleep(0.05)

    if bind_error:
        bind_exc = bind_error[0]
        bind_errno = getattr(bind_exc, "errno", None)
        # WinError 10048 / EADDRINUSE / EACCES on the bind syscall.
        is_port_collision = (
            isinstance(bind_exc, OSError)
            and (
                getattr(bind_exc, "winerror", None) == 10048
                or (isinstance(bind_errno, int) and bind_errno in (48, 98, 13))
            )
        )
        if is_port_collision:
            typer.echo(
                f"{_cli_error('Port')} {port} is busy and the cockpit could not "
                "bind to it. Common causes:\n"
                f"  • a previous `agentops cockpit` is still "
                "holding the socket (Windows TIME_WAIT, lasts up to "
                "~2 min after Ctrl+C)\n"
                "  • another local service is listening on the same "
                "port\n"
                "Fixes (pick one):\n"
                f"  • wait ~2 minutes and re-run\n"
                f"  • pick another port: agentops cockpit --port "
                f"{port + 1}\n"
                f"  • find the holder: PowerShell `Get-NetTCPConnection "
                f"-LocalPort {port}`",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"{_cli_error('Failed to start cockpit')}: {bind_exc}", err=True)
        raise typer.Exit(code=1)

    try:
        webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001 - never fail cockpit on a browser launch issue
        pass

    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass

    typer.echo(_cli_warn("Stopping cockpit…"))
    server.should_exit = True
    server_thread.join(timeout=5)


def main() -> None:
    app()
