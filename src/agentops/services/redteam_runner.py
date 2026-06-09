"""Orchestrate Foundry / PyRIT AI Red Teaming from AgentOps.

This service wraps Foundry's AI Red Teaming agent (built on the open-source
``PyRIT`` toolkit and exposed through
``azure.ai.evaluation.red_team.RedTeam``) so AgentOps can actively *run*
red-team attacks against an agent target instead of only consuming
pre-generated evidence via ``redteam_path``.

The flow is:

1. Read the ``redteam:`` block in ``agentops.yaml``.
2. Resolve the attack target (Azure OpenAI deployment, Foundry agent, or HTTP endpoint).
3. Lazy-import ``azure.ai.evaluation.red_team.RedTeam`` and invoke the scan.
4. Normalize the run's per-category / per-strategy outcomes into a stable
   JSON written to ``.agentops/redteam/latest.json`` by default.
5. Optionally gate the pipeline on a maximum attack-success-rate threshold.

AgentOps does NOT reimplement PyRIT. The orchestration boundary is the
``RedTeam`` Python API; all attack generation, adversarial mutation, and
content-safety judging stay inside the Foundry / PyRIT layer.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

NORMALIZED_RESULT_FILENAME = "latest.json"
DEFAULT_NORMALIZED_DIR = Path(".agentops") / "redteam"


class RedTeamRunnerError(RuntimeError):
    """Raised when the Red Team scan cannot be invoked or parsed."""


@dataclass(frozen=True)
class RedTeamRunResult:
    """Normalized summary of a single AI Red Team scan."""

    target: Dict[str, Any]
    risk_categories: List[str]
    attack_strategies: List[str]
    num_objectives: int
    total_attempts: int = 0
    successful_attacks: int = 0
    attack_success_rate: float = 0.0
    per_category: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_strategy: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    output_path: Optional[str] = None
    raw_summary_path: Optional[str] = None
    has_violations: bool = False
    fail_threshold: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_redteam_installed() -> bool:
    """Return True when ``azure.ai.evaluation.red_team`` is importable.

    The PyRIT-backed Red Team API ships under the ``[redteam]`` extra of
    ``azure-ai-evaluation``. Install with::

        pip install "azure-ai-evaluation[redteam]"
    """

    try:
        import azure.ai.evaluation.red_team  # noqa: F401
    except ImportError:
        return False
    return True


def run_redteam(
    *,
    workspace: Path,
    target: Dict[str, Any],
    risk_categories: List[str],
    attack_strategies: List[str],
    num_objectives: int = 10,
    output_path: Optional[Path] = None,
    azure_ai_project: Optional[Dict[str, Any]] = None,
    credential: Any = None,
    fail_threshold: Optional[float] = None,
) -> RedTeamRunResult:
    """Invoke the Foundry AI Red Teaming agent and normalize the result.

    The function does not raise on attack findings; callers decide whether to
    fail the pipeline based on ``has_violations`` and ``attack_success_rate``.
    It raises :class:`RedTeamRunnerError` when the dependency is missing, the
    target is unresolvable, or the scan cannot produce a parseable summary.
    """

    if not target:
        raise RedTeamRunnerError(
            "Red Team target is empty. Provide redteam.target in agentops.yaml "
            "(e.g. {'model_deployment': 'gpt-4o-mini'})."
        )
    if not risk_categories:
        raise RedTeamRunnerError("Red Team requires at least one risk category.")
    if not is_redteam_installed():
        raise RedTeamRunnerError(
            "The Foundry Red Team SDK is not installed. Install it with "
            "'pip install \"azure-ai-evaluation[redteam]\"' (see "
            "https://learn.microsoft.com/azure/ai-foundry/concepts/ai-red-teaming-agent)."
        )

    resolved_output = (
        output_path
        if output_path is not None
        else workspace / DEFAULT_NORMALIZED_DIR / NORMALIZED_RESULT_FILENAME
    )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    raw_summary_path = resolved_output.parent / "raw_summary.json"

    scan_summary, raw_payload = _invoke_redteam_scan(
        target=target,
        risk_categories=risk_categories,
        attack_strategies=attack_strategies,
        num_objectives=num_objectives,
        azure_ai_project=azure_ai_project,
        credential=credential,
        output_dir=resolved_output.parent,
    )

    if raw_payload is not None:
        try:
            raw_summary_path.write_text(
                json.dumps(raw_payload, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except (OSError, TypeError):
            raw_summary_path = None  # type: ignore[assignment]

    totals = _aggregate_totals(scan_summary)
    per_category = _summarize_by_axis(scan_summary, axis="risk_category")
    per_strategy = _summarize_by_axis(scan_summary, axis="attack_strategy")

    has_violations = (
        fail_threshold is not None
        and totals["attack_success_rate"] > fail_threshold
    )

    result = RedTeamRunResult(
        target=dict(target),
        risk_categories=list(risk_categories),
        attack_strategies=list(attack_strategies),
        num_objectives=num_objectives,
        total_attempts=totals["total"],
        successful_attacks=totals["successful"],
        attack_success_rate=totals["attack_success_rate"],
        per_category=per_category,
        per_strategy=per_strategy,
        output_path=str(resolved_output),
        raw_summary_path=str(raw_summary_path) if raw_summary_path else None,
        has_violations=has_violations,
        fail_threshold=fail_threshold,
    )

    resolved_output.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return result


def _invoke_redteam_scan(
    *,
    target: Dict[str, Any],
    risk_categories: List[str],
    attack_strategies: List[str],
    num_objectives: int,
    azure_ai_project: Optional[Dict[str, Any]],
    credential: Any,
    output_dir: Path,
) -> tuple[List[Dict[str, Any]], Optional[Any]]:
    """Lazy-import and invoke the Foundry Red Team SDK.

    Returns a tuple of ``(scan_summary_records, raw_payload)`` where each
    record in the first list has the shape::

        {
            "risk_category": "violence",
            "attack_strategy": "base64",
            "successful": True | False,
        }

    ``raw_payload`` is the SDK's native return value (best-effort persisted
    for forensics). The SDK is invoked synchronously; if it returns an
    awaitable we run it to completion via :mod:`asyncio`.
    """

    from azure.ai.evaluation.red_team import (  # type: ignore[import-not-found]
        AttackStrategy,
        RedTeam,
        RiskCategory,
    )

    project = azure_ai_project or _project_from_env()
    cred = credential or _default_credential()

    risk_enums = [_coerce_enum(RiskCategory, category) for category in risk_categories]
    strategy_enums = [_coerce_enum(AttackStrategy, strategy) for strategy in attack_strategies]

    scanner = RedTeam(
        azure_ai_project=project,
        credential=cred,
        risk_categories=risk_enums,
        num_objectives=num_objectives,
    )

    callback = _build_target_callback(target)

    try:
        raw_payload = scanner.scan(
            target=callback,
            attack_strategies=strategy_enums,
            output_path=str(output_dir / "raw_redteam_output.json"),
        )
    except TypeError:
        raw_payload = scanner.scan(
            target=callback,
            attack_strategies=strategy_enums,
        )

    raw_payload = _resolve_if_awaitable(raw_payload)
    records = _records_from_payload(raw_payload)
    return records, raw_payload


def _records_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """Best-effort flattening of the SDK payload into per-attempt records."""

    records: List[Dict[str, Any]] = []
    candidates = []
    if isinstance(payload, dict):
        for key in ("attack_details", "attacks", "results", "details"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
    elif isinstance(payload, list):
        candidates = payload

    for item in candidates:
        if not isinstance(item, dict):
            continue
        category = (
            item.get("risk_category")
            or item.get("category")
            or item.get("risk")
        )
        strategy = (
            item.get("attack_strategy")
            or item.get("strategy")
            or item.get("converter")
        )
        successful = item.get("attack_success")
        if successful is None:
            verdict = (item.get("verdict") or item.get("outcome") or "").lower()
            successful = verdict in {"success", "successful", "fail", "failed", "violation"}
        records.append(
            {
                "risk_category": _stringify_enum(category),
                "attack_strategy": _stringify_enum(strategy),
                "successful": bool(successful),
            }
        )
    return records


def _aggregate_totals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    successful = sum(1 for r in records if r.get("successful"))
    asr = (successful / total) if total else 0.0
    return {
        "total": total,
        "successful": successful,
        "attack_success_rate": round(asr, 4),
    }


def _summarize_by_axis(records: List[Dict[str, Any]], *, axis: str) -> Dict[str, Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = {}
    for record in records:
        key = record.get(axis) or "unknown"
        entry = bucket.setdefault(str(key), {"total": 0, "successful": 0, "attack_success_rate": 0.0})
        entry["total"] += 1
        if record.get("successful"):
            entry["successful"] += 1
    for entry in bucket.values():
        total = entry["total"]
        entry["attack_success_rate"] = round((entry["successful"] / total) if total else 0.0, 4)
    return bucket


def _build_target_callback(target: Dict[str, Any]) -> Any:
    """Translate a YAML target descriptor into a callable the SDK can drive."""

    if "model_deployment" in target:
        deployment = target["model_deployment"]
        endpoint = target.get("endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_version = target.get("api_version") or os.environ.get("AZURE_OPENAI_API_VERSION")
        if not endpoint:
            raise RedTeamRunnerError(
                "Red Team target 'model_deployment' requires AZURE_OPENAI_ENDPOINT "
                "(set in .agentops/.env or .azure/<env>/.env) or 'endpoint' in the target."
            )
        return {
            "azure_deployment": deployment,
            "azure_endpoint": endpoint,
            "api_version": api_version,
        }
    if "endpoint" in target:
        return {"endpoint": target["endpoint"], "headers": target.get("headers", {})}
    if "agent" in target:
        return {"agent": target["agent"]}
    raise RedTeamRunnerError(
        "Unsupported Red Team target. Provide one of: model_deployment, agent, endpoint."
    )


def _project_from_env() -> Optional[Dict[str, Any]]:
    endpoint = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return None
    return {"endpoint": endpoint}


def _default_credential() -> Any:
    from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

    return DefaultAzureCredential(process_timeout=30)


def _coerce_enum(enum_cls: Any, value: Any) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        normalized = value.replace("-", "_").upper()
        if hasattr(enum_cls, normalized):
            return getattr(enum_cls, normalized)
        for member in enum_cls:
            if str(getattr(member, "value", "")).lower() == value.lower():
                return member
            if member.name.lower() == value.lower():
                return member
    return value


def _stringify_enum(value: Any) -> str:
    if value is None:
        return "unknown"
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def _resolve_if_awaitable(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RedTeamRunnerError(
                    "Red Team scan returned a coroutine while inside a running "
                    "event loop. Run 'agentops redteam run' from a sync context."
                )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return asyncio.get_event_loop().run_until_complete(value)
    return value
