"""HTTP backend for AgentOps — calls any HTTP-deployed agent endpoint row by row.

Supports agents deployed outside Microsoft Foundry Agent Service, such as
Microsoft Agent Framework applications running on Azure Container Apps (ACA)
or any custom REST endpoint that accepts a JSON payload and returns a response.

The backend:
- Resolves the target URL from config or from an environment variable.
- POSTs each dataset row as JSON. The prompt is sent under ``request_field``
  and all other JSONL row fields (except ``input_field`` and ``expected_field``)
  are forwarded as-is — enabling session_id, user_id, context, tool_definitions,
  or any framework-specific fields to reach the agent without extra configuration.
- Extracts the model response via ``response_field`` (supports dot-path).
- Runs local and AI-assisted evaluators using the same evaluation engine as
  the Foundry local-mode path.
- Produces ``backend_metrics.json`` with per-row scores.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

from agentops.backends.base import BackendExecutionResult, BackendRunContext
from agentops.backends.foundry_backend import (
    _build_foundry_evaluator_runtimes,
    _load_jsonl,
    _normalize_text,
    _resolve_dataset_source_path,
    _run_foundry_evaluator,
    _validate_supported_local_evaluators,
)
from agentops.core.config_loader import load_bundle_config, load_dataset_config

logger = logging.getLogger(__name__)

_DEFAULT_REQUEST_FIELD = "message"
_DEFAULT_RESPONSE_FIELD = "text"


def _to_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_dot_path(payload: Any, dot_path: str) -> Any:
    """Extract a value from a nested dict using a dot-separated path.

    For example, ``"output.text"`` retrieves ``payload["output"]["text"]``.
    Returns the payload directly when dot-path is a single key.
    """
    parts = dot_path.split(".")
    current: Any = payload
    for part in parts:
        if not isinstance(current, dict):
            raise ValueError(
                f"Cannot traverse response path '{dot_path}': "
                f"expected object at '{part}', got {type(current).__name__}"
            )
        if part not in current:
            raise ValueError(
                f"Response field '{part}' not found in HTTP response payload "
                f"(full path: '{dot_path}')"
            )
        current = current[part]
    return current


def _post_json(
    *,
    url: str,
    body: Dict[str, Any],
    extra_headers: Dict[str, str],
    auth_token: Optional[str],
    timeout_seconds: Optional[int],
) -> Dict[str, Any]:
    """POST a JSON body to the given URL and return the parsed response."""
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    headers.update(extra_headers)

    request_body = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url=url, method="POST", data=request_body, headers=headers)

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError(
            f"HTTP agent returned an unexpected response type "
            f"(expected JSON object, got {type(payload).__name__})"
        )
    return payload


class HttpBackend:
    """Evaluation backend that calls an arbitrary HTTP agent endpoint."""

    def _resolve_url(self, context: BackendRunContext) -> str:
        backend = context.backend_config
        url = backend.url
        if url:
            return url.rstrip("/")

        env_name = backend.url_env
        if env_name:
            url = os.getenv(env_name)
            if url:
                return url.rstrip("/")
            raise ValueError(
                f"HTTP backend requires a target URL. "
                f"Set the environment variable '{env_name}' to the agent endpoint URL.\n"
                f"\n"
                f"  PowerShell:\n"
                f'    $env:{env_name} = "https://your-agent.region.azurecontainerapps.io/chat"\n'
                f"\n"
                f"  Bash/zsh:\n"
                f'    export {env_name}="https://your-agent.region.azurecontainerapps.io/chat"'
            )

        raise ValueError(
            "HTTP backend requires 'backend.url' or 'backend.url_env' in your run config."
        )

    def execute(self, context: BackendRunContext) -> BackendExecutionResult:
        context.backend_output_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = context.backend_output_dir / "backend.stdout.log"
        stderr_path = context.backend_output_dir / "backend.stderr.log"
        metrics_path = context.backend_output_dir / "backend_metrics.json"

        backend = context.backend_config
        started = datetime.now(timezone.utc)
        started_perf = perf_counter()

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        exit_code = 0

        try:
            url = self._resolve_url(context)
            request_field = backend.request_field or _DEFAULT_REQUEST_FIELD
            response_field = backend.response_field or _DEFAULT_RESPONSE_FIELD
            timeout_seconds = backend.timeout_seconds
            extra_headers = dict(backend.headers)

            auth_token: Optional[str] = None
            if backend.auth_header_env:
                auth_token = os.getenv(backend.auth_header_env)
                if not auth_token:
                    raise ValueError(
                        f"HTTP backend auth token env var '{backend.auth_header_env}' is set "
                        f"but the variable is empty or unset."
                    )

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

            # AI-assisted evaluators require Azure OpenAI — read from environment.
            fallback_endpoint: Optional[str] = os.getenv("AZURE_OPENAI_ENDPOINT")
            fallback_deployment: Optional[str] = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.getenv(
                "AZURE_OPENAI_DEPLOYMENT"
            )

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

            logger.info("HTTP backend: evaluating %d row(s) against %s", total_rows, url)

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

                # Forward all row fields except input/expected (internal to agentops),
                # then set the prompt under request_field. This transparently passes
                # framework-specific fields such as session_id, user_id, or context.
                _skip = {input_field, expected_field}
                request_body: Dict[str, Any] = {
                    key: value for key, value in row.items() if key not in _skip
                }
                request_body[request_field] = prompt_text

                row_start = perf_counter()
                try:
                    response_payload = _post_json(
                        url=url,
                        body=request_body,
                        extra_headers=extra_headers,
                        auth_token=auth_token,
                        timeout_seconds=timeout_seconds,
                    )
                    raw_response = _extract_dot_path(response_payload, response_field)
                    prediction_text = _normalize_text(raw_response)
                except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
                    stderr_lines.append(f"row={index} error={exc!s}")
                    logger.error("HTTP request failed for row %d: %s", index, exc)
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
                        row_metric_entries.append({"name": runtime.name, "value": score})
                    except Exception as exc:  # noqa: BLE001
                        stderr_lines.append(
                            f"row={index} evaluator={runtime.name} error={exc!s}"
                        )
                        logger.error(
                            "Evaluator '%s' failed for row %d: %s", runtime.name, index, exc
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

                row_metrics_payload.append({"row_index": index, "metrics": row_metric_entries})
                stdout_lines.append(
                    f"row={index} expected={expected_text!r} prediction={prediction_text!r}"
                )

            # Aggregate overall metrics
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
            logger.error("HTTP backend failed: %s", exc)
            exit_code = 1

        finished = datetime.now(timezone.utc)
        duration = perf_counter() - started_perf

        stdout_path.write_text("\n".join(stdout_lines), encoding="utf-8")
        stderr_path.write_text("\n".join(stderr_lines), encoding="utf-8")

        return BackendExecutionResult(
            backend="http",
            command=context.backend_config.url or context.backend_config.url_env or "http",
            started_at=_to_utc_timestamp(started),
            finished_at=_to_utc_timestamp(finished),
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            stdout_file=stdout_path,
            stderr_file=stderr_path,
        )
