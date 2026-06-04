"""Prepare Microsoft Foundry AI Agent Evaluation inputs from AgentOps config."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agentops.core.agentops_config import AgentOpsConfig, classify_agent
from agentops.core.config_loader import load_agentops_config
from agentops.core.evaluators import EvaluatorPreset, detect_dataset_shape, select_evaluators


OFFICIAL_EVAL_RUNNER = "official-ai-agent-evaluation"
OFFICIAL_EVAL_ACTION = "microsoft/ai-agent-evals@v3-beta"
OFFICIAL_EVAL_ADO_TASK = "AIAgentEvaluation@2"
OFFICIAL_EVAL_ACTION_ENV = "AGENTOPS_OFFICIAL_EVAL_ACTION"
OFFICIAL_EVAL_ADO_TASK_ENV = "AGENTOPS_OFFICIAL_EVAL_ADO_TASK"
AZD_EVAL_RUNNER = "azd-ai-agent-eval"
AZD_AI_AGENTS_EXTENSION_VERSION = "1.0.0"
AZD_AI_AGENTS_EXTENSION_VERSION_ENV = "AGENTOPS_AZD_AI_AGENTS_EXTENSION_VERSION"
AGENTOPS_LOCAL_RUNNER = "agentops-local"
AGENTOPS_CLOUD_RUNNER = "agentops-cloud"

_LATENCY_PRESET = "avg_latency_seconds"
_OFFICIAL_EVALUATORS: dict[str, str] = {
    "CoherenceEvaluator": "builtin.coherence",
    "FluencyEvaluator": "builtin.fluency",
    "SimilarityEvaluator": "builtin.text_similarity",
    "F1ScoreEvaluator": "builtin.f1_score",
    "GroundednessEvaluator": "builtin.groundedness",
    "RelevanceEvaluator": "builtin.relevance",
    "RetrievalEvaluator": "builtin.retrieval",
    "ResponseCompletenessEvaluator": "builtin.response_completeness",
    "ToolCallAccuracyEvaluator": "builtin.tool_call_accuracy",
    "IntentResolutionEvaluator": "builtin.intent_resolution",
    "TaskAdherenceEvaluator": "builtin.task_adherence",
}
_GROUND_TRUTH_EVALUATORS = {
    "builtin.text_similarity",
    "builtin.f1_score",
    "builtin.response_completeness",
}


def official_eval_action_ref() -> str:
    """Return the GitHub Action ref used for Microsoft Foundry eval workflows."""

    return os.getenv(OFFICIAL_EVAL_ACTION_ENV, OFFICIAL_EVAL_ACTION)


def official_eval_ado_task_ref() -> str:
    """Return the Azure DevOps task ref used for Microsoft Foundry eval workflows."""

    return os.getenv(OFFICIAL_EVAL_ADO_TASK_ENV, OFFICIAL_EVAL_ADO_TASK)


def azd_ai_agents_extension_version() -> str:
    """Return the pinned azd AI agents extension version for generated CI."""

    return os.getenv(
        AZD_AI_AGENTS_EXTENSION_VERSION_ENV,
        AZD_AI_AGENTS_EXTENSION_VERSION,
    )


class OfficialEvalUnsupported(ValueError):
    """Raised when an AgentOps config cannot use Microsoft Foundry AI Agent Evaluation."""


@dataclass(frozen=True)
class OfficialEvalSupport:
    """Eligibility result for the Microsoft Foundry evaluation runner."""

    eligible: bool
    runner: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    official_evaluators: tuple[str, ...]
    agent_ids: str | None
    data_path: Path | None


@dataclass(frozen=True)
class OfficialEvalPreparation:
    """Prepared Microsoft Foundry evaluation input and metadata."""

    data_path: Path
    metadata_path: Path
    agent_ids: str
    deployment_name: str
    official_evaluators: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _EvalPlan:
    config: AgentOpsConfig
    config_path: Path
    dataset_path: Path
    agent_ids: str
    official_evaluators: tuple[str, ...]
    skipped_agentops_evaluators: tuple[str, ...]
    warnings: tuple[str, ...]


def analyze_official_eval_support(config_path: Path) -> OfficialEvalSupport:
    """Report whether ``config_path`` can use the Microsoft Foundry eval runner."""

    try:
        plan = _build_plan(config_path)
    except OfficialEvalUnsupported as exc:
        return OfficialEvalSupport(
            eligible=False,
            runner=AGENTOPS_LOCAL_RUNNER,
            reasons=(str(exc),),
            warnings=(),
            official_evaluators=(),
            agent_ids=None,
            data_path=None,
        )
    except Exception as exc:
        return OfficialEvalSupport(
            eligible=False,
            runner=AGENTOPS_LOCAL_RUNNER,
            reasons=(f"agentops.yaml cannot be prepared for Microsoft Foundry eval: {exc}",),
            warnings=(),
            official_evaluators=(),
            agent_ids=None,
            data_path=None,
        )

    return OfficialEvalSupport(
        eligible=True,
        runner=OFFICIAL_EVAL_RUNNER,
        reasons=(
            "Agent target is a Foundry prompt agent (`name:version`).",
            "Dataset columns are compatible with Microsoft Foundry eval.",
        ),
        warnings=plan.warnings,
        official_evaluators=plan.official_evaluators,
        agent_ids=plan.agent_ids,
        data_path=plan.dataset_path,
    )


def recommended_eval_runner(directory: Path) -> str:
    """Return the safest evaluation runner for generated CI/CD workflows."""

    try:
        from agentops.core.azd_eval import find_eval_yaml

        if find_eval_yaml(directory) is not None:
            return AZD_EVAL_RUNNER
    except Exception:
        pass

    support = analyze_official_eval_support(directory / "agentops.yaml")
    return AGENTOPS_CLOUD_RUNNER if support.eligible else AGENTOPS_LOCAL_RUNNER


def prepare_official_eval(
    config_path: Path,
    output_path: Path,
    *,
    deployment_name: str | None = None,
) -> OfficialEvalPreparation:
    """Convert AgentOps JSONL config into Microsoft Foundry AI Agent Evaluation JSON."""

    plan = _build_plan(config_path)
    deployment = _resolve_deployment_name(deployment_name)
    payload = _build_payload(plan)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    metadata_path = output_path.with_name("metadata.json")
    metadata = {
        "runner": OFFICIAL_EVAL_RUNNER,
        "action": official_eval_action_ref(),
        "azure_devops_task": official_eval_ado_task_ref(),
        "agent_ids": plan.agent_ids,
        "deployment_name": deployment,
        "data_path": str(output_path),
        "items_total": len(payload.get("data", [])),
        "official_evaluators": list(plan.official_evaluators),
        "skipped_agentops_evaluators": list(plan.skipped_agentops_evaluators),
        "machine_readable_thresholds": False,
        "warnings": list(plan.warnings),
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return OfficialEvalPreparation(
        data_path=output_path,
        metadata_path=metadata_path,
        agent_ids=plan.agent_ids,
        deployment_name=deployment,
        official_evaluators=plan.official_evaluators,
        warnings=plan.warnings,
    )


def _build_plan(config_path: Path) -> _EvalPlan:
    config_path = config_path.resolve()
    if not config_path.exists():
        raise OfficialEvalUnsupported(
            "agentops.yaml was not found; use AgentOps local eval until a release "
            "config exists."
        )

    config = load_agentops_config(config_path)
    target = classify_agent(config.agent, config.protocol)
    if target.kind != "foundry_prompt":
        raise OfficialEvalUnsupported(
            "Microsoft Foundry AI Agent Evaluation only evaluates Foundry prompt agents "
            "(`agent: name:version`); use AgentOps local eval for hosted endpoints, "
            "HTTP agents, and model targets."
        )

    dataset_path = _resolve_dataset_path(config_path, config.dataset)
    shape = detect_dataset_shape(dataset_path)
    overrides = [item.name for item in config.evaluators or []] or None
    presets = select_evaluators(
        target,
        shape,
        overrides=overrides,
        threshold_metrics=config.thresholds.keys(),
    )
    official_evaluators, skipped, warnings = _map_evaluators(presets)
    if not official_evaluators:
        raise OfficialEvalUnsupported(
            "no AgentOps evaluators could be mapped to Microsoft Foundry evaluators."
        )

    _validate_dataset_for_official_runner(dataset_path, official_evaluators)

    return _EvalPlan(
        config=config,
        config_path=config_path,
        dataset_path=dataset_path,
        agent_ids=config.agent,
        official_evaluators=tuple(official_evaluators),
        skipped_agentops_evaluators=tuple(skipped),
        warnings=tuple(warnings),
    )


def _map_evaluators(
    presets: Sequence[EvaluatorPreset],
) -> tuple[list[str], list[str], list[str]]:
    official: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for preset in presets:
        if preset.name == _LATENCY_PRESET:
            skipped.append(preset.name)
            continue

        official_name = _OFFICIAL_EVALUATORS.get(preset.name)
        if official_name is None:
            raise OfficialEvalUnsupported(
                f"AgentOps evaluator {preset.name!r} has no official Foundry "
                "mapping yet."
            )
        if official_name not in official:
            official.append(official_name)

    return official, skipped, warnings


def _build_payload(plan: _EvalPlan) -> dict[str, Any]:
    rows = list(_convert_rows(plan.dataset_path))
    payload: dict[str, Any] = {
        "name": _dataset_name(plan.config_path, plan.dataset_path),
        "evaluators": list(plan.official_evaluators),
        "data": rows,
    }
    if "builtin.text_similarity" in plan.official_evaluators:
        payload["openai_graders"] = {
            "builtin.text_similarity": {
                "evaluation_metric": "fuzzy_match",
                "input": "{{sample.output_text}}",
                "reference": "{{item.ground_truth}}",
            }
        }
    return payload


def _convert_rows(dataset_path: Path) -> Iterable[dict[str, Any]]:
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{dataset_path}: invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"{dataset_path}: line {line_number} is not a JSON object")

            query = row.get("query", row.get("input"))
            if not isinstance(query, str) or not query.strip():
                raise ValueError(
                    f"{dataset_path}: line {line_number} must include a non-empty "
                    "`input` or `query` string."
                )

            converted = dict(row)
            converted["query"] = query
            if "ground_truth" not in converted and row.get("expected") is not None:
                converted["ground_truth"] = row["expected"]
            yield converted


def _validate_dataset_for_official_runner(
    dataset_path: Path,
    official_evaluators: Sequence[str],
) -> None:
    needs_ground_truth = any(
        evaluator in _GROUND_TRUTH_EVALUATORS for evaluator in official_evaluators
    )

    for line_number, row in enumerate(_convert_rows(dataset_path), start=1):
        if needs_ground_truth and not row.get("ground_truth"):
            raise OfficialEvalUnsupported(
                f"{dataset_path}: line {line_number} needs `expected` or "
                "`ground_truth` for the selected Microsoft Foundry evaluators."
            )


def _resolve_dataset_path(config_path: Path, dataset: Path) -> Path:
    dataset_path = dataset
    if not dataset_path.is_absolute():
        dataset_path = config_path.parent / dataset_path
    return dataset_path.resolve()


def _resolve_deployment_name(value: str | None) -> str:
    deployment = (
        value
        or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or ""
    ).strip()
    if not deployment:
        raise ValueError(
            "deployment name is required. Pass --deployment-name or set "
            "AZURE_OPENAI_DEPLOYMENT."
        )
    return deployment


def _dataset_name(config_path: Path, dataset_path: Path) -> str:
    parts = [
        config_path.parent.name or "workspace",
        dataset_path.stem or "dataset",
    ]
    raw = "-".join(parts)
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw)
    return "-".join(part for part in safe.split("-") if part) or "agentops-eval"


def _append_github_output(path: Path, outputs: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def _emit_ado_output(outputs: Mapping[str, str]) -> None:
    variables = {
        "data_path": "officialDataPath",
        "agent_ids": "officialAgentIds",
        "deployment_name": "officialDeploymentName",
        "metadata_path": "officialMetadataPath",
    }
    for key, variable_name in variables.items():
        print(f"##vso[task.setvariable variable={variable_name};isOutput=true]{outputs[key]}")


def _command_prepare(args: argparse.Namespace) -> int:
    prepared = prepare_official_eval(
        Path(args.config),
        Path(args.out),
        deployment_name=args.deployment_name,
    )
    outputs = {
        "data_path": str(prepared.data_path),
        "agent_ids": prepared.agent_ids,
        "deployment_name": prepared.deployment_name,
        "metadata_path": str(prepared.metadata_path),
    }
    if args.github_output:
        _append_github_output(Path(args.github_output), outputs)
    if args.ado_output:
        _emit_ado_output(outputs)
    if args.print_json:
        print(json.dumps(outputs, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare official Microsoft Foundry AI Agent Evaluation inputs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--config", default="agentops.yaml")
    prepare_parser.add_argument("--out", required=True)
    prepare_parser.add_argument("--deployment-name")
    prepare_parser.add_argument("--github-output")
    prepare_parser.add_argument("--ado-output", action="store_true")
    prepare_parser.add_argument("--print-json", action="store_true")
    prepare_parser.set_defaults(func=_command_prepare)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
