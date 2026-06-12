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
    azure_ai_project: Optional[Any] = None,
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
    azure_ai_project: Optional[Any],
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

    project = azure_ai_project if azure_ai_project is not None else _project_from_env()
    if project is None:
        raise RedTeamRunnerError(
            "Azure AI project metadata is required. Set "
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT for new (hub-less) Foundry "
            "projects, or AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP + "
            "AZURE_AI_PROJECT_NAME for hub-based projects. AgentOps reads "
            "these from the active .azure/<env>/.env or .agentops/.env."
        )
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

    # The SDK return value shape varies across azure-ai-evaluation versions
    # (older releases returned a dict with ``attack_details``; current
    # releases return a ``RedTeamResult`` object whose attributes are not
    # JSON-serializable). The on-disk ``results.json`` is the stable
    # contract — fall back to it when the in-memory payload did not yield
    # any records, and replace ``raw_payload`` so ``raw_summary.json``
    # captures the actual scan data instead of a useless ``repr()`` string.
    if not records:
        disk_payload = _load_results_from_output_dir(output_dir)
        if disk_payload is not None:
            disk_records = _records_from_payload(disk_payload)
            if disk_records:
                records = disk_records
                raw_payload = disk_payload

    return records, raw_payload


def _load_results_from_output_dir(output_dir: Path) -> Optional[Any]:
    """Locate and parse the SDK's on-disk ``results.json``.

    The Red Team SDK writes the canonical OpenAI Evals-shaped result to a
    file (or directory of files) at the path supplied via
    ``scanner.scan(output_path=...)``. Recent SDK versions create a
    directory containing ``results.json`` plus ``evaluation_results.jsonl``;
    older versions wrote a single JSON file directly. Handle both shapes.
    """

    base = output_dir / "raw_redteam_output.json"
    candidates = [
        base / "results.json",
        base,
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _records_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """Best-effort flattening of the SDK payload into per-attempt records.

    Supports three shapes:

    * ``RedTeamResult``-like objects — unwrapped via ``scan_result`` /
      ``to_dict()`` / ``result`` attributes.
    * OpenAI Evals-shaped payloads with
      ``output_items.data[*].results.properties.attack_success``.
    * Legacy ``attack_details`` / ``attacks`` / ``details`` lists.
    """

    # Unwrap ``RedTeamResult``-like objects to their dict representation
    # before pattern-matching against the known shapes below.
    if payload is not None and not isinstance(payload, (dict, list)):
        for attr in ("scan_result", "to_dict", "result"):
            value = getattr(payload, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception:  # noqa: BLE001 — best-effort extraction.
                    value = None
            if isinstance(value, (dict, list)):
                payload = value
                break

    records: List[Dict[str, Any]] = []

    # OpenAI Evals shape: output_items.data[*].results.properties.attack_success
    if isinstance(payload, dict):
        output_items = payload.get("output_items")
        if isinstance(output_items, dict):
            data = output_items.get("data")
            if isinstance(data, list):
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    result = entry.get("results")
                    if not isinstance(result, dict):
                        continue
                    props = result.get("properties")
                    if not isinstance(props, dict):
                        props = {}
                    category = result.get("name") or result.get("metric")
                    strategy = (
                        props.get("attack_technique")
                        or props.get("attack_strategy")
                    )
                    successful = props.get("attack_success")
                    if successful is None:
                        label = str(result.get("label") or "").lower()
                        passed = result.get("passed")
                        if label in {"fail", "failed", "violation"}:
                            successful = True
                        elif passed is False:
                            successful = True
                        else:
                            successful = False
                    records.append(
                        {
                            "risk_category": _stringify_enum(category),
                            "attack_strategy": _stringify_enum(strategy),
                            "successful": bool(successful),
                        }
                    )
                if records:
                    return records

    # Legacy shape: dict carrying an ``attack_details`` / ``attacks`` /
    # ``details`` list, or a bare list of per-attempt dicts.
    candidates: List[Any] = []
    if isinstance(payload, dict):
        for key in ("attack_details", "attacks", "details"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        # ``results`` is also a list in the legacy shape but conflicts with
        # the OpenAI Evals-shaped ``output_items`` flow above; only use it
        # when the SDK did not emit ``output_items``.
        if not candidates and "output_items" not in payload:
            value = payload.get("results")
            if isinstance(value, list):
                candidates = value
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


def _project_from_env() -> Optional[Any]:
    """Build the azure_ai_project descriptor the Red Team SDK expects.

    The SDK supports two project shapes:

    * Hub-less / "OneDP" Foundry projects (the default for new accounts):
      detected by ``isinstance(project, str)``. We pass the bare endpoint
      URL (``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT``) as a string and the SDK
      skips AML workspace discovery, which would otherwise 404 because the
      account has no AML workspace.

    * Hub-based AI Foundry projects (legacy): require the
      subscription_id / resource_group_name / project_name triplet.

    We prefer the string form whenever the OneDP-style endpoint is set,
    and fall back to the triplet for hub-based projects.
    """

    endpoint = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if endpoint and "/api/projects/" in endpoint:
        return endpoint.rstrip("/")

    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group = (
        os.environ.get("AZURE_RESOURCE_GROUP")
        or os.environ.get("AZURE_RESOURCE_GROUP_NAME")
    )
    project_name = (
        os.environ.get("AZURE_AI_PROJECT_NAME")
        or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_NAME")
    )

    if not project_name and "/projects/" in endpoint:
        project_name = endpoint.rsplit("/projects/", 1)[-1].split("/", 1)[0] or None

    if subscription and resource_group and project_name:
        return {
            "subscription_id": subscription,
            "resource_group_name": resource_group,
            "project_name": project_name,
        }
    return None


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
