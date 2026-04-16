"""Local adapter backend for AgentOps — runs a local agent process per row.

Supports two execution modes:

**Subprocess mode** (``local.adapter``):
    The adapter command is spawned once per dataset row.  Each invocation
    receives a JSON object on **stdin** and must write a JSON object to
    **stdout**.

    Input JSON::

        {"input": "<prompt text>", "expected": "<expected text>", ...extra row fields}

    Expected output JSON::

        {"response": "<agent response text>"}

**Callable mode** (``local.callable``):
    A Python function specified as ``module:function`` is imported and called
    once per dataset row.  The function signature must be::

        def run_evaluation(input: str, context: dict) -> dict:
            ...
            return {"response": "<agent response text>"}

    The ``context`` dict contains all row fields from the dataset.
    The return dict must include a ``"response"`` key.

The backend collects responses and runs the same evaluation engine used
by the Foundry local-mode and HTTP backends to produce
``backend_metrics.json``.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

from agentops.backends.base import BackendExecutionResult, BackendRunContext
from agentops.backends.eval_engine import (
    _build_foundry_evaluator_runtimes,
    _load_jsonl,
    _normalize_text,
    _resolve_dataset_source_path,
    _run_foundry_evaluator,
    _validate_supported_local_evaluators,
)
from agentops.core.config_loader import load_bundle_config, load_dataset_config
from agentops.utils.telemetry import agent_invoke_span, set_agent_invoke_result

logger = logging.getLogger(__name__)


def _load_callable(
    callable_path: str,
) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """Import and return the user function from a ``module:function`` path."""
    module_name, _, func_name = callable_path.partition(":")
    module_name = module_name.strip()
    func_name = func_name.strip()

    # Ensure cwd is importable so that project-local modules work.
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    # Also add .agentops/ to sys.path so callable adapters placed there
    # by ``agentops init`` are importable without manual path hacking.
    agentops_dir = str(Path.cwd() / ".agentops")
    if agentops_dir not in sys.path and Path(agentops_dir).is_dir():
        sys.path.insert(1, agentops_dir)

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"Could not import module '{module_name}' from local.callable '{callable_path}'. "
            f"Make sure the module is importable from your project root ({cwd}) "
            f"or from the .agentops/ directory."
        ) from exc

    func = getattr(module, func_name, None)
    if func is None:
        raise ValueError(
            f"Module '{module_name}' has no function '{func_name}' "
            f"(from local.callable '{callable_path}')"
        )
    if not callable(func):
        raise ValueError(
            f"'{callable_path}' resolved to a non-callable object "
            f"(type: {type(func).__name__})"
        )
    return func


def _to_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LocalAdapterBackend:
    """Evaluation backend that invokes a local adapter per row.

    Supports two modes:
    - **subprocess** (``local.adapter``) — spawns a command per row
    - **callable** (``local.callable``) — imports and calls a Python function per row
    """

    def execute(self, context: BackendRunContext) -> BackendExecutionResult:
        context.backend_output_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = context.backend_output_dir / "backend.stdout.log"
        stderr_path = context.backend_output_dir / "backend.stderr.log"
        metrics_path = context.backend_output_dir / "backend_metrics.json"

        target = context.run_config.target
        execution = context.run_config.execution

        assert target.local is not None
        adapter_command = target.local.adapter
        callable_path = target.local.callable
        timeout_seconds = execution.timeout_seconds

        # Resolve the callable function once if in callable mode.
        user_callable: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
        if callable_path:
            user_callable = _load_callable(callable_path)

        started = datetime.now(timezone.utc)
        started_perf = perf_counter()

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        exit_code = 0

        try:
            bundle_config = load_bundle_config(context.bundle_path)
            dataset_config = load_dataset_config(context.dataset_path)

            dataset_source_path = _resolve_dataset_source_path(
                context.dataset_path, dataset_config.source.path
            )
            rows = _load_jsonl(dataset_source_path)
            total_rows = len(rows)

            enabled_evaluators = [e for e in bundle_config.evaluators if e.enabled]
            _validate_supported_local_evaluators(enabled_evaluators)
            enabled_evaluator_order = [e.name for e in enabled_evaluators]

            fallback_endpoint: Optional[str] = os.getenv("AZURE_OPENAI_ENDPOINT")
            fallback_deployment: Optional[str] = os.getenv(
                "AZURE_AI_MODEL_DEPLOYMENT_NAME"
            ) or os.getenv("AZURE_OPENAI_DEPLOYMENT")

            foundry_evaluator_runtimes = _build_foundry_evaluator_runtimes(
                enabled_evaluators,
                fallback_endpoint=fallback_endpoint,
                fallback_deployment=fallback_deployment,
            )

            input_field = dataset_config.format.input_field
            expected_field = dataset_config.format.expected_field

            enabled_local_names = frozenset(
                e.name for e in enabled_evaluators if e.source == "local"
            )
            evaluator_aggregate_values: Dict[str, List[float]] = {
                name: [] for name in enabled_evaluator_order
            }

            row_metrics_payload: List[Dict[str, Any]] = []

            mode_label = callable_path or adapter_command
            logger.info(
                "Local adapter backend: evaluating %d row(s) via '%s'",
                total_rows,
                mode_label,
            )

            for index, row in enumerate(rows, start=1):
                logger.info("Processing row %d/%d", index, total_rows)

                if input_field not in row:
                    raise ValueError(
                        f"Dataset row {index} missing input field '{input_field}'"
                    )
                if expected_field not in row:
                    raise ValueError(
                        f"Dataset row {index} missing expected field '{expected_field}'"
                    )

                prompt_text = _normalize_text(row[input_field])
                expected_text = _normalize_text(row[expected_field])

                row_start = perf_counter()

                if user_callable is not None:
                    # --- Callable mode ---
                    try:
                        with agent_invoke_span(
                            target=context.run_config.target.type,
                            provider="local.callable",
                        ) as invoke_span:
                            context_dict = dict(row)
                            result = user_callable(prompt_text, context_dict)
                            if not isinstance(result, dict):
                                raise TypeError(
                                    f"Callable must return a dict, got {type(result).__name__}"
                                )
                            if "response" not in result:
                                raise ValueError(
                                    "Callable return dict must include a 'response' key"
                                )
                            prediction_text = _normalize_text(
                                result.get("response", "")
                            )
                            set_agent_invoke_result(invoke_span)
                    except Exception as exc:  # noqa: BLE001
                        stderr_lines.append(f"row={index} error={exc!s}")
                        logger.error("Callable failed for row %d: %s", index, exc)
                        exit_code = 1
                        continue
                else:
                    # --- Subprocess mode ---
                    adapter_input = json.dumps(
                        {"input": prompt_text, "expected": expected_text, **row}
                    )

                    try:
                        with agent_invoke_span(
                            target=context.run_config.target.type,
                            provider="local.subprocess",
                        ) as invoke_span:
                            completed = subprocess.run(
                                shlex.split(
                                    adapter_command, posix=(sys.platform != "win32")
                                ),
                                input=adapter_input,
                                capture_output=True,
                                text=True,
                                timeout=timeout_seconds,
                                check=False,
                            )
                            if completed.returncode != 0:
                                stderr_lines.append(
                                    f"row={index} adapter exit_code={completed.returncode} "
                                    f"stderr={completed.stderr.strip()}"
                                )
                                logger.error(
                                    "Adapter failed for row %d (exit %d): %s",
                                    index,
                                    completed.returncode,
                                    completed.stderr.strip(),
                                )
                                exit_code = 1
                                continue

                            adapter_output = json.loads(completed.stdout)
                            prediction_text = _normalize_text(
                                adapter_output.get("response", "")
                            )
                            set_agent_invoke_result(invoke_span)
                    except subprocess.TimeoutExpired:
                        stderr_lines.append(f"row={index} error=adapter timeout")
                        logger.error("Adapter timed out for row %d", index)
                        exit_code = 1
                        continue
                    except (json.JSONDecodeError, ValueError) as exc:
                        stderr_lines.append(f"row={index} error={exc!s}")
                        logger.error(
                            "Adapter returned invalid JSON for row %d: %s", index, exc
                        )
                        exit_code = 1
                        continue

                row_latency = perf_counter() - row_start

                row_metric_entries: List[Dict[str, Any]] = []

                for runtime in foundry_evaluator_runtimes:
                    try:
                        score = _run_foundry_evaluator(
                            runtime,
                            prompt=prompt_text,
                            prediction=prediction_text,
                            expected=expected_text,
                            row=row,
                        )
                        row_metric_entries.append(
                            {"name": runtime.name, "value": score}
                        )
                    except Exception as exc:  # noqa: BLE001
                        stderr_lines.append(
                            f"row={index} evaluator={runtime.name} error={exc!s}"
                        )
                        logger.error(
                            "Evaluator '%s' failed for row %d: %s",
                            runtime.name,
                            index,
                            exc,
                        )

                if "exact_match" in enabled_local_names:
                    passed = prediction_text.lower() == expected_text.lower()
                    row_metric_entries.append(
                        {"name": "exact_match", "value": 1.0 if passed else 0.0}
                    )
                if "latency_seconds" in enabled_local_names:
                    row_metric_entries.append(
                        {"name": "latency_seconds", "value": row_latency}
                    )
                if "avg_latency_seconds" in enabled_local_names:
                    row_metric_entries.append(
                        {"name": "avg_latency_seconds", "value": row_latency}
                    )

                for entry in row_metric_entries:
                    name = entry["name"]
                    if name in evaluator_aggregate_values:
                        evaluator_aggregate_values[name].append(entry["value"])

                row_metrics_payload.append(
                    {
                        "row_index": index,
                        "input": prompt_text,
                        "response": prediction_text,
                        "context": row.get("context"),
                        "metrics": row_metric_entries,
                    }
                )
                stdout_lines.append(
                    f"row={index} expected={expected_text!r} prediction={prediction_text!r}"
                )

            aggregate_metrics: List[Dict[str, Any]] = []
            for name, values in evaluator_aggregate_values.items():
                if values:
                    aggregate_metrics.append(
                        {"name": name, "value": sum(values) / len(values)}
                    )

            metrics_path.write_text(
                json.dumps(
                    {"metrics": aggregate_metrics, "row_metrics": row_metrics_payload},
                    indent=2,
                ),
                encoding="utf-8",
            )

        except Exception as exc:  # noqa: BLE001
            stderr_lines.append(str(exc))
            logger.error("Local adapter backend failed: %s", exc)
            exit_code = 1

        finished = datetime.now(timezone.utc)
        duration = perf_counter() - started_perf

        stdout_path.write_text("\n".join(stdout_lines), encoding="utf-8")
        stderr_path.write_text("\n".join(stderr_lines), encoding="utf-8")

        return BackendExecutionResult(
            backend="local_adapter",
            command=callable_path or adapter_command or "local_adapter",
            started_at=_to_utc_timestamp(started),
            finished_at=_to_utc_timestamp(finished),
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            stdout_file=stdout_path,
            stderr_file=stderr_path,
        )
